#!/usr/bin/env python3
"""Healthcheck for a worker running as PID 1 in its container."""

import os
import sys


def main() -> int:
    expected = sys.argv[1] if len(sys.argv) == 2 else ""
    my_pid = os.getpid()
    pids = [1]
    try:
        pids.extend(int(p) for p in os.listdir("/proc") if p.isdigit() and int(p) != 1)
    except OSError:
        pass

    for pid in pids:
        if pid == my_pid:
            continue
        try:
            with open(f"/proc/{pid}/cmdline", "rb") as stream:
                command = stream.read().replace(b"\0", b" ").decode(errors="replace")
            if expected in command:
                with open(f"/proc/{pid}/stat", encoding="utf-8") as stream:
                    state = stream.read().split()[2]
                # T is stopped (SIGSTOP), while Z/X are dead states.
                # A stopped worker must be reported unhealthy so Compose can restart it.
                if state in {"R", "S", "D", "I"}:
                    return 0
                return 1
        except (OSError, IndexError):
            continue

    return 1


if __name__ == "__main__":
    raise SystemExit(main())
