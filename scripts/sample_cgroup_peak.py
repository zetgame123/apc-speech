#!/usr/bin/env python3
"""П.5: сэмплинг пика RAM через cgroup memory.peak (не docker stats в обёртке).

Почему так: в §16 сэмплинг `docker stats` шёл в обёртке eval_hour.py и погиб
вместе с ней — пик не измерен. `memory.peak` — монотонный счётчик ядра на
время жизни cgroup: достаточно прочитать его ПОСЛЕ прогона, переживать
обёртке не нужно. Периодический опрос `memory.current` — только профиль
для отчёта; главный результат — `memory.peak` в конце.

Использование:
  1. docker restart apc_small-worker-long-1   # сброс lifetime-пика (без -v!)
  2. дождаться healthy
  3. setsid nohup python3 scripts/sample_cgroup_peak.py --container apc_small-worker-long-1 \
        --out reports/hour_peak.log > /tmp/opencode/peak_sampler.log 2>&1 < /dev/null &
  4. запуск часовки (scripts/eval_hour.py под setsid/nohup аналогично)
  5. после succeeded: memory.peak из лога + прямым чтением (см. конец лога)

Частота CPU не меняется, sudo не используется (чтение cgroup — обычное чтение
файлов sysfs, доступно владельцу; запись никуда не идёт).
"""

import argparse
import subprocess
import time


def cgroup_base(container: str) -> str:
    cid = subprocess.run(
        ["docker", "inspect", container, "--format", "{{.Id}}"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    return f"/sys/fs/cgroup/system.slice/docker-{cid}.scope"


def read1(base: str, name: str) -> str:
    try:
        with open(f"{base}/{name}", encoding="utf-8") as f:
            return f.read().strip().split("\n")[0]
    except Exception as e:
        return f"ERR:{e}"


def read_all(base: str, name: str) -> str:
    try:
        with open(f"{base}/{name}", encoding="utf-8") as f:
            return f.read()
    except Exception as e:
        return f"ERR:{e}"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--container", default="apc_small-worker-long-1")
    ap.add_argument("--interval", type=int, default=60)
    ap.add_argument("--out", default="reports/hour_peak.log")
    args = ap.parse_args()

    base = cgroup_base(args.container)
    limit = read1(base, "memory.max")
    with open(args.out, "a", encoding="utf-8") as f:
        f.write(
            f"# start {time.strftime('%Y-%m-%dT%H:%M:%S')} container={args.container}\n"
            f"# cgroup={base} memory.max={limit}\n"
        )
        f.write("# t_utc memory.current_bytes memory.peak_bytes oom_kill\n")
        while True:
            cur = read1(base, "memory.current")
            peak = read1(base, "memory.peak")
            events = read_all(base, "memory.events")
            oom = "?"
            for line in events.split("\n"):
                if line.startswith("oom_kill "):
                    oom = line.split()[1]
            f.write(f"{time.strftime('%Y-%m-%dT%H:%M:%S')} {cur} {peak} {oom}\n")
            f.flush()
            time.sleep(args.interval)


if __name__ == "__main__":
    main()
