# 0007: React web app, FastAPI, one EU server with Docker Compose

**Decision.**
- **Web app:** React with TypeScript (Vite) and Tailwind, with live progress through server-sent events.
- **Backend:** FastAPI, with agent runs on a small job queue.
- **Hosting:** one EU virtual server running Docker Compose behind Caddy (automatic HTTPS), with a free CDN/DNS
  layer in front.

**Why.**
- The demo has to feel like a product: live run view, reviewer screen, red-team page, trace view.
- One server keeps cost at roughly EUR 5-15 per month, keeps data in the EU, and runs the same Compose file people
  use to self-host.

**Given up.** Managed platform conveniences such as autoscaling and managed databases. The demo's load does not
need them.
