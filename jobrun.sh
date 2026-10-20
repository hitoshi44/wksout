#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
曜日 × 時刻の平均同時実行数を、全ジョブと「指定したジョブのまとまり」ごとに表にする。
標準ライブラリのみ（Python 3.11 以上）。

使い方（jobs_YYYY-MM-DD.tsv が並んでいるフォルダで）:
  python heatmap_report.py                                   # 全ジョブだけ（＋占有の多いジョブ名の一覧）
  python heatmap_report.py -g "integration=^integration-" -g "e2e=^e2e" -g "build=^build$"
  python heatmap_report.py --groups groups.txt               # まとまりをファイルで指定
  python heatmap_report.py --weeks 0                         # 期間全体（既定は直近 12 週）

まとまりの指定:
  "表示名=正規表現"。正規表現はジョブ名に対して部分一致（Python の re.search）。
  完全一致にしたいときは ^ と $ を付ける。上から順に判定し、最初に当たったまとまりに入れる。
  groups.txt は 1 行 1 つ、同じ書式（# で始まる行と空行は無視）。

出力（--outdir、既定 report_heatmap/）:
  heatmap.html       表のまとめ（ブラウザで開く）
  heatmap_cells.csv  まとまり・曜日・時刻ごとの平均同時実行数
  job_names.csv      ジョブ名ごとの回数・占有時間と、入ったまとまり
