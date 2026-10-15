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
  python ci_usage.py --pool-re "-[0-9a-z]+-[0-9a-z]{5}$"  # runner 名から除く部分を自分で指定
  （runners.tsv があれば、runner の所属グループをプールにする。make_runners.sh で作成）

出力（--outdir、既定 report/）:
  report.html        グラフと表のまとめ（ブラウザで開く）
  weekly.csv         週別の占有時間・同時実行数・待ち時間
  jobs_ranking.csv   ジョブ別の占有時間（混雑する時間帯に占める割合の順）
  jobs_monthly.csv   上位ジョブの月別占有時間
  projects.csv       プロジェクト別の占有時間・同時実行数（・稼働率）
  projects_monthly.csv  プロジェクト別の月別占有時間
  tags.csv           タグ別の占有時間
  pools.csv          runner プール（runner 名からハッシュを除いたもの）別の占有・上限・待ち時間
  job_runner.csv     ジョブごとの runner と所属グループ（プール別の期間のみ）
  pipeline_runner.csv  パイプラインごとに、どのプールでジョブが何本実行されたか
  project_pool.csv   プロジェクト × プールのジョブ数
  pool_map.csv       runner 名・runner ID → プールの対応（ハッシュの除き方の確認用）
  pool_over_limit.csv  プールの上限の目安を超えた時間と、そのとき動いていた runner ID
  team_pool.csv      チーム（プロジェクトのサブグループ）× プールの使用割合
  coverage_weekly.csv  週ごとの「runner・プロジェクト名がそろっているジョブ」の割合（プール別の期間の根拠）
  wait_by_project.csv  待ち時間の長いジョブのプロジェクト内訳
  whatif_weekly.csv  --exclude を指定したときの週別比較

考え方:
  - 占有時間 = ジョブの started_at〜finished_at の合計（runner の枠を埋めていた時間）
  - 同時実行数 = その時点で走っていたジョブ数
  - 混雑する時間帯 = 1 時間平均の同時実行数が全時間の上位 --busy-pct %（既定 10%）の時間
  - プール = runner の所属グループ（runners.tsv があるとき）。
    runners.tsv にない runner は、runner 名の末尾のハッシュを除いたもの。
    runner が登録し直されて名前や ID が変わっても、同じプールとしてまとめる
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
EPOCH_DT = datetime(1970, 1, 1)
WEEKDAYS = ["月", "火", "水", "木", "金", "土", "日"]


# ------------------------------------------------------------------ 読み込み
class Job:
    __slots__ = ("jid", "pipeline", "project", "name", "stage", "status", "s", "e", "queued", "tags",
                 "runner_id", "runner", "pool")


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
            j.runner_id = r["runner_id"].strip()
            j.runner = r["runner"].strip()
            j.pipeline = r["pipeline_id"].strip()
            j.pool = ""
            old = best.get(jid)
            # 同じジョブが複数期間に出たら、終了しているほうを採用
            if old is None or (old.e is None and j.e is not None):
                best[jid] = j
    return rows


# ------------------------------------------------------------------ runner プール
_SEG_RE = re.compile(r"[-_. ]([0-9A-Za-z]{5,})$")
_K8S_RAND = re.compile(r"[bcdfghjklmnpqrstvwxz2456789]{5,10}")   # Pod 名のランダム部分に使われる文字


def _looks_like_hash(seg):
    if _K8S_RAND.fullmatch(seg):
        return True
    if re.fullmatch(r"[0-9a-fA-F]{8,}", seg):
        return True
    if re.fullmatch(r"[A-Za-z]+\d{1,3}", seg):        # team1、runner01 のような名前は残す
        return False
    # 英字と数字が入り混じった 8 文字以上（トークン・ランダム ID など）
    return len(seg) >= 8 and len(re.findall(r"\d", seg)) >= 2 and len(re.findall(r"[A-Za-z]", seg)) >= 2


def make_pool_fn(pool_re):
    """runner 名 → プール名。pool_re があればその部分を消す。なければ末尾のハッシュらしい部分を最大 3 つ消す。"""
    if pool_re:
        rx = re.compile(pool_re)

        def fn(name):
            if not name:
                return "(runner 不明)"
            return rx.sub("", name).strip() or name
        return fn

    def fn(name):
        if not name:
            return "(runner 不明)"
        s = name.strip()
        for _ in range(3):
            m = _SEG_RE.search(s)
            if not m or not _looks_like_hash(m.group(1)):
                break
            s = s[:m.start()]
        return s or name
    return fn


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


