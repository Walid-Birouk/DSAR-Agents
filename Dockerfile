# The desk: the web app and the API in one image, the advisor from the same image (PLAN.md 11.2, 11.4).
# Image tags checked on Docker Hub on 2026-11-22.

FROM node:24.22.3-bookworm-slim AS web
WORKDIR /web
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
RUN npm run build

FROM python:3.12.16-slim-bookworm AS app
COPY --from=ghcr.io/astral-sh/uv:0.11.33 /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PROJECT_ENVIRONMENT=/app/.venv PYTHONUNBUFFERED=1
WORKDIR /app
COPY pyproject.toml uv.lock README.md LICENSE ./
RUN uv sync --locked --no-dev --no-install-project
COPY src/ src/
RUN uv sync --locked --no-dev
COPY evals/ evals/
COPY --from=web /web/dist frontend/dist
# The fictional company is generated at build time from its seed, so every image holds the same data.
RUN /app/.venv/bin/a15 generate --out data/generated
RUN useradd --system --uid 10015 --home /app desk && mkdir -p runs && chown -R desk:desk runs
USER desk
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=4)"]
CMD ["/app/.venv/bin/a15", "web", "--host", "0.0.0.0", "--port", "8000", "--root", "runs/web"]
