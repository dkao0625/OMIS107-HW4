# SCU Today

A single-page Santa Clara University campus dashboard. The Python standard-library server reads public SCU calendar, library-hours, and dining pages and serves the extracted information to the page. The event search and evening filter run locally in the browser; no AI or external search service is used.

The header uses the supplied Santa Clara University logo, cropped from the provided image and served from `assets/scu-logo.png`.

## Run locally

Requires Python 3.9 or newer. No packages are required.

```sh
python3 server.py
```

Open [http://127.0.0.1:8000](http://127.0.0.1:8000). Stop the server with Ctrl+C.

The server identifies itself as `SCU-CampusToday/1.0`, checks each source's `robots.txt`, caches source pages for ten minutes, and does not fetch the same page more than once every 30 seconds. A source that disallows access or does not publish readable information is reported as unavailable rather than replaced with made-up content. Source addresses are listed in `server.py`.