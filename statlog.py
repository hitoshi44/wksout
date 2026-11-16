#!/usr/bin/env python3
# ビルド (BUILD_TAG) ごとに CPU / メモリを集計して表示する
# 使い方: sudo python3 watch_builds.py [間隔秒=5]
import collections
import os
import sys
import time

INTERVAL = float(sys.argv[1]) if len(sys.argv) > 1 else 5
HZ = os.sysconf("SC_CLK_TCK")
PAGE = os.sysconf("SC_PAGE_SIZE")


def snapshot():
    procs = {}
    for d in os.listdir("/proc"):
        if not d.isdigit():
            continue
        try:
            with open(f"/proc/{d}/environ", "rb") as f:
                env = f.read().split(b"\0")
            tag = next((e[10:].decode() for e in env if e.startswith(b"BUILD_TAG=")), None)
            if not tag:
                continue
            with open(f"/proc/{d}/stat") as f:
                s = f.read()
            fields = s[s.rfind(")") + 2:].split()
            cpu = int(fields[11]) + int(fields[12])  # utime + stime (tick)
            start = fields[19]                       # PID 再利用対策
            with open(f"/proc/{d}/statm") as f:
                rss = int(f.read().split()[1]) * PAGE
            procs[(int(d), start)] = (tag, cpu, rss)
        except (OSError, ValueError, IndexError):
            continue  # 読んでいる間に終了したプロセスなど
    return procs


prev, t_prev = snapshot(), time.monotonic()
while True:
    time.sleep(INTERVAL)
    cur, t_cur = snapshot(), time.monotonic()
    elapsed = t_cur - t_prev
    agg = collections.defaultdict(lambda: [0.0, 0, 0])  # cpu%, rss, 数
    for key, (tag, cpu, rss) in cur.items():
        delta = cpu - (prev[key][1] if key in prev else 0)
        a = agg[tag]
        a[0] += delta / HZ / elapsed * 100
        a[1] += rss
        a[2] += 1
    ts = time.strftime("%H:%M:%S")
    for tag, (c, r, n) in sorted(agg.items()):
        print(f"{ts} {tag} procs={n} cpu={c:.1f}% rss={r / 1048576:.0f}MB", flush=True)
    prev, t_prev = cur, t_cur
