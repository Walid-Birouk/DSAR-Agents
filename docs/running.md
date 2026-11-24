# Running and self-hosting

## Locally

```bash
uv sync
uv run a15 generate                        # Ferlane's four systems and the 160 cases (seed 15)
cd frontend && npm ci && npm run build && cd ..
uv run a15 web                             # http://127.0.0.1:8000
```

Without a model key the web app runs in **scripted** mode: the prepared cases are played by the oracle agent, with
no model and no cost. With `MISTRAL_API_KEY` in `.env` (or `A15_WEB_PROVIDER=deepseek` and `DEEPSEEK_API_KEY`),
it runs **live**, and visitors can write their own requests. `--scripted` forces scripted mode.

Evals and experiments: see the README.

## On a server (the public demo)

One small EU virtual server is enough (PLAN.md 15.2). These steps have not been run on a server yet: they need
a server account and a domain.
- **What has been checked locally (2026-11-23):**
  - the image builds and its container starts read-only, as user 10015, and answers `/api/health`;
  - Compose, Caddy, the collector and Prometheus accept their configs;
  - the observability stack received and stored a case's trace.
- **CI repeats those checks** on every push (`.github/workflows/ci.yml`, jobs `image` and `deploy-config`).
- **A version tag** (`v0.1.0`) publishes the image to `ghcr.io/<owner>/<repo>` (`release.yml`).

1. A Debian or Ubuntu server with Docker Engine and the Compose plugin, a non-root user in the `docker` group, and
   a firewall that allows 22, 80 and 443 only.
2. A DNS A record for the domain, pointing at the server (optionally behind a free CDN/DNS layer).
3. `git clone` the repository to `/srv/a15`. Then `cp deploy/production.env.example deploy/production.env`, and
   fill in the domain, the Mistral key and four fresh secrets (`python -c "import secrets;
   print(secrets.token_hex(32))"`).
4. `docker compose -f deploy/docker-compose.yml --env-file deploy/production.env up -d --build`. Caddy obtains the
   HTTPS certificate on its own.
5. Back up the web store and its audit log every night:
   `30 3 * * * /srv/a15/deploy/backup.sh /srv/a15/backups` (14 days kept).
6. Optional: the private observability stack.
   `GRAFANA_ADMIN_PASSWORD=... docker compose -f deploy/observability/docker-compose.yml up -d`, then set
   `A15_OTLP_ENDPOINT` for the app and reach Jaeger and Grafana over an SSH tunnel
   (`ssh -L 16686:127.0.0.1:16686 -L 3000:127.0.0.1:3000 server`).
7. Set a spending limit on the provider account as well. The app's own cap is EUR 2 a day (decision D4).

## The frontend on Vercel, the backend on the server

The web app is static, so Vercel can serve it (`frontend/vercel.json`: Vite build, SPA fallback, the same
security headers as Caddy). The API, the live runs and the sandboxes stay on the server above.

**Before a backend is linked**, the Vercel site:
- shows the first page from the recorded runs (`frontend/public/harness.json`, written by `a15 eval harness`);
- says that the live desk is not connected; Try it, Break it and Follow a request need it.

**To link it**, keep the browser on one origin, so the session cookie stays first-party and the CSP's
`connect-src 'self'` holds. Add a rewrite before the SPA fallback in `frontend/vercel.json`:

```json
"rewrites": [
  { "source": "/api/:path*", "destination": "https://api.your-domain.example/api/:path*" },
  { "source": "/((?!api/).*)", "destination": "/index.html" }
]
```

**What Vercel's documentation does and does not settle** (read on 2026-11-23, vercel.com/docs/rewrites):
- **Stated:**
  - an external rewrite proxies the request ("without changing the URL in the browser");
  - Vercel honours the upstream's `Cache-Control`. The app sends `Cache-Control: no-store` on every `/api/`
    answer, so no visitor's data is cached.
- **Not stated:** whether a rewrite streams server-sent events, how long it may stay open, and whether it
  passes `Set-Cookie`. So:
  - the run view falls back to polling when the event stream drops;
  - after linking, check that a run's steps appear live and that the session cookie is set.

  If the cookie does not pass, serve the whole app from the server (Caddy) instead; that path needs no proxy.

On the server, set `A15_SECURE_COOKIES=1` (HTTPS-only session cookie) and `A15_TRUST_PROXY=1`, so the client
address comes from `X-Forwarded-For`.

## Replacing the fictional company

The desk talks to company systems only through MCP servers with subject-scoped tools (`mcp_servers/`). To connect
your own systems, write servers with the same tool names and result shapes (see `tests/test_mcp_servers.py`,
the contract tests), backed by your databases. Keep the scope rules: every tool takes a case token and returns
only the verified person's records.
