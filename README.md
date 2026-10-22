# SCU Today

SCU Today puts campus events, library hours, and dining information on one page. Browse the dashboard at `/` and visit `/about` for source details. Event search and the evening filter are simple browser-side filters; no AI is used.

The Python server reads public SCU pages and checks each site's `robots.txt`. It identifies itself as `SCU-CampusToday/1.0`, caches pages for ten minutes, and waits at least 30 seconds before requesting a page again. When a source is unavailable or does not publish readable information, the site displays a message instead of guessing. The header uses the supplied SCU logo in `assets/scu-logo.png`.

## Run locally

Requires Python 3.9 or newer.

```sh
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python server.py
```

Open [http://127.0.0.1:8000](http://127.0.0.1:8000). Stop the server with Ctrl+C.