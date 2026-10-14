#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
GitLab CI ジョブ履歴（jobs_all.tsv）から runner の占有状況を集計する。
標準ライブラリのみ（Python 3.11 以上）。

使い方（jobs_YYYY-MM-DD.tsv が並んでいるフォルダで実行）:
  python ci_usage.py                                      # jobs_*.tsv をすべて読む
  python ci_usage.py --capacity 8                         # runner 枠の合計が分かる場合
  python ci_usage.py --tags "*docker*"                    # タグ（runner プール）で絞る
  python ci_usage.py --exclude "123:e2e-*" --exclude "*:nightly-*"
                                                          # 「プロジェクトID:ジョブ名」で削った場合を試算

出力（--outdir、既定 report/）:
  report.html        グラフと表のまとめ（ブラウザで開く）
  weekly.csv         週別の占有時間・同時実行数・待ち時間
  jobs_ranking.csv   ジョブ別の占有時間（混雑する時間帯に占める割合の順）
  jobs_monthly.csv   上位ジョブの月別占有時間
  projects.csv       プロジェクト別の占有時間・同時実行数（・稼働率）
  projects_monthly.csv  プロジェクト別の月別占有時間
  tags.csv           タグ（runner プール）別の占有時間
  whatif_weekly.csv  --exclude を指定したときの週別比較

考え方:
  - 占有時間 = ジョブの started_at〜finished_at の合計（runner の枠を埋めていた時間）
  - 同時実行数 = その時点で走っていたジョブ数
  - 混雑する時間帯 = 1 時間平均の同時実行数が全時間の上位 --busy-pct %（既定 10%）の時間
  - CPU・メモリ使用量そのものではない点に注意