def time_at_limit(jobs, limit, h0, n):
    """同時実行数が limit 以上だった時間（秒）を 1 時間ごとに返す。"""
    sec = [0.0] * n
    ev = sorted([(j.s, 1) for j in jobs] + [(j.e, -1) for j in jobs])
    cur, prev = 0, None
    for t, d in ev:
        if prev is not None and cur >= limit and t > prev:
            x = prev
            while x < t:
                h = int(x // 3600)
                y = min(t, (h + 1) * 3600)
                sec[h - h0] += y - x
                x = y
        cur += d
        prev = t
    return sec


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
    vmax = max(vals) if vals and max(vals) > 0 else 1
    # 目盛り間隔は 1・2・5 の系列から、目盛りが 6 本以内になる最小のもの
    e = 10 ** math.floor(math.log10(vmax)) / 10
    step = next(m * e * 10 ** k for k in range(3) for m in (1, 2, 5) if math.ceil(vmax / (m * e * 10 ** k)) <= 6)
    ticks = max(2, math.ceil(vmax / step))
    ymax = step * ticks
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
    for g in range(ticks + 1):
        v = step * g
        yy = y(v)
        p.append(f'<line x1="{L}" x2="{W - R}" y1="{yy:.1f}" y2="{yy:.1f}" class="grid"/>')
        p.append(f'<text x="{L - 6}" y="{yy + 4:.1f}" class="ax" text-anchor="end">{f"{v:,.0f}" if float(step).is_integer() else f"{v:g}"}</text>')
    step = max(1, math.ceil(n / 10))
    for i in range(0, n, step):
        p.append(f'<text x="{x(i):.1f}" y="{H - B + 18}" class="ax" text-anchor="middle">{html.escape(labels[i])}</text>')
    lx = L
    for name, vs, color in series:
        # None で線を切る（データのない週をまたいで線を引かない）
        segs, cur = [], []
        for i, v in enumerate(vs):
            if v is None:
                if cur:
                    segs.append(cur)
                cur = []
            else:
                cur.append(f"{x(i):.1f},{y(v):.1f}")
        if cur:
            segs.append(cur)
        for sg in segs:
            if len(sg) == 1:
                cx, cy = sg[0].split(",")
                p.append(f'<circle cx="{cx}" cy="{cy}" r="2" fill="{color}"/>')
            else:
                p.append(f'<polyline points="{" ".join(sg)}" fill="none" stroke="{color}" stroke-width="2"/>')
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
h3{font-size:1rem;margin:20px 0 6px}
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
    ap.add_argument("--wait-weeks", type=int, default=13,
                    help="待ち時間のプロジェクト内訳を見る期間（直近の週数、既定 13 ≒ 3 か月。0 で全期間）")
    ap.add_argument("--project-top", type=int, default=10, help="プロジェクト別の表示件数（既定 10）")
    ap.add_argument("--merge-parallel", action="store_true",
                    help="parallel / matrix で分かれたジョブ（'rspec 1/4'、'test: [a, b]'）を 1 つにまとめる")
    ap.add_argument("--pool-re",
                    help="runner 名から消す部分の正規表現（例: '-[0-9a-z]+-[0-9a-z]{5}$'）。"
                         "省略時は末尾のハッシュらしい部分を自動で除く")
    ap.add_argument("--runners", default="runners.tsv",
                    help="runner ID と所属グループの対応表（make_runners.sh で作成、既定 runners.tsv）。"
                         "あれば所属グループをプールにする")
    ap.add_argument("--pool-since",
                    help="プール別（5 章）の集計開始日（例: 2026-08-01）。"
                         "省略時は runner・プロジェクト名の情報がそろっている直近の期間を自動で選ぶ")
    ap.add_argument("--pool-coverage", type=float, default=80,
                    help="自動で選ぶとき、runner・プロジェクト名がそろっているジョブの割合が何 %% 以上の週を使うか（既定 80）")
    ap.add_argument("--pool-limit-weeks", type=int, default=3,
                    help="プールの上限の目安 = 週ごとの最大同時実行数の上から N 番目（既定 3。1 回だけの突出を除くため）")
    ap.add_argument("--teams", default="teams.csv",
                    help="プロジェクト ID とチーム名の対応表（ID,チーム名 の CSV。既定 teams.csv）。"
                         "あればプロジェクトのパスより優先してチーム名に使う")
    ap.add_argument("--team-depth", type=int, default=2,
                    help="プロジェクトのパスの何階層目までをチームとみなすか（既定 2: mygroup/team1）")
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
    pool_of = make_pool_fn(a.pool_re)
    rgroup = {}
    if a.runners and os.path.isfile(a.runners):
        with open(a.runners, encoding="utf-8", errors="replace") as f:
            for line in f:
                c = line.rstrip("\r\n").split("\t")
                if len(c) >= 3 and c[0].strip() and c[1] != "error":
                    kind = {"group_type": "group", "project_type": "project",
                            "instance_type": "instance"}.get(c[1], c[1])
                    rgroup[c[0].strip()] = f"{c[2]}（{kind}）" if c[2] else f"（{kind}）"
        print(f"runner の所属: {a.runners} から {len(rgroup)} 件")
    n_by = Counter()
    for j in alljobs:
        if j.runner_id and j.runner_id in rgroup:
            j.pool = rgroup[j.runner_id]
            n_by["所属グループ"] += 1
        elif not j.runner_id and not j.runner:
            j.pool = "(runner 不明)"
            n_by["runner 情報なし"] += 1
        else:
            j.pool = pool_of(j.runner) if j.runner else f"(所属未取得 #{j.runner_id})"
            n_by["runner 名から推定"] += 1
    by_txt = " / ".join(f"{k} {v:,} 件" for k, v in n_by.most_common())
    print("プールの決め方: " + by_txt)
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

    # 表示名はパスの最後（例: group/sub/web-app → web-app）。同じ名前が複数あるときだけ 1 つ上の階層も付ける
    short = {pid: path.split("/")[-1] for pid, path in pname.items()}
    for depth in range(2, 10):
        dup = Counter(short.values())
        if all(c == 1 for c in dup.values()):
            break
        for pid, path in pname.items():
            if dup[short[pid]] > 1:
                short[pid] = "/".join(path.split("/")[-depth:])

    def plabel(pid):
        return short.get(pid, pid)

    # チーム名の対応表（任意）。ID,チーム名 の CSV。1 行目が見出しでも、BOM 付きでも読める
    tname = {}
    if a.teams and os.path.isfile(a.teams):
        with open(a.teams, encoding="utf-8-sig", errors="replace", newline="") as f:
            for row in csv.reader(f):
                if len(row) >= 2 and row[0].strip().isdigit() and row[1].strip():
                    tname[row[0].strip()] = row[1].strip()
        print(f"チーム名: {a.teams} から {len(tname)} 件")

    def team_of(pid):
        """チーム名。teams.csv にあればその名前、なければプロジェクトのパスから取る（mygroup/team1 など）。"""
        if pid in tname:
            return tname[pid]
        path = pname.get(pid)
        if not path:
            return "(名前不明)"
        parts = path.split("/")
        if len(parts) > a.team_depth:
            return "/".join(parts[:a.team_depth])
        return "/".join(parts[:-1]) or path

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

    # --- タグ別 ---
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
    week_idx = {w["week"]: i for i, w in enumerate(weekly)}

    def widx_of_hour(k):
        return week_idx[(EPOCH + timedelta(days=day_info((h0 + k) // 24)[2])).isoformat()]

    def widx_of_job(j):
        return week_idx.get((EPOCH + timedelta(days=day_info(int(j.s // 86400))[2])).isoformat())

    def busy_overlap(j):
        """ジョブのうち混雑する時間帯に重なっていた秒数"""
        t = 0.0
        for h in range(int(j.s // 3600), int(j.e // 3600) + 1):
            if busyset[h - h0]:
                ov = min(j.e, (h + 1) * 3600) - max(j.s, h * 3600)
                if ov > 0:
                    t += ov
        return t

    # 同時実行数が上限に達していた時間（上限 = --capacity、なければ期間中の最大値）
    limit = int(a.capacity) if a.capacity else base["max"]
    at_lim = time_at_limit(usable, limit, h0, n)
    for w in weekly:
        w["limit_min"] = 0.0
    for k in range(n):
        if at_lim[k]:
            weekly[widx_of_hour(k)]["limit_min"] += at_lim[k] / 60

    # --- 待ち時間の長いジョブのプロジェクト内訳（直近 --wait-weeks 週）---
    wait_from = (h0 + n) * 3600 - a.wait_weeks * 7 * 86400 if a.wait_weeks else -math.inf
    wjobs = [j for j in usable if j.s >= wait_from and j.queued is not None]
    wq = sorted(j.queued for j in wjobs)
    wait_thr = percentile(wq, 95) or 0
    wstat = defaultdict(lambda: {"n": 0, "long": 0, "q": []})
    for j in wjobs:
        ws = wstat[j.project]
        ws["n"] += 1
        ws["q"].append(j.queued)
        if j.queued > wait_thr:
            ws["long"] += 1
    n_long = sum(ws["long"] for ws in wstat.values())
    wait_rows = []
    for pid, ws in sorted(wstat.items(), key=lambda kv: -kv[1]["long"]):
        q = sorted(ws["q"])
        wait_rows.append({"pid": pid, "name": plabel(pid), "n": ws["n"], "long": ws["long"],
                          "p50": percentile(q, 50), "p95": percentile(q, 95),
                          "long_share": ws["long"] / n_long * 100 if n_long else 0,
                          "job_share": ws["n"] / len(wjobs) * 100 if wjobs else 0})
    wait_period = (f"{(EPOCH + timedelta(days=int(max(wait_from, h0 * 3600) // 86400))):%Y/%m/%d} 〜 "
                   f"{(EPOCH + timedelta(days=(h0 + n - 1) // 24)):%Y/%m/%d}")
    recent_k0 = max(0, int(wait_from // 3600) - h0) if wait_from != -math.inf else 0

    # --- runner プール別（runner・プロジェクト名の情報がそろっている直近の期間だけ）---
    def known(j):
        return bool(j.runner_id or j.runner) and ((not pname and not tname) or j.project in pname or j.project in tname)
    wk_tot, wk_ok = [0] * len(weekly), [0] * len(weekly)
    for j in usable:
        i = widx_of_job(j)
        if i is not None:
            wk_tot[i] += 1
            if known(j):
                wk_ok[i] += 1
    if a.pool_since:
        pool_from = local_sec(a.pool_since)
    else:
        # 末尾の週からさかのぼり、そろっている割合が --pool-coverage % を下回る週の手前までを使う
        start_i = len(weekly)
        for i in range(len(weekly) - 1, -1, -1):
            if wk_tot[i] == 0:
                continue
            if wk_ok[i] / wk_tot[i] * 100 >= a.pool_coverage:
                start_i = i
            else:
                break
        if start_i >= len(weekly):
            start_i = max(0, len(weekly) - 2)
            print(f"注意: runner・プロジェクト名がそろっている割合が {a.pool_coverage:g}% 以上の週がないため、"
                  "直近 2 週でプール別を集計します")
        pool_from = local_sec(weekly[start_i]["week"])
    pool_w0 = next((i for i, w in enumerate(weekly) if local_sec(w["week"]) + 7 * 86400 > pool_from),
                   len(weekly) - 1)
    pool_k0 = max(0, int(pool_from // 3600) - h0)
    pool_usable = [j for j in usable if j.s >= pool_from]
    pool_total = sum(j.e - j.s for j in pool_usable) or 1
    nb_pool = sum(busyset[pool_k0:])
    pq = sorted(j.queued for j in pool_usable if j.queued is not None)
    pool_wait_thr = percentile(pq, 95) or 0
    n_long_pool = sum(1 for q in pq if q > pool_wait_thr)
    pool_cov = sum(1 for j in pool_usable if known(j)) / len(pool_usable) * 100 if pool_usable else 0
    pool_period = (f"{(EPOCH + timedelta(days=int(max(pool_from, h0 * 3600) // 86400))):%Y/%m/%d} 〜 "
                   f"{(EPOCH + timedelta(days=(h0 + n - 1) // 24)):%Y/%m/%d}")
    print(f"プール別の集計期間: {pool_period}（{len(pool_usable):,} ジョブ、"
          f"runner・プロジェクト名がそろっている割合 {pool_cov:.0f}%）")

    if tname:
        miss = Counter(j.project for j in pool_usable if j.project not in tname)
        if miss:
            print(f"注意: teams.csv にないプロジェクト {len(miss)} 件（この期間の {sum(miss.values()):,} ジョブ）: "
                  + ", ".join(f"{plabel(pid)}({pid})" for pid, _ in miss.most_common(10))
                  + (" …" if len(miss) > 10 else ""))

    pjobs = defaultdict(list)
    for j in pool_usable:
        pjobs[j.pool].append(j)
    pool_rows, over_rows = [], []
    for pool, pj in sorted(pjobs.items(), key=lambda kv: -sum(j.e - j.s for j in kv[1])):
        sec = sum(j.e - j.s for j in pj)
        _, phm = concurrency(pj, h0, n, need_busy=False)
        wpk = [0] * len(weekly)
        for k, v in enumerate(phm):
            if v:
                i = widx_of_hour(k)
                if v > wpk[i]:
                    wpk[i] = v
        sp = sorted((v for v in wpk[pool_w0:] if v), reverse=True)
        lim = sp[min(a.pool_limit_weeks, len(sp)) - 1] if sp else 0
        atl = time_at_limit(pj, lim, h0, n) if lim else [0.0] * n
        bsec = sum(busy_overlap(j) for j in pj)
        bavg = bsec / (nb_pool * 3600) if nb_pool else 0
        rq = sorted(j.queued for j in pj if j.queued is not None)
        nlong = sum(1 for q in rq if q > pool_wait_thr)
        wq_ = [[] for _ in weekly]
        for j in pj:
            if j.queued is not None:
                i = widx_of_job(j)
                if i is not None:
                    wq_[i].append(j.queued)
        hsp = sorted(phm)
        ids = {j.runner_id for j in pj if j.runner_id}
        pool_rows.append({
            "pool": pool, "n": len(pj), "ids": len(ids), "hours": sec / 3600, "share": sec / pool_total * 100,
            "peak": hsp[-1], "p99": percentile(hsp, 99), "lim": lim,
            "busy_avg": bavg, "fill": bavg / lim * 100 if lim else 0,
            "at_lim_min": sum(atl[pool_k0:]) / 60,
            "p50": percentile(rq, 50), "p95": percentile(rq, 95),
            "long": nlong, "long_share": nlong / n_long_pool * 100 if n_long_pool else 0,
            "wpeak": wpk[pool_w0:], "wp95": [percentile(sorted(x), 95) if x else None for x in wq_[pool_w0:]],
        })
        # 上限の目安を超えた時間：そのとき動いていた runner ID を記録（新旧 runner の重なりの確認用）
        if lim:
            over = {k: v for k, v in enumerate(phm) if v > lim}
            if over:
                act = defaultdict(set)
                for j in pj:
                    for h in range(int(j.s // 3600), int(j.e // 3600) + 1):
                        if h - h0 in over:
                            act[h - h0].add(j.runner_id or "?")
                for k, v in over.items():
                    over_rows.append([pool, (EPOCH_DT + timedelta(hours=h0 + k)).strftime("%Y-%m-%d %H:00"),
                                      v, lim, len(act[k]), " ".join(sorted(act[k], key=lambda x: (len(x), x)))])
    over_rows.sort(key=lambda r: (-r[2], r[1]))

    # runner 名 → プールの対応（runner ごとの稼働期間も出す。入れ替わりの間隔の確認用）
    rmap = {}
    for j in usable:
        key = (j.pool, j.runner, j.runner_id)
        m = rmap.get(key)
        if m is None:
            rmap[key] = [1, j.s, j.e]
        else:
            m[0] += 1
            m[1] = min(m[1], j.s)
            m[2] = max(m[2], j.e)

    def dts(sec):
        return (EPOCH_DT + timedelta(seconds=sec)).strftime("%Y-%m-%d %H:%M")
    map_rows = sorted([[p, r, rid, c, dts(s), dts(e), round((e - s) / 86400, 1)]
                       for (p, r, rid), (c, s, e) in rmap.items()], key=lambda r: (r[0], r[4]))

    # チーム × プール（プール別と同じ期間）
    top_pools = [r["pool"] for r in pool_rows[:6]]
    tstat = defaultdict(lambda: {"n": 0, "sec": 0.0, "q": [], "long": 0, "pool": Counter()})
    for j in pool_usable:
        ts = tstat[team_of(j.project)]
        ts["n"] += 1
        ts["sec"] += j.e - j.s
        ts["pool"][j.pool] += j.e - j.s
        if j.queued is not None:
            ts["q"].append(j.queued)
            if j.queued > pool_wait_thr:
                ts["long"] += 1
    team_rows = []
    for team, ts in sorted(tstat.items(), key=lambda kv: -kv[1]["sec"]):
        q = sorted(ts["q"])
        other = ts["sec"] - sum(ts["pool"][p] for p in top_pools)
        team_rows.append({"team": team, "n": ts["n"], "hours": ts["sec"] / 3600,
                          "p50": percentile(q, 50), "p95": percentile(q, 95),
                          "long_share": ts["long"] / n_long_pool * 100 if n_long_pool else 0,
                          "pools": [ts["pool"][p] / ts["sec"] * 100 if ts["sec"] else 0 for p in top_pools],
                          "other": other / ts["sec"] * 100 if ts["sec"] else 0})

    # プロジェクト × プール（ジョブ数ベース）
    ppstat = defaultdict(lambda: {"n": 0, "q": [], "pool": Counter()})
    for j in pool_usable:
        pp = ppstat[j.project]
        pp["n"] += 1
        pp["pool"][j.pool] += 1
        if j.queued is not None:
            pp["q"].append(j.queued)
    projpool_rows = []
    for pid, pp in sorted(ppstat.items(), key=lambda kv: -kv[1]["n"]):
        other = pp["n"] - sum(pp["pool"][p] for p in top_pools)
        projpool_rows.append({"pid": pid, "name": plabel(pid), "team": team_of(pid), "n": pp["n"],
                              "p95": percentile(sorted(pp["q"]), 95),
                              "pools": [pp["pool"][p] / pp["n"] * 100 for p in top_pools],
                              "other": other / pp["n"] * 100, "counts": pp["pool"]})

    # パイプライン単位：どのプールで実行されたか
    plstat = {}
    for j in pool_usable:
        key = (j.project, j.pipeline)
        pl = plstat.get(key)
        if pl is None:
            pl = plstat[key] = {"n": 0, "s": j.s, "e": j.e, "qmax": 0.0, "pool": Counter()}
        pl["n"] += 1
        pl["s"] = min(pl["s"], j.s)
        pl["e"] = max(pl["e"], j.e)
        pl["pool"][j.pool] += 1
        if j.queued is not None and j.queued > pl["qmax"]:
            pl["qmax"] = j.queued

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
        st["busy"] += busy_overlap(j)
    ranking = sorted(stats.items(), key=lambda kv: -kv[1]["busy"])

    # --- プロジェクト別 ---
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
        i = widx_of_job(j)
        if i is not None:
            ps["weekly"][i] += d / 3600
        ps["months"][day_info(int(j.s // 86400))[3]] += d
        ps["busy"] += busy_overlap(j)
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

    def rnd(v, d=1):
        return None if v is None else round(v, d)

    wcsv("weekly.csv", ["週(月曜)", "ジョブ数", "占有時間h", "平均同時実行", "最大同時実行",
                        "混雑する時間帯の時間数", "待ちp50秒", "待ちp95秒", f"同時実行{limit}以上の時間(分)"],
         [[w["week"], w["jobs"], round(w["runner_h"], 2), round(w["avg_conc"], 3), w["peak_conc"],
           w["busy_hours"], w["queued_p50_s"], w["queued_p95_s"], round(w["limit_min"], 1)] for w in weekly])
    wcsv("wait_by_project.csv", ["プロジェクトID", "プロジェクト", "ジョブ数", "待ちp50秒", "待ちp95秒",
                                 f"長い待ち(>{wait_thr:.0f}秒)の件数", "長い待ちに占める割合%", "全ジョブに占める割合%"],
         [[r["pid"], r["name"], r["n"], r["p50"], r["p95"], r["long"], round(r["long_share"], 2),
           round(r["job_share"], 2)] for r in wait_rows])
    wcsv("tags.csv", ["タグ", "ジョブ数", "占有時間h", "割合%", "プロジェクト数", "最大同時実行", "同時実行p99"],
         [[r[0], r[1], round(r[2], 2), round(r[3], 2), r[4], r[5], r[6]] for r in tag_rows])
    wcsv("pools.csv", ["プール", "runner ID 数", "ジョブ数", "占有時間h", "割合%", "最大同時実行", "同時実行p99",
                       "上限の目安", "混雑する時間帯の平均同時実行", "混雑する時間帯の埋まり具合%",
                       "上限到達(分)", "待ちp50秒", "待ちp95秒", "長い待ちの件数",
                       "長い待ちに占める割合%"],
         [[r["pool"], r["ids"], r["n"], round(r["hours"], 2), round(r["share"], 2), r["peak"], r["p99"], r["lim"],
           round(r["busy_avg"], 3), round(r["fill"], 1), round(r["at_lim_min"], 1), r["p50"], r["p95"],
           r["long"], round(r["long_share"], 2)] for r in pool_rows])
    wcsv("job_runner.csv", ["job_id", "pipeline_id", "プロジェクトID", "プロジェクト", "チーム", "ジョブ名", "開始",
                            "待ち秒", "実行秒", "runner_id", "runner 名", "プール（runner の所属グループ）"],
         [[j.jid, j.pipeline, j.project, plabel(j.project), team_of(j.project), j.name, dts(j.s),
           j.queued, round(j.e - j.s), j.runner_id, j.runner, j.pool]
          for j in sorted(pool_usable, key=lambda x: x.s)])
    wcsv("pipeline_runner.csv", ["pipeline_id", "プロジェクトID", "プロジェクト", "チーム", "開始", "終了", "ジョブ数",
                                 "最大待ち秒", "プール数", "プール別ジョブ数"],
         [[pl_id, pid, plabel(pid), team_of(pid), dts(pl["s"]), dts(pl["e"]), pl["n"], round(pl["qmax"], 1),
           len(pl["pool"]), " / ".join(f"{k}: {v}" for k, v in pl["pool"].most_common())]
          for (pid, pl_id), pl in sorted(plstat.items(), key=lambda kv: kv[1]["s"])])
    wcsv("project_pool.csv", ["プロジェクトID", "プロジェクト", "チーム", "ジョブ数", "待ちp95秒"]
         + [f"{p} ジョブ数" for p in top_pools] + ["その他 ジョブ数"],
         [[r["pid"], r["name"], r["team"], r["n"], r["p95"]] + [r["counts"][p] for p in top_pools]
          + [r["n"] - sum(r["counts"][p] for p in top_pools)] for r in projpool_rows])
    wcsv("coverage_weekly.csv", ["週(月曜)", "ジョブ数", "runner・プロジェクト名がそろっているジョブ数", "割合%"],
         [[w["week"], wk_tot[i], wk_ok[i], round(wk_ok[i] / wk_tot[i] * 100, 1) if wk_tot[i] else None]
          for i, w in enumerate(weekly)])
    wcsv("pool_map.csv", ["プール", "runner 名", "runner ID", "ジョブ数", "最初のジョブ開始", "最後のジョブ終了", "稼働日数"],
         map_rows)
    wcsv("pool_over_limit.csv", ["プール", "時刻", "最大同時実行", "上限の目安", "動いていた runner ID 数", "runner ID"],
         over_rows)
    wcsv("team_pool.csv", ["チーム", "ジョブ数", "占有時間h", "待ちp50秒", "待ちp95秒", "長い待ちに占める割合%"]
         + [f"{p} %" for p in top_pools] + ["その他 %"],
         [[r["team"], r["n"], round(r["hours"], 2), r["p50"], r["p95"], round(r["long_share"], 2)]
          + [round(x, 1) for x in r["pools"]] + [round(r["other"], 1)] for r in team_rows])
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
    palette = ["#2563eb", "#dc2626", "#16a34a", "#f59e0b", "#7c3aed"]
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

    H.append(f"<h2>2. 待ち時間の長いジョブのプロジェクト内訳（{wait_period}）</h2>")
    H.append(table(["プロジェクト", "ジョブ数", "待ち p50（秒）", "待ち p95（秒）", "長い待ち※の件数",
                    "長い待ち※に占める割合", "全ジョブに占める割合"],
                   [[r["name"], f"{r['n']:,}", fnum(r["p50"]), fnum(r["p95"]), f"{r['long']:,}", bar(r["long_share"]),
                     f"{r['job_share']:.1f}%"] for r in wait_rows[:a.project_top]], center={6}))
    H.append(f"<p class='fn'>※長い待ち：この期間の全ジョブの待ち時間 p95（{wait_thr:.0f} 秒）を超えたもの</p>")

    H.append("<h2>3. 曜日 × 時刻の平均同時実行数（直近 12 週）</h2>")
    H.append(heatmap(cells))

    H.append("<h2>4. タグ別</h2>")
    H.append(table(["タグ", "ジョブ数", "占有時間h", "割合", "プロジェクト数", "最大同時", "同時p99"],
                   [[r[0], f"{r[1]:,}", fnum(r[2]), bar(r[3]), r[4], r[5] if r[5] is not None else "-",
                     r[6] if r[6] is not None else "-"] for r in tag_rows[:15]]))

    # ---- 5. runner プール別
    H.append(f"<h2>5. runner プール別（{pool_period}）</h2>")
    H.append(f"<p class='note'>プール＝runner の所属グループ。runner の情報が残っている直近の期間だけで集計"
             f"（期間は --pool-since で変更可）。</p>")
    H.append(table(["プール", "runner ID 数", "ジョブ数", "占有h", "割合", "最大同時", "同時p99", "上限の目安※1",
                    "混雑時※の平均同時", "混雑時※の埋まり具合※2", "上限到達（分）",
                    "待ち p50（秒）", "待ち p95（秒）", "長い待ち※3に占める割合"],
                   [[r["pool"], r["ids"], f"{r['n']:,}", fnum(r["hours"]), bar(r["share"]), r["peak"], r["p99"],
                     r["lim"], fnum(r["busy_avg"], 2), bar(r["fill"]), fnum(r["at_lim_min"]),
                     fnum(r["p50"]), fnum(r["p95"]), bar(r["long_share"])] for r in pool_rows[:15]]))
    H.append(f"<p class='fn'>※ 混雑時：全体の同時実行数が上位 {a.busy_pct:g}% の時間"
             f"　※1 週ごとの最大同時実行数の上から {a.pool_limit_weeks} 番目（concurrent の推定値）"
             f"　※2 混雑時の平均同時 ÷ 上限の目安"
             f"　※3 待ち時間が期間全体の p95（{pool_wait_thr:.0f} 秒）を超えたジョブ</p>")

    H.append(f"<h3>チーム × プール（{pool_period}）</h3>")
    H.append("<p class='note'>各チームの runner 使用時間が、どのプールで実行されたかの割合。</p>")
    H.append(table(["チーム", "ジョブ数", "占有h", "待ち p50（秒）", "待ち p95（秒）", "長い待ち※3に占める割合"]
                   + [p[-24:] for p in top_pools] + ["その他"],
                   [[r["team"], f"{r['n']:,}", fnum(r["hours"]), fnum(r["p50"]), fnum(r["p95"]), bar(r["long_share"])]
                    + [f"{x:.0f}%" if x >= 0.5 else "-" for x in r["pools"]]
                    + [f"{r['other']:.0f}%" if r["other"] >= 0.5 else "-"] for r in team_rows[:15]]))

    H.append(f"<h3>プロジェクト × プール（{pool_period}・ジョブ数の多い順に上位 {a.project_top}）</h3>")
    H.append("<p class='note'>各プロジェクトのジョブがどのプールで実行されたかの割合（ジョブ数）。"
             "ジョブ・パイプライン単位の一覧は job_runner.csv / pipeline_runner.csv。</p>")
    H.append(table(["プロジェクト", "チーム", "ジョブ数", "待ち p95（秒）"] + [p[-24:] for p in top_pools] + ["その他"],
                   [[r["name"], r["team"], f"{r['n']:,}", fnum(r["p95"])]
                    + [f"{x:.0f}%" if x >= 0.5 else "-" for x in r["pools"]]
                    + [f"{r['other']:.0f}%" if r["other"] >= 0.5 else "-"] for r in projpool_rows[:a.project_top]]))

    H.append("<h3>上限の目安を超えた時間（上位 20）</h3>")
    if over_rows:
        H.append("<p class='note'>runner ID が 2 つ以上動いていれば、runner の入れ替わり（新旧の重なり）で一時的に枠が増えたと考えられます。"
                 "全件は pool_over_limit.csv。</p>")
        H.append(table(["プール", "時刻", "最大同時", "上限の目安", "動いていた runner ID 数", "runner ID"],
                       [r[:5] + [r[5] if len(r[5]) <= 60 else r[5][:60] + " …"] for r in over_rows[:20]],
                       num_from=2, center={4}))
    else:
        H.append("<p class='note'>該当なし</p>")

    H.append(f"<h2>6. プロジェクト別（混雑する時間帯に占める割合の順・上位 {a.project_top}）</h2>")
    ptop = proj_rows[:a.project_top]
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

    H.append(f"<h2>7. ジョブ別の占有（混雑する時間帯に占める割合の順・上位 {a.top}）</h2>")
    H.append(table(["ジョブ名", "ステージ", "プロジェクト数", "回数", "占有h", "全時間帯に占める割合",
                    "混雑する時間帯※に占める割合", "平均分", "p95分", "failed率"],
                   [[r[0], r[1], r[2], f"{r[3]:,}", fnum(r[4]), f"{r[5]:.1f}%", bar(r[7]), fnum(r[8]), fnum(r[9]),
                     f"{r[10]:.0f}%"] for r in rank_rows[:a.top]], num_from=2, center={5}))
    H.append(footnote)

    if whatif:
        H.append("<h2>8. 指定したジョブを削った場合（--exclude）</h2>")
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
    print(f"runner プール: {len(pool_rows)} 個（runner 名 {len(map_rows)} 個をまとめた結果）")
    for r in pool_rows[:8]:
        print(f"  {r['pool'][:40]:<40}  runner ID {r['ids']:>3}  上限の目安 {r['lim']:>3}  "
              f"混雑時の埋まり {r['fill']:5.1f}%  直近 待ちp95 {fnum(r['p95']):>6} 秒")
    if whatif:
        print(f"削減後: 混雑する時間帯の平均同時 {whatif['busy_avg']:.2f}  最大 {whatif['max']}  削減 {whatif['saved_h']:,.0f} h")
    print(f"出力: {os.path.join(a.outdir, 'report.html')}")


if __name__ == "__main__":
    main()