"""
import argparse
import csv
import glob
import html
import math
import os
import re
import sys
from collections import defaultdict
from datetime import date, datetime, timedelta

COLS = ["project", "pipeline_id", "job_id", "name", "stage", "status", "duration",
        "queued_duration", "created_at", "started_at", "finished_at", "runner_id",
        "runner", "tags"]
EPOCH = date(1970, 1, 1)
WEEKDAYS = ["月", "火", "水", "木", "金", "土", "日"]
ALL = "全ジョブ"
OTHER = "指定外"


def parse_ts(s):
    try:
        return datetime.fromisoformat(s).timestamp() if s else None
    except ValueError:
        return None


def load(paths, tzoff):
    """job_id で重複除去。返り値は {job_id: (開始秒, 終了秒, ジョブ名)}（ローカル時刻）"""
    jobs = {}
    for path in paths:
        with open(path, encoding="utf-8", errors="replace", newline="") as f:
            for row in csv.reader(f, delimiter="\t", quoting=csv.QUOTE_NONE):
                if not row or row[0] == "project":
                    continue
                r = dict(zip(COLS, row + [""] * (len(COLS) - len(row))))
                s, e = parse_ts(r["started_at"]), parse_ts(r["finished_at"])
                if s is None or e is None or e <= s:
                    continue
                jobs[r["job_id"]] = (s + tzoff, e + tzoff, r["name"])
    return jobs


def parse_group(text):
    if "=" not in text:
        sys.exit(f"まとまりの書式が違います（表示名=正規表現）: {text}")
    label, pat = text.split("=", 1)
    label, pat = label.strip(), pat.strip()
    try:
        return label, re.compile(pat)
    except re.error as ex:
        sys.exit(f"正規表現の誤り [{label}] {pat}: {ex}")


# ------------------------------------------------------------------ HTML
CSS = """
body{font-family:system-ui,-apple-system,"Segoe UI","Hiragino Sans","Yu Gothic UI",sans-serif;
 margin:0;background:#f7f7f5;color:#1f2328;line-height:1.6}
main{max-width:1180px;margin:0 auto;padding:24px 16px 64px}
h1{font-size:1.5rem;margin:0 0 4px}h2{font-size:1.15rem;margin:36px 0 6px;border-bottom:1px solid #ddd;padding-bottom:4px}
.sub{color:#666;font-size:.9rem}.meta{color:#555;font-size:.85rem;margin:2px 0 8px}
.fn{color:#666;font-size:.78rem;margin:4px 0 0}
.tw{overflow-x:auto}table{border-collapse:collapse;font-size:.85rem;background:#fff}
th,td{border:1px solid #e3e3e0;padding:3px 6px}th{background:#f0f0ec;text-align:left;font-size:.78rem}
td.n{text-align:right;white-space:nowrap}
.heat{width:100%;table-layout:fixed}
.heat th,.heat td{padding:3px 0;text-align:center;font-size:.72rem;white-space:nowrap}
.heat th:first-child{width:4.2em}
code{font-size:.8rem;background:#eee;padding:0 4px;border-radius:3px}
"""


def heatmap(cells, vmax):
    vmax = vmax or 1
    h = ['<table class="heat"><tr><th>曜日＼時</th>' + "".join(f"<th>{i}時</th>" for i in range(24)) + "</tr>"]
    for wd in range(7):
        h.append(f"<tr><th>{WEEKDAYS[wd]}</th>")
        for v in cells[wd]:
            a = min(1.0, v / vmax)
            fg = "#fff" if a > 0.55 else "inherit"
            txt = f"{v:.1f}" if v >= 0.05 else ("0" if v == 0 else "·")
            h.append(f'<td style="background:rgba(37,99,235,{a:.2f});color:{fg}" title="{v:.3f}">{txt}</td>')
        h.append("</tr>")
    h.append("</table>")
    return "".join(h)


def table(headers, rows, num_from=1):
    h = ["<div class='tw'><table><tr>" + "".join(f"<th>{html.escape(x)}</th>" for x in headers) + "</tr>"]
    for r in rows:
        h.append("<tr>" + "".join(f"<td class='{'n' if i >= num_from else ''}'>{html.escape(str(c))}</td>"
                                  for i, c in enumerate(r)) + "</tr>")
    h.append("</table></div>")
    return "".join(h)


# ------------------------------------------------------------------ メイン
def main():
    ap = argparse.ArgumentParser(description="曜日 × 時刻の平均同時実行数（全ジョブ / 指定したジョブのまとまり）")
    ap.add_argument("inputs", nargs="*", default=["jobs_*.tsv"])
    ap.add_argument("-g", "--group", action="append", default=[], help="まとまり '表示名=正規表現'（複数指定可）")
    ap.add_argument("--groups", help="まとまりを書いたファイル（1 行 1 つ、'表示名=正規表現'）")
    ap.add_argument("--weeks", type=int, default=12, help="直近何週を平均するか（0 なら期間全体、既定 12）")
    ap.add_argument("--since", help="この日以降（例: 2026-07-01）。指定すると --weeks より優先")
    ap.add_argument("--until", help="この日より前（例: 2026-10-01）")
    ap.add_argument("--no-other", action="store_true", help="「指定外」の表を出さない")
    ap.add_argument("--same-scale", action="store_true", help="色の濃さを全ジョブの表と同じ基準にする（既定は表ごと）")
    ap.add_argument("--top", type=int, default=30, help="占有の多いジョブ名を何件表示するか（既定 30）")
    ap.add_argument("--tz", type=float, default=9, help="UTC からの時差（既定 9）")
    ap.add_argument("--outdir", default="report_heatmap")
    a = ap.parse_args()
    try:
        sys.stdout.reconfigure(errors="replace")
    except Exception:
        pass

    # まとまり
    specs = list(a.group)
    if a.groups:
        with open(a.groups, encoding="utf-8") as f:
            specs += [ln.strip() for ln in f if ln.strip() and not ln.lstrip().startswith("#")]
    groups = [parse_group(t) for t in specs]
    labels = [lb for lb, _ in groups]
    if len(set(labels)) != len(labels):
        sys.exit("表示名が重複しています")

    # 読み込み
    files = []
    for p in a.inputs:
        files += [f for f in (sorted(glob.glob(p)) if any(c in p for c in "*?[") else [p])
                  if os.path.isfile(f) and f not in files]
    if not files:
        sys.exit(f"入力ファイルが見つかりません: {' '.join(a.inputs)}")
    jobs = load(files, a.tz * 3600)
    if not jobs:
        sys.exit("集計対象のジョブがありません")
    print(f"入力: {len(files)} ファイル / ジョブ {len(jobs):,} 件")

    # 期間（日単位、ローカル時刻）
    last_day = int(max(e for _, e, _ in jobs.values()) // 86400)
    if a.since or a.until:
        d0 = (date.fromisoformat(a.since) - EPOCH).days if a.since else int(min(s for s, _, _ in jobs.values()) // 86400)
        d1 = (date.fromisoformat(a.until) - EPOCH).days if a.until else last_day + 1
    elif a.weeks > 0:
        d1 = last_day + 1
        d0 = d1 - a.weeks * 7
    else:
        d0 = int(min(s for s, _, _ in jobs.values()) // 86400)
        d1 = last_day + 1
    lo, hi = d0 * 86400, d1 * 86400
    h0, n = d0 * 24, (d1 - d0) * 24
    period = f"{EPOCH + timedelta(days=d0):%Y/%m/%d} 〜 {EPOCH + timedelta(days=d1 - 1):%Y/%m/%d}"
    print(f"期間: {period}（{(d1 - d0) / 7:.1f} 週）")

    # ジョブ名 → まとまり
    names = sorted({nm for _, _, nm in jobs.values()})
    name_group = {}
    for nm in names:
        name_group[nm] = next((lb for lb, rx in groups if rx.search(nm)), OTHER)

    # 1 時間ごとの占有秒（まとまりごと）
    keys = [ALL] + labels + ([] if a.no_other or not groups else [OTHER])
    busy = {k: [0.0] * n for k in keys}
    name_stat = defaultdict(lambda: [0, 0.0])        # ジョブ名 → [回数, 占有秒]（期間内）
    for s, e, nm in jobs.values():
        s, e = max(s, lo), min(e, hi)
        if e <= s:
            continue
        g = name_group[nm]
        targets = [busy[ALL]] + ([busy[g]] if g in busy else [])
        for hh in range(int(s // 3600), int((e - 1e-9) // 3600) + 1):
            ov = min(e, (hh + 1) * 3600) - max(s, hh * 3600)
            if ov > 0:
                for t in targets:
                    t[hh - h0] += ov
        st = name_stat[nm]
        st[0] += 1
        st[1] += e - s

    # 曜日 × 時刻の平均
    cnt = [[0] * 24 for _ in range(7)]
    for k in range(n):
        hh = h0 + k
        cnt[(EPOCH + timedelta(days=hh // 24)).weekday()][hh % 24] += 1

    def cells_of(arr):
        sm = [[0.0] * 24 for _ in range(7)]
        for k in range(n):
            hh = h0 + k
            sm[(EPOCH + timedelta(days=hh // 24)).weekday()][hh % 24] += arr[k] / 3600
        return [[sm[w][h] / cnt[w][h] if cnt[w][h] else 0.0 for h in range(24)] for w in range(7)]

    cells = {k: cells_of(busy[k]) for k in keys}
    total_sec = sum(busy[ALL]) or 1

    # まとまりごとの情報
    ginfo = {}
    for k in keys:
        members = sorted(((nm, *name_stat[nm]) for nm in names
                          if nm in name_stat and (k == ALL or name_group[nm] == k)), key=lambda x: -x[2])
        sec = sum(busy[k])
        ginfo[k] = {"members": members, "jobs": sum(m[1] for m in members), "hours": sec / 3600,
                    "share": sec / total_sec * 100, "avg": sec / (n * 3600),
                    "peak": max(max(r) for r in cells[k]),
                    "peak_at": max(((w, h) for w in range(7) for h in range(24)), key=lambda wh: cells[k][wh[0]][wh[1]])}

    # ------------------------------------------------------------- CSV
    os.makedirs(a.outdir, exist_ok=True)
    with open(os.path.join(a.outdir, "heatmap_cells.csv"), "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["まとまり", "曜日", "時", "平均同時実行数"])
        for k in keys:
            for wd in range(7):
                for hr in range(24):
                    w.writerow([k, WEEKDAYS[wd], hr, round(cells[k][wd][hr], 3)])
    ranked = sorted(name_stat.items(), key=lambda kv: -kv[1][1])
    with open(os.path.join(a.outdir, "job_names.csv"), "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["ジョブ名", "回数", "占有時間h", "全体に占める割合%", "まとまり"])
        for nm, (c, sec) in ranked:
            w.writerow([nm, c, round(sec / 3600, 2), round(sec / total_sec * 100, 2),
                        name_group[nm] if groups else ""])

    # ------------------------------------------------------------- HTML
    vmax_all = ginfo[ALL]["peak"]
    H = ["<!doctype html><html lang='ja'><head><meta charset='utf-8'>"
         "<meta name='viewport' content='width=device-width,initial-scale=1'><title>曜日×時刻の同時実行数</title>"
         f"<style>{CSS}</style></head><body><main>",
         "<h1>曜日 × 時刻の平均同時実行数</h1>",
         f"<div class='sub'>{period}（{(d1 - d0) / 7:.1f} 週の平均・UTC{a.tz:+g}）</div>"]

    def section(no, k):
        gi = ginfo[k]
        wd, hr = gi["peak_at"]
        H.append(f"<h2>{no}. {html.escape(k)}</h2>")
        meta = [f"ジョブ {gi['jobs']:,} 件", f"占有 {gi['hours']:,.0f} h"]
        if k != ALL:
            meta.append(f"全ジョブに占める割合 {gi['share']:.1f}%")
            rx = next((rx.pattern for lb, rx in groups if lb == k), None)
            if rx is not None:
                meta.append(f"条件 <code>{html.escape(rx)}</code>")
        meta.append(f"一番多い時間帯 {WEEKDAYS[wd]} {hr}時（{gi['peak']:.2f}）")
        H.append("<div class='meta'>" + " ／ ".join(meta) + "</div>")
        H.append(heatmap(cells[k], vmax_all if a.same_scale else gi["peak"]))
        if k != ALL:
            mem = gi["members"]
            shown = ", ".join(f"{html.escape(nm)}（{c:,}）" for nm, c, _ in mem[:20])
            more = f" ほか {len(mem) - 20} 種類" if len(mem) > 20 else ""
            H.append(f"<p class='fn'>含まれるジョブ名 {len(mem)} 種類：{shown}{more}</p>")

    no = 1
    for k in keys:
        if k == OTHER and not ginfo[k]["jobs"]:
            continue
        section(no, k)
        no += 1

    if groups:
        H.append(f"<h2>{no}. まとまりの比較</h2>")
        H.append(table(["まとまり", "ジョブ名の種類", "ジョブ数", "占有h", "全ジョブに占める割合", "平均同時", "一番多い時間帯", "その時間帯の平均同時"],
                       [[k, len(ginfo[k]["members"]), f"{ginfo[k]['jobs']:,}", f"{ginfo[k]['hours']:,.0f}",
                         f"{ginfo[k]['share']:.1f}%", f"{ginfo[k]['avg']:.2f}",
                         f"{WEEKDAYS[ginfo[k]['peak_at'][0]]} {ginfo[k]['peak_at'][1]}時", f"{ginfo[k]['peak']:.2f}"]
                        for k in keys if ginfo[k]["jobs"]]))
        no += 1

    H.append(f"<h2>{no}. 占有時間の多いジョブ名（上位 {a.top}）</h2>")
    H.append(table(["ジョブ名", "回数", "占有h", "全ジョブに占める割合"] + (["まとまり"] if groups else []),
                   [[nm, f"{c:,}", f"{sec / 3600:,.1f}", f"{sec / total_sec * 100:.1f}%"] + ([name_group[nm]] if groups else [])
                    for nm, (c, sec) in ranked[:a.top]]))
    H.append("<p class='fn'>※全件は job_names.csv。まとまりの指定（-g）を考えるときの参考に</p>")
    H.append("</main></body></html>")
    with open(os.path.join(a.outdir, "heatmap.html"), "w", encoding="utf-8") as f:
        f.write("".join(H))

    # ------------------------------------------------------------- 画面
    print()
    print(f"占有時間の多いジョブ名（上位 {min(a.top, len(ranked))}）")
    for nm, (c, sec) in ranked[:a.top]:
        tag = f"  → {name_group[nm]}" if groups else ""
        print(f"  {sec / total_sec * 100:5.1f}%  {c:7,} 回  {nm}{tag}")
    if groups:
        print()
        for k in keys[1:]:
            print(f"[{k}] ジョブ名 {len(ginfo[k]['members'])} 種類 / 全体の {ginfo[k]['share']:.1f}%")
    print(f"\n出力: {os.path.join(a.outdir, 'heatmap.html')}")


if __name__ == "__main__":
    main()