"""
import argparse
import csv
import fnmatch
import glob
import html
import json
import math
import os
import re
import sys
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta

COLS = ["project", "pipeline_id", "job_id", "name", "stage", "status", "duration",
        "queued_duration", "created_at", "started_at", "finished_at", "runner_id",
        "runner", "tags"]
EPOCH = date(1970, 1, 1)
WEEKDAYS = ["月", "火", "水", "木", "金", "土", "日"]


# ------------------------------------------------------------------ 読み込み
class Job:
    __slots__ = ("jid", "project", "name", "stage", "status", "s", "e", "queued", "tags")


def parse_ts(s):
    if not s:
        return None
    try:
        return datetime.fromisoformat(s).timestamp()
    except ValueError:
        return None


def to_float(s):
    try:
        return float(s) if s else None
    except ValueError:
        return None


def expand_inputs(patterns):
    """jobs_*.tsv のようなパターンを Python 側で展開する（cmd / PowerShell でも動くように）。"""
    files = []
    for p in patterns:
        hit = sorted(glob.glob(p)) if any(c in p for c in "*?[") else [p]
        files += [f for f in hit if f not in files and os.path.isfile(f)]
    return files


def load(paths, tzoff):
    """複数ファイルを job_id で重複除去しながら読む。時刻はすべて「ローカル時刻の秒」に変換する。"""
    best = {}
    rows = 0
    for path in paths:
        rows += _load_one(path, tzoff, best)
    return rows, list(best.values())


def _load_one(path, tzoff, best):
    rows = 0
    with open(path, encoding="utf-8", errors="replace", newline="") as f:
        for row in csv.reader(f, delimiter="\t", quoting=csv.QUOTE_NONE):
            if not row or row[0] == "project":   # 各ファイルのヘッダ行は飛ばす
                continue
            if len(row) < len(COLS):
                row = row + [""] * (len(COLS) - len(row))
            r = dict(zip(COLS, row))
            try:
                jid = int(r["job_id"])
            except ValueError:
                continue
            rows += 1
            s, e = parse_ts(r["started_at"]), parse_ts(r["finished_at"])
            j = Job()
            j.jid, j.project, j.name, j.stage, j.status = jid, r["project"], r["name"], r["stage"], r["status"]
            j.s = s + tzoff if s is not None else None
            j.e = e + tzoff if e is not None else None
            j.queued = to_float(r["queued_duration"])
            j.tags = r["tags"]
            old = best.get(jid)
            # 同じジョブが複数期間に出たら、終了しているほうを採用
            if old is None or (old.e is None and j.e is not None):
                best[jid] = j
    return rows


# ------------------------------------------------------------------ 計算
def percentile(sorted_vals, p):
    if not sorted_vals:
        return None
    k = max(0, min(len(sorted_vals) - 1, math.ceil(p / 100 * len(sorted_vals)) - 1))
    return sorted_vals[k]


def concurrency(jobs, h0, n, need_busy=True):
    """1 時間ごとの占有秒（busy）と最大同時実行数（hmax）を返す。"""
    busy = [0.0] * n if need_busy else None
    hmax = [0] * n
    ev = []
    for j in jobs:
        ev.append((j.s, 1))
        ev.append((j.e, -1))
        if need_busy:
            s, e = j.s, j.e
            for h in range(int(s // 3600), int(e // 3600) + 1):
                ov = min(e, (h + 1) * 3600) - max(s, h * 3600)
                if ov > 0:
                    busy[h - h0] += ov
    ev.sort()  # 同時刻は終了(-1)を先に
    cur, last = 0, 0
    for t, d in ev:
        h = int(t // 3600) - h0
        if h > last:
            for k in range(last + 1, h + 1):
                if cur > hmax[k]:
                    hmax[k] = cur
            last = h
        cur += d
        if cur > hmax[h]:
            hmax[h] = cur
    return busy, hmax


_day_cache = {}


def day_info(day):
    v = _day_cache.get(day)
    if v is None:
        d = EPOCH + timedelta(days=day)
        wd = d.weekday()
        v = (d, wd, day - wd, d.strftime("%Y-%m"))
        _day_cache[day] = v
    return v  # (日付, 曜日0=月, 週の月曜の通し日, "YYYY-MM")


def weekly_table(busy, hmax, busyset, h0, jobs):
    weeks = {}
    for k in range(len(hmax)):
        _, _, wk, _ = day_info((h0 + k) // 24)
        w = weeks.get(wk)
        if w is None:
            w = weeks[wk] = {"hours": 0, "busy": 0.0, "peak": 0, "busyh": 0, "jobs": 0, "queued": []}
        w["hours"] += 1
        if busy is not None:
            w["busy"] += busy[k]
        w["peak"] = max(w["peak"], hmax[k])
        if busyset is not None and busyset[k]:
            w["busyh"] += 1
    if jobs is not None:
        for j in jobs:
            _, _, wk, _ = day_info(int(j.s // 86400))
            w = weeks.get(wk)
            if w:
                w["jobs"] += 1
                if j.queued is not None:
                    w["queued"].append(j.queued)
    out = []
    for wk in sorted(weeks):
        w = weeks[wk]
        q = sorted(w["queued"])
        out.append({
            "week": (EPOCH + timedelta(days=wk)).isoformat(),
            "jobs": w["jobs"],
            "runner_h": w["busy"] / 3600,
            "avg_conc": w["busy"] / (w["hours"] * 3600) if w["hours"] else 0,
            "peak_conc": w["peak"],
            "busy_hours": w["busyh"],
            "queued_p50_s": percentile(q, 50),
            "queued_p95_s": percentile(q, 95),
        })
    return out


# ------------------------------------------------------------------ HTML 部品
def nice_ceil(v):
    if v <= 0:
        return 1
    e = 10 ** math.floor(math.log10(v))
    for m in (1, 2, 2.5, 5, 10):
        if v <= m * e:
            return m * e
    return 10 * e


def fnum(v, d=1):
    if v is None:
        return "-"
    if abs(v) >= 100:
        return f"{v:,.0f}"
    return f"{v:,.{d}f}"


def svg_line(title, unit, labels, series, height=250, tips=None):
    """折れ線グラフ。マウスを乗せると、その週の値をツールチップで表示する。
    tips: ツールチップに出す見出し（labels と同じ長さ。省略時は labels）"""
    W, H, L, R, T, B = 920, height, 60, 16, 34, 42
    vals = [v for _, vs, _ in series for v in vs if v is not None]
    ymax = nice_ceil(max(vals) if vals else 1)
    n = len(labels)

    def x(i):
        return L + (W - L - R) * (i / (n - 1) if n > 1 else 0.5)

    def y(v):
        return T + (H - T - B) * (1 - v / ymax)

    data = {"xs": [round(x(i), 1) for i in range(n)], "top": T, "bottom": H - B,
            "labels": tips or labels, "unit": unit,
            "series": [{"name": nm, "color": c,
                        "ys": [None if v is None else round(y(v), 1) for v in vs],
                        "vals": ["-" if v is None else (f"{int(v):,}" if isinstance(v, int) or float(v).is_integer()
                                                  else fnum(v, 2 if abs(v) < 10 else 1)) for v in vs]}
                       for nm, vs, c in series]}
    p = [f'<svg viewBox="0 0 {W} {H}" class="chart" role="img" aria-label="{html.escape(title)}" '
         f'data-chart="{html.escape(json.dumps(data, ensure_ascii=False))}">',
         f'<text x="{L}" y="18" class="ttl">{html.escape(title)}</text>',
         f'<text x="{W - R}" y="18" class="ax" text-anchor="end">{html.escape(unit)}</text>']
    for g in range(5):
        v = ymax * g / 4
        yy = y(v)
        p.append(f'<line x1="{L}" x2="{W - R}" y1="{yy:.1f}" y2="{yy:.1f}" class="grid"/>')
        p.append(f'<text x="{L - 6}" y="{yy + 4:.1f}" class="ax" text-anchor="end">{fnum(v)}</text>')
    step = max(1, math.ceil(n / 10))
    for i in range(0, n, step):
        p.append(f'<text x="{x(i):.1f}" y="{H - B + 18}" class="ax" text-anchor="middle">{html.escape(labels[i])}</text>')
    lx = L
    for name, vs, color in series:
        pts = " ".join(f"{x(i):.1f},{y(v):.1f}" for i, v in enumerate(vs) if v is not None)
        p.append(f'<polyline points="{pts}" fill="none" stroke="{color}" stroke-width="2"/>')
        p.append(f'<rect x="{lx}" y="{H - 14}" width="12" height="4" fill="{color}"/>'
                 f'<text x="{lx + 16}" y="{H - 9}" class="ax">{html.escape(name)}</text>')
        lx += 30 + 13 * len(name)
    # ホバー用（JS で動かす）
    p.append(f'<line class="xh" x1="0" x2="0" y1="{T}" y2="{H - B}" visibility="hidden"/>')
    for _, _, color in series:
        p.append(f'<circle class="dot" r="4" fill="{color}" stroke="#fff" stroke-width="1.5" visibility="hidden"/>')
    p.append(f'<rect class="hit" x="{L}" y="{T}" width="{W - L - R}" height="{H - T - B}" fill="transparent"/>')
    p.append("</svg>")
    return "".join(p)


HOVER_JS = """
(function () {
  var tip = document.getElementById('tip');
  document.querySelectorAll('svg.chart[data-chart]').forEach(function (svg) {
    var d = JSON.parse(svg.getAttribute('data-chart'));
    var xh = svg.querySelector('.xh');
    var dots = svg.querySelectorAll('.dot');
    var n = d.xs.length;
    function hide() {
      tip.style.display = 'none';
      xh.setAttribute('visibility', 'hidden');
      dots.forEach(function (c) { c.setAttribute('visibility', 'hidden'); });
    }
    svg.addEventListener('mousemove', function (ev) {
      var pt = svg.createSVGPoint(); pt.x = ev.clientX; pt.y = ev.clientY;
      var px = pt.matrixTransform(svg.getScreenCTM().inverse()).x;
      var i = 0, best = Infinity;
      for (var k = 0; k < n; k++) {
        var dist = Math.abs(d.xs[k] - px);
        if (dist < best) { best = dist; i = k; }
      }
      xh.setAttribute('x1', d.xs[i]); xh.setAttribute('x2', d.xs[i]);
      xh.setAttribute('visibility', 'visible');
      var rows = '';
      d.series.forEach(function (s, k) {
        var c = dots[k], y = s.ys[i];
        if (y === null) { c.setAttribute('visibility', 'hidden'); }
        else { c.setAttribute('cx', d.xs[i]); c.setAttribute('cy', y); c.setAttribute('visibility', 'visible'); }
        rows += '<div><i style="background:' + s.color + '"></i>' + esc(s.name) +
                '<b>' + s.vals[i] + '</b></div>';
      });
      tip.innerHTML = '<div class="th">' + esc(d.labels[i]) + '</div>' + rows +
                      '<div class="tu">' + esc(d.unit) + '</div>';
      tip.style.display = 'block';
      var w = tip.offsetWidth, h = tip.offsetHeight;
      var left = ev.clientX + 14, top = ev.clientY + 14;
      if (left + w > window.innerWidth - 8) left = ev.clientX - w - 14;
      if (top + h > window.innerHeight - 8) top = ev.clientY - h - 14;
      tip.style.left = left + 'px'; tip.style.top = top + 'px';
    });
    svg.addEventListener('mouseleave', hide);
  });
  function esc(t) {
    return String(t).replace(/[&<>"]/g, function (c) {
      return {'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;'}[c];
    });
  }
})();
"""


def heatmap(cells):
    """cells[wd][hour] = 平均同時実行数"""
    mx = max((v for row in cells for v in row), default=0) or 1
    h = ['<table class="heat"><tr><th>曜日＼時</th>' + "".join(f"<th>{i}時</th>" for i in range(24)) + "</tr>"]
    for wd in range(7):
        h.append(f"<tr><th>{WEEKDAYS[wd]}</th>")
        for v in cells[wd]:
            a = v / mx
            fg = "#fff" if a > 0.55 else "inherit"
            h.append(f'<td style="background:rgba(37,99,235,{a:.2f});color:{fg}">{v:.1f}</td>')
        h.append("</tr>")
    h.append("</table>")
    return "".join(h)


def table(headers, rows, num_from=1, center=()):
    """num_from 列目以降は数値（右寄せ）。横棒は左寄せ、文字列の列は長ければ折り返す。"""
    def cls(i, c):
        if isinstance(c, str) and c.startswith("<span class='barc'"):
            return "b"
        if i in center:
            return "c"
        return "n" if i >= num_from else "t"

    def body(c):
        return c if isinstance(c, str) and c.startswith("<") else html.escape(str(c))
    h = ["<div class='tw'><table><tr>" + "".join(f"<th>{html.escape(x)}</th>" for x in headers) + "</tr>"]
    for r in rows:
        h.append("<tr>" + "".join(f"<td class='{cls(i, c)}'>{body(c)}</td>" for i, c in enumerate(r)) + "</tr>")
    h.append("</table></div>")
    return "".join(h)


def bar(pct):
    return (f"<span class='barc'><span class='bar'><span style='width:{min(100, pct):.1f}%'></span></span>"
            f"<span class='bv'>{pct:.1f}%</span></span>")


CSS = """
body{font-family:system-ui,-apple-system,"Segoe UI","Hiragino Sans","Yu Gothic UI",sans-serif;
 margin:0;background:#f7f7f5;color:#1f2328;line-height:1.6}
main{max-width:1180px;margin:0 auto;padding:24px 16px 64px}
h1{font-size:1.5rem;margin:0 0 4px}h2{font-size:1.15rem;margin:36px 0 8px;border-bottom:1px solid #ddd;padding-bottom:4px}
.sub{color:#666;font-size:.9rem}.note{color:#555;font-size:.85rem}
.kpi{display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));gap:10px;margin:16px 0}
.kpi div{background:#fff;border:1px solid #e3e3e0;border-radius:8px;padding:10px 12px}
.kpi b{display:block;font-size:1.3rem}.kpi span{font-size:.8rem;color:#666}
.chart{width:100%;height:auto;background:#fff;border:1px solid #e3e3e0;border-radius:8px;margin:8px 0}
.chart .grid{stroke:#eee}.chart .ax{font-size:11px;fill:#666}.chart .ttl{font-size:13px;font-weight:600;fill:#1f2328}
.tw{overflow-x:auto}table{border-collapse:collapse;font-size:.85rem;background:#fff}
th,td{border:1px solid #e3e3e0;padding:3px 6px}td{white-space:nowrap}th{background:#f0f0ec;text-align:left;vertical-align:bottom;font-size:.78rem;line-height:1.35;min-width:4.4em;max-width:7.5em}td.c{text-align:center}td.t{white-space:normal;overflow-wrap:anywhere;max-width:16em}
td.n{text-align:right}.heat{width:100%;table-layout:fixed}.heat th,.heat td{min-width:0;max-width:none;padding:3px 0;text-align:center;font-size:.72rem;white-space:nowrap}.heat th:first-child{width:4.2em}
.barc{display:inline-flex;align-items:center;gap:4px}.bv{min-width:3.2em;text-align:right;font-variant-numeric:tabular-nums}
.bar{display:inline-block;width:56px;height:8px;background:#eee;border-radius:4px;vertical-align:middle}
.bar span{display:block;height:100%;background:#2563eb;border-radius:4px}
.chart .xh{stroke:#9ca3af;stroke-width:1;stroke-dasharray:3 3}
.fn{color:#666;font-size:.78rem;margin:4px 0 0}
#tip{position:fixed;display:none;pointer-events:none;z-index:10;background:#fff;border:1px solid #d4d4d0;
 border-radius:6px;box-shadow:0 4px 12px rgba(0,0,0,.12);padding:6px 10px;font-size:.8rem;min-width:150px}
#tip .th{font-weight:600;margin-bottom:3px}#tip .tu{color:#888;font-size:.72rem;margin-top:2px}
#tip div{display:flex;align-items:center;gap:6px}#tip i{width:10px;height:3px;display:inline-block}
#tip b{margin-left:auto;padding-left:12px;font-variant-numeric:tabular-nums}
"""


# ------------------------------------------------------------------ メイン
def main():
    ap = argparse.ArgumentParser(description="GitLab CI ジョブ履歴の runner 占有集計")
    ap.add_argument("inputs", nargs="*", default=["jobs_*.tsv"],
                    help="入力ファイル（既定: カレントの jobs_*.tsv をすべて）")
    ap.add_argument("--tz", type=float, default=9, help="表示タイムゾーンの UTC からの時差（既定 9）")
    ap.add_argument("--capacity", type=float, help="runner の同時実行枠の合計（分かれば稼働率を出す）")
    ap.add_argument("--tags", help="タグで絞る（glob、例: '*docker*'。タグなしは ''）")
    ap.add_argument("--exclude", action="append", default=[],
                    help="削る想定のジョブ 'プロジェクトID:ジョブ名'（glob 可、複数指定可）")
    ap.add_argument("--busy-pct", type=float, default=10, help="混雑する時間帯とみなす上位 %%（既定 10）")
    ap.add_argument("--top", type=int, default=30, help="ランキングの表示件数（既定 30）")
    ap.add_argument("--since", help="この日以降に開始したジョブだけ集計（例: 2025-06-01）")
    ap.add_argument("--until", help="この日より前に開始したジョブだけ集計（例: 2026-10-01）")
    ap.add_argument("--group-by", choices=["name", "project"], default="name",
                    help="ジョブ別集計の単位。name=ジョブ名でプロジェクトをまたいで合算（既定）、project=プロジェクト+ジョブ名")
    ap.add_argument("--names", default="projects.tsv",
                    help="プロジェクト ID と名前の対応表（ID<TAB>名前）。あれば ID の代わりに名前を表示（既定 projects.tsv）")
    ap.add_argument("--project-top", type=int, default=10, help="プロジェクト別の表示件数（既定 10）")
    ap.add_argument("--merge-parallel", action="store_true",
                    help="parallel / matrix で分かれたジョブ（'rspec 1/4'、'test: [a, b]'）を 1 つにまとめる")
    ap.add_argument("--outdir", default="report")
    a = ap.parse_args()
    try:
        sys.stdout.reconfigure(errors="replace")
    except Exception:
        pass

    tzoff = a.tz * 3600
    files = expand_inputs(a.inputs)
    if not files:
        sys.exit(f"入力ファイルが見つかりません: {' '.join(a.inputs)}")
    print(f"入力: {len(files)} ファイル（{os.path.basename(files[0])} 〜 {os.path.basename(files[-1])}）")
    rows, alljobs = load(files, tzoff)
    usable = [j for j in alljobs if j.s is not None and j.e is not None and j.e > j.s]
    n_nostart = sum(1 for j in alljobs if j.s is None)
    n_running = sum(1 for j in alljobs if j.s is not None and j.e is None)
    print(f"読み込み: {rows:,} 行 / 重複除去後 {len(alljobs):,} ジョブ / 集計対象 {len(usable):,}")
    if usable:
        oldest = min(usable, key=lambda j: j.s)
        print(f"最古の開始: {day_info(int(oldest.s // 86400))[0]}（job_id {oldest.jid}）")

    # 期間で絞る（開始日時がこの範囲のジョブだけ）
    def local_sec(dstr):
        return (date.fromisoformat(dstr) - EPOCH).days * 86400
    if a.since or a.until:
        lo = local_sec(a.since) if a.since else -math.inf
        hi = local_sec(a.until) if a.until else math.inf
        before = len(usable)
        usable = [j for j in usable if lo <= j.s < hi]
        print(f"期間 {a.since or '-'} 〜 {a.until or '-'} で絞り込み: {before - len(usable):,} 件を除外")
    if not usable:
        sys.exit("集計対象のジョブがありません")

    # プロジェクト名の対応表（任意）
    pname = {}
    if a.names and os.path.isfile(a.names):
        with open(a.names, encoding="utf-8", errors="replace") as f:
            for line in f:
                c = line.rstrip("\r\n").split("\t")
                if len(c) >= 2 and c[0].strip():
                    pname[c[0].strip()] = c[1].strip()
        print(f"プロジェクト名: {a.names} から {len(pname)} 件")

    def plabel(pid):
        return pname.get(pid, pid)

    # ジョブの集計単位
    par_re = re.compile(r"( \d+/\d+|: \[.*\])$")   # parallel の " 1/4"、matrix の ": [a, b]"

    def norm(name):
        return par_re.sub("", name) if a.merge_parallel else name
    if a.group_by == "name":
        def keyfn(j):
            return ("", norm(j.name))
    else:
        def keyfn(j):
            return (j.project, norm(j.name))

    # --- タグ（runner プール）別 ---
    tagstat = defaultdict(lambda: {"sec": 0.0, "n": 0, "proj": set(), "jobs": []})
    for j in usable:
        t = tagstat[j.tags]
        t["sec"] += j.e - j.s
        t["n"] += 1
        t["proj"].add(j.project)
        t["jobs"].append(j)
    tag_total = sum(t["sec"] for t in tagstat.values())
    tag_rows = []
    for tags, t in sorted(tagstat.items(), key=lambda kv: -kv[1]["sec"]):
        share = t["sec"] / tag_total * 100
        peak = p99 = None
        if share >= 1 and len(tag_rows) < 10:
            tj = t["jobs"]
            th0 = int(min(j.s for j in tj) // 3600)
            tn = int(max(j.e for j in tj) // 3600) - th0 + 1
            _, thm = concurrency(tj, th0, tn, need_busy=False)
            s = sorted(thm)
            peak, p99 = s[-1], percentile(s, 99)
        tag_rows.append([tags or "(タグなし)", t["n"], t["sec"] / 3600, share, len(t["proj"]), peak, p99])

    if a.tags is not None:
        usable = [j for j in usable if fnmatch.fnmatchcase(j.tags, a.tags)]
        print(f"タグ '{a.tags}' で絞り込み: {len(usable):,} ジョブ")
        if not usable:
            sys.exit("該当するジョブがありません")

    # --- 全体の同時実行 ---
    h0 = int(min(j.s for j in usable) // 3600)
    n = int(max(j.e for j in usable) // 3600) - h0 + 1
    busy, hmax = concurrency(usable, h0, n)
    avgc = [b / 3600 for b in busy]
    thr = percentile(sorted(avgc), 100 - a.busy_pct) or 0
    busyset = [v >= thr and v > 0 for v in avgc]
    nb = sum(busyset)
    total_sec = sum(busy)
    busy_sec = sum(b for b, f in zip(busy, busyset) if f)
    hs = sorted(hmax)
    base = {"avg": total_sec / (n * 3600), "busy_avg": busy_sec / (nb * 3600) if nb else 0,
            "p99": percentile(hs, 99), "max": hs[-1]}

    weekly = weekly_table(busy, hmax, busyset, h0, usable)

    # 曜日×時間帯（直近 12 週）
    cells_sum = [[0.0] * 24 for _ in range(7)]
    cells_cnt = [[0] * 24 for _ in range(7)]
    for k in range(max(0, n - 12 * 7 * 24), n):
        hh = h0 + k
        _, wd, _, _ = day_info(hh // 24)
        cells_sum[wd][hh % 24] += avgc[k]
        cells_cnt[wd][hh % 24] += 1
    cells = [[cells_sum[w][h] / cells_cnt[w][h] if cells_cnt[w][h] else 0 for h in range(24)] for w in range(7)]

    # --- ジョブ別 ---
    stats = {}
    for j in usable:
        k = keyfn(j)
        st = stats.get(k)
        if st is None:
            st = stats[k] = {"sec": 0.0, "busy": 0.0, "n": 0, "fail": 0, "durs": [], "projsec": Counter(),
                             "stage": Counter(), "months": defaultdict(float)}
        d = j.e - j.s
        st["sec"] += d
        st["n"] += 1
        st["durs"].append(d)
        st["stage"][j.stage] += 1
        if j.status == "failed":
            st["fail"] += 1
        st["projsec"][j.project] += j.e - j.s
        st["months"][day_info(int(j.s // 86400))[3]] += d
        s, e = j.s, j.e
        for h in range(int(s // 3600), int(e // 3600) + 1):
            if busyset[h - h0]:
                ov = min(e, (h + 1) * 3600) - max(s, h * 3600)
                if ov > 0:
                    st["busy"] += ov
    ranking = sorted(stats.items(), key=lambda kv: -kv[1]["busy"])

    # --- プロジェクト別 ---
    week_idx = {w["week"]: i for i, w in enumerate(weekly)}
    pstats = {}
    for j in usable:
        ps = pstats.get(j.project)
        if ps is None:
            ps = pstats[j.project] = {"sec": 0.0, "busy": 0.0, "n": 0, "names": set(), "jobs": [],
                                      "weekly": [0.0] * len(weekly), "months": defaultdict(float)}
        d = j.e - j.s
        ps["sec"] += d
        ps["n"] += 1
        ps["names"].add(j.name)
        ps["jobs"].append(j)
        _, _, wk, mon = day_info(int(j.s // 86400))
        i = week_idx.get((EPOCH + timedelta(days=wk)).isoformat())
        if i is not None:
            ps["weekly"][i] += d / 3600
        ps["months"][mon] += d
        s, e = j.s, j.e
        for h in range(int(s // 3600), int(e // 3600) + 1):
            if busyset[h - h0]:
                ov = min(e, (h + 1) * 3600) - max(s, h * 3600)
                if ov > 0:
                    ps["busy"] += ov
    pranking = sorted(pstats.items(), key=lambda kv: -kv[1]["busy"])
    proj_rows = []
    for rank, (pid, ps) in enumerate(pranking):
        peak = p99 = None
        if rank < a.project_top:
            _, phm = concurrency(ps["jobs"], h0, n, need_busy=False)
            sp = sorted(phm)
            peak, p99 = sp[-1], percentile(sp, 99)
        avg = ps["sec"] / (n * 3600)                      # 期間平均の同時実行数
        busy_avg = ps["busy"] / (nb * 3600) if nb else 0  # 混雑時間帯の平均同時実行数
        proj_rows.append({"pid": pid, "name": plabel(pid), "n": ps["n"], "jobs": len(ps["names"]),
                          "hours": ps["sec"] / 3600, "share": ps["sec"] / total_sec * 100,
                          "busy_share": ps["busy"] / busy_sec * 100 if busy_sec else 0,
                          "avg": avg, "busy_avg": busy_avg, "p99": p99, "peak": peak,
                          "weekly": ps["weekly"], "months": ps["months"]})

    # --- what-if（--exclude）---
    whatif = None
    if a.exclude:
        def hit(j):
            return any(fnmatch.fnmatchcase(f"{j.project}:{j.name}", p) for p in a.exclude)
        removed = [j for j in usable if hit(j)]
        kept = [j for j in usable if not hit(j)]
        b2, hm2 = concurrency(kept, h0, n)
        s2 = sorted(hm2)
        w2 = weekly_table(b2, hm2, None, h0, None)
        whatif = {
            "keys": sorted({f"{j.project}:{j.name}" for j in removed}),
            "n_removed": len(removed),
            "avg": sum(b2) / (n * 3600),
            "busy_avg": sum(b for b, f in zip(b2, busyset) if f) / (nb * 3600) if nb else 0,
            "p99": percentile(s2, 99), "max": s2[-1], "weekly": w2,
            "saved_h": sum(j.e - j.s for j in removed) / 3600,
        }

    # ------------------------------------------------------------- CSV
    os.makedirs(a.outdir, exist_ok=True)

    def wcsv(name, headers, rows_):
        with open(os.path.join(a.outdir, name), "w", encoding="utf-8-sig", newline="") as f:
            w = csv.writer(f)
            w.writerow(headers)
            w.writerows(rows_)

    wcsv("weekly.csv", ["週(月曜)", "ジョブ数", "占有時間h", "平均同時実行", "最大同時実行",
                        "混雑する時間帯の時間数", "待ちp50秒", "待ちp95秒"],
         [[w["week"], w["jobs"], round(w["runner_h"], 2), round(w["avg_conc"], 3), w["peak_conc"],
           w["busy_hours"], w["queued_p50_s"], w["queued_p95_s"]] for w in weekly])
    wcsv("tags.csv", ["タグ", "ジョブ数", "占有時間h", "割合%", "プロジェクト数", "最大同時実行", "同時実行p99"],
         [[r[0], r[1], round(r[2], 2), round(r[3], 2), r[4], r[5], r[6]] for r in tag_rows])
    rank_rows = []
    for (_, name), st in ranking:
        ds = sorted(st["durs"])
        rank_rows.append([name, st["stage"].most_common(1)[0][0], len(st["projsec"]), st["n"],
                          round(st["sec"] / 3600, 2), round(st["sec"] / total_sec * 100, 2),
                          round(st["busy"] / 3600, 2), round(st["busy"] / busy_sec * 100, 2) if busy_sec else 0,
                          round(st["sec"] / st["n"] / 60, 2), round(percentile(ds, 95) / 60, 2),
                          round(st["fail"] / st["n"] * 100, 1)])
    wcsv("jobs_ranking.csv", ["ジョブ名", "ステージ", "プロジェクト数", "回数", "占有時間h",
                              "全時間帯に占める割合%", "混雑する時間帯の占有時間h", "混雑する時間帯に占める割合%",
                              "平均分", "p95分", "failed率%"], rank_rows)
    months = sorted({m for st in stats.values() for m in st["months"]})
    top_total = sorted(stats.items(), key=lambda kv: -kv[1]["sec"])[:a.top]
    wcsv("jobs_monthly.csv", ["ジョブ名", "プロジェクト数"] + months,
         [[nm, len(st["projsec"])] + [round(st["months"].get(m, 0) / 3600, 2) for m in months]
          for (_, nm), st in top_total])
    pcols = ["プロジェクトID", "プロジェクト", "回数", "ジョブ種類", "占有時間h", "全時間帯に占める割合%", "混雑する時間帯に占める割合%",
             "平均同時実行", "混雑する時間帯の平均同時実行", "同時実行p99", "最大同時実行"]
    if a.capacity:
        pcols += ["平均稼働率%", "混雑する時間帯の稼働率%"]
    prow = []
    for r in proj_rows:
        x = [r["pid"], r["name"], r["n"], r["jobs"], round(r["hours"], 2), round(r["share"], 2),
             round(r["busy_share"], 2), round(r["avg"], 3), round(r["busy_avg"], 3), r["p99"], r["peak"]]
        if a.capacity:
            x += [round(r["avg"] / a.capacity * 100, 2), round(r["busy_avg"] / a.capacity * 100, 2)]
        prow.append(x)
    wcsv("projects.csv", pcols, prow)
    wcsv("projects_monthly.csv", ["プロジェクトID", "プロジェクト"] + months,
         [[r["pid"], r["name"]] + [round(r["months"].get(m, 0) / 3600, 2) for m in months] for r in proj_rows])
    if whatif:
        wcsv("whatif_weekly.csv", ["週(月曜)", "占有時間h(現状)", "占有時間h(削減後)",
                                   "平均同時実行(現状)", "平均同時実行(削減後)", "最大同時実行(現状)", "最大同時実行(削減後)"],
             [[w["week"], round(w["runner_h"], 2), round(v["runner_h"], 2), round(w["avg_conc"], 3),
               round(v["avg_conc"], 3), w["peak_conc"], v["peak_conc"]] for w, v in zip(weekly, whatif["weekly"])])

    # ------------------------------------------------------------- HTML
    labels = [datetime.fromisoformat(w["week"]).strftime("%y/%m/%d") for w in weekly]
    wtips = []
    for w in weekly:
        d0 = date.fromisoformat(w["week"])
        wtips.append(f"{d0:%Y/%m/%d}（月）〜 {d0 + timedelta(days=6):%m/%d}（日）")
    first = (EPOCH + timedelta(days=h0 // 24)).isoformat()
    last = (EPOCH + timedelta(days=(h0 + n - 1) // 24)).isoformat()
    cap = a.capacity
    footnote = (f"<p class='fn'>※混雑する時間帯：1 時間ごとの runner 稼働（平均同時実行数）が多い時間のうち、"
                f"上位 {a.busy_pct:g}% の時間</p>")
    H = [f"<!doctype html><html lang='ja'><head><meta charset='utf-8'>"
         f"<meta name='viewport' content='width=device-width,initial-scale=1'><title>CI Runner 占有レポート</title>"
         f"<style>{CSS}</style></head><body><main>",
         "<h1>CI Runner 占有レポート</h1>",
         f"<div class='sub'>{first} 〜 {last}（UTC{a.tz:+g}）"
         f"{' / タグ: ' + html.escape(a.tags or '(タグなし)') if a.tags is not None else ''}</div>"]

    kpis = [("集計対象ジョブ", f"{len(usable):,}"),
            ("総占有時間", f"{fnum(total_sec / 3600)} h"),
            ("平均同時実行数", fnum(base["avg"], 2)),
            ("混雑する時間帯※の平均同時実行数", fnum(base["busy_avg"], 2)),
            ("同時実行数 p99 / 最大", f"{base['p99']} / {base['max']}")]
    if cap:
        kpis.append(("平均稼働率", f"{base['avg'] / cap * 100:.1f}%"))
        kpis.append(("混雑する時間帯※の稼働率", f"{base['busy_avg'] / cap * 100:.1f}%"))
    H.append("<div class='kpi'>" + "".join(f"<div><span>{k}</span><b>{v}</b></div>" for k, v in kpis) + "</div>")
    H.append(footnote)

    H.append("<h2>1. 週別の推移</h2>")
    H.append(svg_line("週あたりの runner 占有時間", "時間/週", labels,
                      [("現状", [w["runner_h"] for w in weekly], "#2563eb")]
                      + ([("削減後", [w["runner_h"] for w in whatif["weekly"]], "#f59e0b")] if whatif else []), tips=wtips))
    conc_series = [("最大", [w["peak_conc"] for w in weekly], "#dc2626"),
                   ("平均", [w["avg_conc"] for w in weekly], "#2563eb")]
    if cap:
        conc_series.append(("枠", [cap] * len(weekly), "#9ca3af"))
    H.append(svg_line("同時実行数", "ジョブ数", labels, conc_series, tips=wtips))
    H.append(svg_line("runner に拾われるまでの待ち時間", "秒",
                      labels, [("p95", [w["queued_p95_s"] for w in weekly], "#dc2626"),
                               ("p50", [w["queued_p50_s"] for w in weekly], "#2563eb")], tips=wtips))

    H.append("<h2>2. 曜日 × 時刻の平均同時実行数（直近 12 週）</h2>")
    H.append(heatmap(cells))

    H.append("<h2>3. タグ（runner プール）別</h2>")
    H.append(table(["タグ", "ジョブ数", "占有時間h", "割合", "プロジェクト数", "最大同時", "同時p99"],
                   [[r[0], f"{r[1]:,}", fnum(r[2]), bar(r[3]), r[4], r[5] if r[5] is not None else "-",
                     r[6] if r[6] is not None else "-"] for r in tag_rows[:15]]))

    H.append(f"<h2>4. プロジェクト別（混雑する時間帯に占める割合の順・上位 {a.project_top}）</h2>")
    ptop = proj_rows[:a.project_top]
    palette = ["#2563eb", "#dc2626", "#16a34a", "#f59e0b", "#7c3aed"]
    H.append(svg_line("上位 5 プロジェクトの週あたり占有時間", "時間/週", labels,
                      [(r["name"][-24:], r["weekly"], palette[i]) for i, r in enumerate(ptop[:5])], tips=wtips))
    ph = ["プロジェクト", "回数", "ジョブ種類", "占有h", "全時間帯に占める割合", "混雑する時間帯※に占める割合", "平均同時", "混雑する時間帯※の平均同時", "同時p99", "最大同時"]
    if cap:
        ph += ["稼働率", "混雑する時間帯※の稼働率"]
    prows_html = []
    for r in ptop:
        x = [r["name"], f"{r['n']:,}", r["jobs"], fnum(r["hours"]), f"{r['share']:.1f}%", bar(r["busy_share"]),
             fnum(r["avg"], 2), fnum(r["busy_avg"], 2), r["p99"], r["peak"]]
        if cap:
            x += [f"{r['avg'] / cap * 100:.1f}%", f"{r['busy_avg'] / cap * 100:.1f}%"]
        prows_html.append(x)
    H.append(table(ph, prows_html, center={4}))
    H.append(footnote)

    H.append(f"<h2>5. ジョブ別の占有（混雑する時間帯に占める割合の順・上位 {a.top}）</h2>")
    H.append(table(["ジョブ名", "ステージ", "プロジェクト数", "回数", "占有h", "全時間帯に占める割合",
                    "混雑する時間帯※に占める割合", "平均分", "p95分", "failed率"],
                   [[r[0], r[1], r[2], f"{r[3]:,}", fnum(r[4]), f"{r[5]:.1f}%", bar(r[7]), fnum(r[8]), fnum(r[9]),
                     f"{r[10]:.0f}%"] for r in rank_rows[:a.top]], num_from=2, center={5}))
    H.append(footnote)

    if whatif:
        H.append("<h2>6. 指定したジョブを削った場合（--exclude）</h2>")
        H.append(f"<p class='note'>指定: {html.escape(' , '.join(a.exclude))} → 該当 {len(whatif['keys'])} 種類・{whatif['n_removed']:,} 件</p>")
        rows6 = [["総占有時間h", fnum(total_sec / 3600), fnum(total_sec / 3600 - whatif["saved_h"])],
                 ["平均同時実行数", fnum(base["avg"], 2), fnum(whatif["avg"], 2)],
                 ["混雑する時間帯※の平均同時実行数", fnum(base["busy_avg"], 2), fnum(whatif["busy_avg"], 2)],
                 ["同時実行数 p99", base["p99"], whatif["p99"]],
                 ["最大同時実行数", base["max"], whatif["max"]]]
        if cap:
            rows6.append(["混雑する時間帯※の稼働率", f"{base['busy_avg'] / cap * 100:.1f}%", f"{whatif['busy_avg'] / cap * 100:.1f}%"])
        H.append(table(["指標", "現状", "削減後"], rows6))
        H.append(footnote)
        if whatif["keys"]:
            H.append("<p class='note'>該当ジョブ: " + html.escape(", ".join(whatif["keys"][:50]))
                     + (" …" if len(whatif["keys"]) > 50 else "") + "</p>")

    H.append(f"</main><div id='tip'></div><script>{HOVER_JS}</script></body></html>")
    with open(os.path.join(a.outdir, "report.html"), "w", encoding="utf-8") as f:
        f.write("".join(H))

    # ------------------------------------------------------------- 画面には要点だけ
    print(f"期間 {first} 〜 {last}  総占有 {total_sec / 3600:,.0f} h  平均同時 {base['avg']:.2f}  "
          f"混雑する時間帯の平均同時 {base['busy_avg']:.2f}  最大 {base['max']}")
    if whatif:
        print(f"削減後: 混雑する時間帯の平均同時 {whatif['busy_avg']:.2f}  最大 {whatif['max']}  削減 {whatif['saved_h']:,.0f} h")
    print(f"出力: {os.path.join(a.outdir, 'report.html')}")


if __name__ == "__main__":
    main()
