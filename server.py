from __future__ import annotations

import html
import json
import os
import re
import ssl
import threading
import time
from datetime import date, datetime
from html.parser import HTMLParser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin, urlparse
from urllib.request import HTTPSHandler, HTTPRedirectHandler, Request, build_opener, urlopen
from urllib.robotparser import RobotFileParser
from zoneinfo import ZoneInfo


HOST = "127.0.0.1"
PORT = 8000
USER_AGENT = "SCU-CampusToday/1.0 (+https://github.com/dkao0625/OMIS107-HW4)"
TIMEZONE = ZoneInfo("America/Los_Angeles")
CACHE_SECONDS = 10 * 60
MIN_REQUEST_INTERVAL = 30
ROBOTS_CACHE_SECONDS = 24 * 60 * 60
MAX_PAGE_BYTES = 2_000_000

SOURCES = {
    "events": {
        "name": "SCU Events Calendar",
        "url": "https://events.scu.edu/",
    },
    "library": {
        "name": "SCU Library Hours",
        "url": "https://scu.libcal.com/hours/",
    },
    "dining": {
        "name": "SCU Dining",
        "url": "https://scu.campusdish.com/en/locationsandmenus",
    },
}

lock = threading.Lock()
page_cache: dict[str, tuple[float, bytes, str]] = {}
robots_cache: dict[str, tuple[float, RobotFileParser]] = {}
last_request: dict[str, float] = {}
dashboard_cache: tuple[float, dict] | None = None
dashboard_refresh_lock = threading.Lock()

try:
    import certifi
except ImportError:
    certifi = None

if certifi is not None:
    certificate_context = ssl.create_default_context(cafile=certifi.where())
else:
    default_bundle = ssl.get_default_verify_paths().cafile
    system_bundle = next(
        (
            path
            for path in (default_bundle, "/etc/ssl/cert.pem", "/etc/ssl/certs/ca-certificates.crt")
            if path and os.path.isfile(path)
        ),
        None,
    )
    certificate_context = ssl.create_default_context(cafile=system_bundle)


class SafeRedirectHandler(HTTPRedirectHandler):
    def redirect_request(self, request, response, code, message, headers, new_url):
        target = urljoin(request.full_url, new_url)
        robots = get_robots(target)
        if not robots.can_fetch(USER_AGENT, target):
            raise URLError(f"Redirect target disallowed by robots.txt: {urlparse(target).netloc}")

        now = time.monotonic()
        with lock:
            previous_request = last_request.get(target)
            delay = MIN_REQUEST_INTERVAL - (now - previous_request) if previous_request else 0
        if delay > 0:
            time.sleep(delay)
        with lock:
            last_request[target] = time.monotonic()
        return super().redirect_request(request, response, code, message, headers, target)


source_opener = build_opener(SafeRedirectHandler(), HTTPSHandler(context=certificate_context))


def get_robots(url: str) -> RobotFileParser:
    parsed = urlparse(url)
    robots_url = f"{parsed.scheme}://{parsed.netloc}/robots.txt"
    now = time.monotonic()
    with lock:
        cached = robots_cache.get(robots_url)
        if cached and now - cached[0] < ROBOTS_CACHE_SECONDS:
            return cached[1]

    parser = RobotFileParser()
    parser.set_url(robots_url)
    request = Request(robots_url, headers={"User-Agent": USER_AGENT})
    try:
        with urlopen(request, timeout=12, context=certificate_context) as response:
            content = response.read(256_000).decode("utf-8", errors="replace")
        parser.parse(content.splitlines())
    except HTTPError as error:
        if error.code not in (404, 410):
            raise RuntimeError(f"Could not read robots.txt for {parsed.netloc}: HTTP {error.code}") from error
        parser.parse([])
    except (URLError, TimeoutError) as error:
        raise RuntimeError(f"Could not read robots.txt for {parsed.netloc}: {error}") from error

    with lock:
        robots_cache[robots_url] = (time.monotonic(), parser)
    return parser


