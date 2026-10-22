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

## Deploy publicly with Render

The included `render.yaml` configures a Render web service. To publish the app:

1. Push this repository to GitHub.
2. In Render, choose **New + → Blueprint**, connect this GitHub repository, and apply the `render.yaml` configuration.
3. Wait for the service to finish deploying, then open the `onrender.com` URL shown on its Render dashboard. That HTTPS URL is the public link to share.

Render supplies the service port through `PORT`; the app reads it from the environment and binds to `0.0.0.0` for public access. Its local defaults remain `127.0.0.1:8000`. On Render's free plan, a service may sleep when idle, so its first request after a pause can take longer.