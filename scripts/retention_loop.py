#!/usr/bin/env python3
"""Run retention cleanup once per day with a real sleep between runs."""

import asyncio
import os
import signal
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from scripts.cleanup import run_cleanup

_running = True


def _stop(*_args):
    global _running
    _running = False


async def main() -> None:
    global _running
    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)
    while _running:
        await run_cleanup()
        for _ in range(86400):
            if not _running:
                return
            await asyncio.sleep(1)


if __name__ == "__main__":
    asyncio.run(main())