def fetch_page(url: str) -> tuple[bytes, str]:
    parser = get_robots(url)
    if not parser.can_fetch(USER_AGENT, url):
        raise RuntimeError("This source disallows this page in robots.txt.")

    now = time.monotonic()
    with lock:
        cached = page_cache.get(url)
        if cached and now - cached[0] < CACHE_SECONDS:
            return cached[1], cached[2]
        previous_request = last_request.get(url)
        if previous_request and now - previous_request < MIN_REQUEST_INTERVAL:
            raise RuntimeError("This source is cooling down; try again in a few minutes.")
        last_request[url] = now

    request = Request(
        url,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "text/html,application/xhtml+xml",
        },
    )
    try:
        with source_opener.open(request, timeout=18) as response:
            content_type = response.headers.get_content_type()
            if content_type not in ("text/html", "application/xhtml+xml"):
                raise RuntimeError(f"Expected an HTML page, got {content_type}.")
            body = response.read(MAX_PAGE_BYTES + 1)
            if len(body) > MAX_PAGE_BYTES:
                raise RuntimeError("The source page is larger than the 2 MB limit.")
            charset = response.headers.get_content_charset() or "utf-8"
    except HTTPError as error:
        raise RuntimeError(f"Source returned HTTP {error.code}.") from error
    except (URLError, TimeoutError) as error:
        raise RuntimeError(f"Could not fetch source: {error}") from error

    with lock:
        page_cache[url] = (time.monotonic(), body, charset)
    return body, charset


class PageParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.text_parts: list[str] = []
        self.json_ld: list[str] = []
        self.scripts: list[str] = []
        self._in_script = False
        self._script_type: str | None = None
        self._script_parts: list[str] = []
        self._containers: list[dict] = []
        self._depth = 0
        self.cards: list[dict] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = dict(attrs)
        classes = set((attributes.get("class") or "").lower().split())
        is_container = tag == "article" or bool(
            classes & {"event", "event-item", "event-card", "calendar-event", "lw_event"}
        )
        self._depth += 1
        if is_container:
            self._containers.append(
                {"depth": self._depth, "texts": [], "links": [], "times": []}
            )
        if tag == "script":
            self._in_script = True
            self._script_type = (attributes.get("type") or "").lower()
            self._script_parts = []
        if tag == "a" and attributes.get("href"):
            for container in self._containers:
                container["links"].append(attributes["href"])
        if tag == "time":
            value = attributes.get("datetime")
            if value:
                for container in self._containers:
                    container["times"].append(value)

    def handle_endtag(self, tag: str) -> None:
        if tag == "script" and self._in_script:
            script = "".join(self._script_parts)
            self.scripts.append(script)
            if self._script_type == "application/ld+json":
                self.json_ld.append(script)
            self._in_script = False
            self._script_type = None
            self._script_parts = []
        if self._containers and self._containers[-1]["depth"] == self._depth:
            card = self._containers.pop()
            card["text"] = " ".join(" ".join(card["texts"]).split())
            self.cards.append(card)
        self._depth = max(0, self._depth - 1)

    def handle_data(self, data: str) -> None:
        if self._in_script:
            self._script_parts.append(data)
            return
        cleaned = " ".join(data.split())
        if cleaned:
            self.text_parts.append(cleaned)
            for container in self._containers:
                container["texts"].append(cleaned)


def load_page(url: str) -> PageParser:
    body, charset = fetch_page(url)
    parser = PageParser()
    parser.feed(body.decode(charset, errors="replace"))
    return parser


def parse_datetime(value: str) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=TIMEZONE)
        return parsed.astimezone(TIMEZONE)
    except ValueError:
        return None


def format_time(value: datetime) -> str:
    return value.strftime("%-I:%M %p").replace(":00", "")


