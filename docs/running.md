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

One small EU virtual server is enough (PLAN.md 15.2). These steps have not been run yet: they need the owner's
server account and domain.

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

## Replacing the fictional company

The desk talks to company systems only through MCP servers with subject-scoped tools (`mcp_servers/`). To connect
your own systems, write servers with the same tool names and result shapes (see `tests/test_mcp_servers.py`,
the contract tests), backed by your databases. Keep the scope rules: every tool takes a case token and returns
only the verified person's records.
