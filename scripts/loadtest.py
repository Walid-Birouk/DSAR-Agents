"""Load test for the web app (PLAN.md M7): N visitors at once, each with their own session, each loading the
pages' data, running a prepared case end to end and reading the result.

    uv run a15 web --scripted --port 8766 --root runs/loadtest &
    uv run python scripts/loadtest.py --url http://127.0.0.1:8766 --visitors 20

Scripted mode needs no model, so this measures the desk and the web app, not a provider. Prints the latency of
the API calls (p50, p95, max), the time from start to a finished case, and every failure.
"""

from __future__ import annotations

import argparse
import asyncio
import statistics
import time

import httpx


def pct(values: list[float], q: float) -> float:
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, round(q * (len(ordered) - 1)))] if ordered else 0.0


async def visitor(url: str, n: int, calls: list[float], cases: list[float], failures: list[str]) -> None:
    async with httpx.AsyncClient(base_url=url, timeout=60) as c:

        async def get(path: str) -> httpx.Response:
            t = time.perf_counter()
            r = await c.get(path)
            calls.append(time.perf_counter() - t)
            return r

        try:
            await get("/api/system")
            presets = (await get("/api/presets?kind=try")).json()
            case_id = presets[n % len(presets)]["case_id"]
            started = time.perf_counter()
            t = time.perf_counter()
            r = await c.post("/api/runs", json={"case_id": case_id})
            calls.append(time.perf_counter() - t)
            r.raise_for_status()
            run_id = r.json()["run_id"]
            while True:
                d = (await get(f"/api/runs/{run_id}")).json()
                if d["run"]["status"] in ("done", "failed"):
                    break
                await asyncio.sleep(0.25)
            cases.append(time.perf_counter() - started)
            if d["run"]["status"] != "done":
                failures.append(f"visitor {n}: run {run_id} failed ({d['run'].get('error')})")
            await get(f"/api/runs/{run_id}/trace")
        except Exception as exc:
            failures.append(f"visitor {n}: {type(exc).__name__}: {exc}")


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://127.0.0.1:8766")
    ap.add_argument("--visitors", type=int, default=20)
    args = ap.parse_args()
    calls: list[float] = []
    cases: list[float] = []
    failures: list[str] = []
    t0 = time.perf_counter()
    await asyncio.gather(*(visitor(args.url, i, calls, cases, failures) for i in range(args.visitors)))
    wall = time.perf_counter() - t0
    print(
        f"{args.visitors} visitors at once, {len(calls)} API calls, {len(cases)} cases, "
        f"wall time {wall:.1f} s"
    )
    print(
        f"API latency: p50 {pct(calls, 0.5) * 1000:.0f} ms, p95 {pct(calls, 0.95) * 1000:.0f} ms, max "
        f"{max(calls, default=0) * 1000:.0f} ms"
    )
    if cases:
        print(
            f"start to finished case: mean {statistics.fmean(cases):.1f} s, p95 {pct(cases, 0.95):.1f} s, "
            f"max {max(cases):.1f} s"
        )
    print(f"failures: {len(failures)}")
    for f in failures:
        print(" ", f)
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