def event_objects(value):
    if isinstance(value, list):
        for item in value:
            yield from event_objects(item)
    elif isinstance(value, dict):
        types = value.get("@type", [])
        if isinstance(types, str):
            types = [types]
        if "Event" in types:
            yield value
        for key in ("@graph", "itemListElement"):
            if key in value:
                yield from event_objects(value[key])


def event_from_json(item: dict, today: date) -> dict | None:
    start = parse_datetime(str(item.get("startDate", "")))
    if not start or start.date() != today:
        return None
    end = parse_datetime(str(item.get("endDate", "")))
    location = item.get("location", {})
    if isinstance(location, dict):
        place = location.get("name", "")
        address = location.get("address")
        if isinstance(address, dict):
            place = ", ".join(part for part in (place, address.get("streetAddress", "")) if part)
    else:
        place = str(location)
    return {
        "title": html.unescape(str(item.get("name", "Untitled event"))),
        "time": f"{format_time(start)}{f' – {format_time(end)}' if end else ''}",
        "place": html.unescape(str(place)),
        "url": str(item.get("url", "")),
        "description": html.unescape(
            re.sub(r"<[^>]+>", " ", str(item.get("description", "")))
        ).strip(),
        "_sort": start.timestamp(),
    }


def livewhale_events(page: PageParser, today: date) -> list[dict]:
    decoder = json.JSONDecoder()
    events = []
    for script in page.scripts:
        assignment = re.search(r"\blivewhale\s*=\s*", script)
        if not assignment:
            continue
        try:
            data, _ = decoder.raw_decode(script[assignment.end():])
        except json.JSONDecodeError:
            continue
        preload = data.get("calendar", {}).get("preload_data", {})
        for item in preload.get("events", []):
            try:
                start = datetime.fromtimestamp(float(item["ts_start"]), TIMEZONE)
            except (KeyError, TypeError, ValueError, OverflowError):
                continue
            canceled = str(item.get("is_canceled", "")).lower() in ("1", "true", "yes")
            all_day = str(item.get("is_all_day", "")).lower() in ("1", "true", "yes")
            if start.date() != today or canceled:
                continue
            event_time = "All day" if all_day else format_time(start)
            categories = " ".join(
                str(category) for category in item.get("categories_clean", [])
            )
            events.append(
                {
                    "title": html.unescape(str(item.get("title", "Untitled event"))),
                    "time": event_time,
                    "place": html.unescape(str(item.get("location", ""))),
                    "url": urljoin(SOURCES["events"]["url"], str(item.get("href", ""))),
                    "description": html.unescape(
                        re.sub(r"<[^>]+>", " ", str(item.get("summary", "")))
                    ).strip(),
                    "categories": html.unescape(categories),
                    "_sort": start.timestamp(),
                }
            )
    return events


def scrape_events() -> tuple[list[dict], str]:
    page = load_page(SOURCES["events"]["url"])
    today = datetime.now(TIMEZONE).date()
    events = livewhale_events(page, today)
    for script in page.json_ld:
        try:
            data = json.loads(script)
        except json.JSONDecodeError:
            continue
        for item in event_objects(data):
            event = event_from_json(item, today)
            if event:
                events.append(event)

    if not events:
        for card in page.cards:
            times = card["times"]
            start = parse_datetime(times[0]) if times else None
            if not start or start.date() != today:
                continue
            title = next((text for text in card["texts"] if text), "")
            if not title:
                continue
            events.append(
                {
                    "title": title[:180],
                    "time": format_time(start),
                    "place": "",
                    "url": urljoin(SOURCES["events"]["url"], card["links"][0]) if card["links"] else "",
                    "description": "",
                    "_sort": start.timestamp(),
                }
            )

    unique = {event["title"]: event for event in events}
    sorted_events = sorted(unique.values(), key=lambda event: event["_sort"])
    for event in sorted_events:
        del event["_sort"]
    return sorted_events, "ok"


