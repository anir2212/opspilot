"""Generate small local request traffic so Prometheus can observe fault effects."""

from __future__ import annotations

import argparse
import asyncio
import time

import httpx


async def generate(url: str, seconds: int, rps: float) -> tuple[int, int]:
    interval = 1 / rps
    deadline = time.monotonic() + seconds
    ok = 0
    errors = 0
    async with httpx.AsyncClient(timeout=5) as client:
        while time.monotonic() < deadline:
            started = time.monotonic()
            try:
                response = await client.get(f"{url}/work")
                ok += int(response.status_code < 400)
                errors += int(response.status_code >= 400)
            except httpx.HTTPError:
                errors += 1
            await asyncio.sleep(max(0, interval - (time.monotonic() - started)))
    return ok, errors


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://127.0.0.1:18101")
    parser.add_argument("--seconds", type=int, default=60)
    parser.add_argument("--rps", type=float, default=2)
    args = parser.parse_args()
    if args.seconds < 1 or args.rps <= 0:
        parser.error("seconds and rps must be positive")
    ok, errors = asyncio.run(generate(args.url, args.seconds, args.rps))
    print(f"ok={ok} errors={errors}")


if __name__ == "__main__":
    main()