def scrape_library() -> tuple[str, str]:
    page = load_page(SOURCES["library"]["url"])
    text = " ".join(page.text_parts)
    ranges = re.findall(
        r"(\d{1,2}(?::\d{2})?\s?(?:a\.?m\.?|p\.?m\.?))\s*[–—-]\s*"
        r"(\d{1,2}(?::\d{2})?\s?(?:a\.?m\.?|p\.?m\.?))",
        text,
        flags=re.IGNORECASE,
    )
    if ranges:
        opening, closing = ranges[0]
        return f"{opening} – {closing}", "ok"
    return "", "No hours could be extracted from the library page."


def scrape_dining() -> tuple[list[str], str]:
    page = load_page(SOURCES["dining"]["url"])
    text = " ".join(page.text_parts)
    if not text.strip():
        return [], "The dining page did not expose readable menu text."
    menu_terms = ("menu", "breakfast", "lunch", "dinner", "serving", "entrée", "entree")
    items = []
    for card in page.cards:
        value = card["text"]
        if value and any(term in value.lower() for term in menu_terms):
            items.append(value[:240])
    if items:
        return list(dict.fromkeys(items))[:12], "ok"
    return [], "No readable daily menu items were found on the public page."


def build_dashboard() -> dict:
    today = datetime.now(TIMEZONE)
    results = {
        "events": [],
        "library": {"hours": "", "status": "not loaded"},
        "dining": {"items": [], "status": "not loaded"},
        "sources": [],
    }
    jobs = {
        "events": scrape_events,
        "library": scrape_library,
        "dining": scrape_dining,
    }
    for key, source in SOURCES.items():
        try:
            value, status = jobs[key]()
            if key == "events":
                results["events"] = value
            elif key == "library":
                results["library"]["hours"] = value
                results["library"]["status"] = status
            else:
                results["dining"]["items"] = value
                results["dining"]["status"] = status
            source_status = status
        except (RuntimeError, OSError) as error:
            source_status = str(error)
            if key == "library":
                results["library"]["status"] = source_status
            elif key == "dining":
                results["dining"]["status"] = source_status

        results["sources"].append(
            {
                "name": source["name"],
                "url": source["url"],
                "status": source_status,
            }
        )

    results["date"] = today.strftime("%A, %B %-d, %Y")
    results["updated_at"] = today.isoformat(timespec="minutes")
    return results


def get_dashboard() -> dict:
    global dashboard_cache
    with dashboard_refresh_lock:
        now = time.monotonic()
        with lock:
            if dashboard_cache and now - dashboard_cache[0] < CACHE_SECONDS:
                return dashboard_cache[1]
        data = build_dashboard()
        with lock:
            dashboard_cache = (time.monotonic(), data)
        return data


class Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        if self.path == "/api/today":
            try:
                body = json.dumps(get_dashboard(), ensure_ascii=False).encode("utf-8")
            except Exception as error:
                body = json.dumps({"error": str(error)}).encode("utf-8")
                self.send_response(502)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

        path = self.path.split("?", 1)[0]
        path = "/index.html" if path == "/" else path
        if path == "/about":
            path = "/about.html"
        if path not in (
            "/index.html",
            "/about.html",
            "/styles.css",
            "/app.js",
            "/assets/scu-logo.png",
        ):
            self.send_error(404)
            return
        try:
            with open(path.lstrip("/"), "rb") as file:
                body = file.read()
        except OSError:
            self.send_error(404)
            return
        content_type = {
            ".html": "text/html; charset=utf-8",
            ".css": "text/css; charset=utf-8",
            ".js": "application/javascript; charset=utf-8",
            ".png": "image/png",
        }[path[path.rfind("."):]]
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args) -> None:
        print(f"{self.log_date_time_string()} {self.address_string()} {format % args}")


if __name__ == "__main__":
    print(f"SCU Campus Today is running at http://{HOST}:{PORT}")
    ThreadingHTTPServer((HOST, PORT), Handler).serve_forever()
