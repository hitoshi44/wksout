#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
曜日 × 時刻の平均同時実行数を、全ジョブと「指定したジョブのグループ」ごとに表にする。
標準ライブラリのみ（Python 3.11 以上）。

使い方（jobs_YYYY-MM-DD.tsv が並んでいるフォルダで）:
  python heatmap_report.py                                   # 全ジョブだけ（＋占有の多いジョブ名の一覧）
  python heatmap_report.py -g "integration=^integration-" -g "e2e=^e2e" -g "build=^build$"
  python heatmap_report.py --groups groups.txt               # グループをファイルで指定
  python heatmap_report.py --weeks 0                         # 期間全体（既定は直近 12 週）

グループの指定:
  "表示名=正規表現"。正規表現はジョブ名に対して部分一致（Python の re.search）。
  完全一致にしたいときは ^ と $ を付ける。上から順に判定し、最初に当たったグループに入れる。
  groups.txt は 1 行 1 つ、同じ書式（# で始まる行と空行は無視）。

出力（--outdir、既定 report_heatmap/）:
  heatmap.html       表のまとめ（ブラウザで開く）
  heatmap_cells.csv  グループ・曜日・時刻ごとの平均同時実行数
  job_names.csv      ジョブ名ごとの回数・占有時間と、入ったグループ
  project_job_cells.csv  プロジェクト × ジョブ × 曜日 × 時刻の平均同時実行数
"""
import argparse
import csv
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
ALL = "全ジョブ"
OTHER = "指定外"


def parse_ts(s):
    try:
        return datetime.fromisoformat(s).timestamp() if s else None
    except ValueError:
        return None


def load(paths, tzoff):
    """job_id で重複除去。返り値は {job_id: (開始秒, 終了秒, ジョブ名, プロジェクトID)}（ローカル時刻）"""
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
                jobs[r["job_id"]] = (s + tzoff, e + tzoff, r["name"], r["project"])
    return jobs


def parse_group(text):
    if "=" not in text:
        sys.exit(f"グループの書式が違います（表示名=正規表現）: {text}")
    label, pat = text.split("=", 1)
    label, pat = label.strip(), pat.strip()
    try:
        return label, re.compile(pat)
    except re.error as ex:
        sys.exit(f"正規表現の誤り [{label}] {pat}: {ex}")


def load_names(path):
    """プロジェクト ID → 名前。CSV（proj-id,proj-name）でも TSV でも読む。見出し行は自動で飛ばす。"""
    if not path:
        path = next((p for p in ("projects.csv", "projects.tsv") if os.path.isfile(p)), None)
        if not path:
            return {}
    elif not os.path.isfile(path):
        sys.exit(f"対応表が見つかりません: {path}")
    with open(path, encoding="utf-8-sig", errors="replace", newline="") as f:
        text = f.read()
    first = text.splitlines()[0] if text else ""
    delim = "\t" if first.count("\t") > first.count(",") else ","
    names = {}
    for c in csv.reader(text.splitlines(), delimiter=delim):
        if len(c) < 2:
            continue
        pid, nm = c[0].strip(), c[1].strip()
        if not pid.isdigit() or not nm:      # 見出し行（proj-id など）や空行は飛ばす
            continue
        names[pid] = nm
    print(f"プロジェクト名: {path} から {len(names)} 件")
    return names


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
.seg{display:flex;flex-wrap:wrap;gap:4px;margin:6px 0}
.seg button{border:1px solid #d4d4d0;background:#fff;border-radius:4px;padding:2px 10px;font-size:.8rem;cursor:pointer}
.seg button.on{background:#1f2328;color:#fff;border-color:#1f2328}
.pheat{width:100%;table-layout:fixed}
.pheat th,.pheat td{padding:3px 0;text-align:center;font-size:.72rem;white-space:nowrap}
.pheat th.nm,.pheat td.nm{width:15em;text-align:left;padding:3px 6px;overflow:hidden;text-overflow:ellipsis}
.pheat th.sh,.pheat td.sh{width:4.2em;text-align:right;padding:3px 6px}
.pheat tr.p td.nm{cursor:pointer;font-weight:600}
.pheat tr.p td.nm::before{content:"▶ ";font-size:.65rem;color:#888}
.pheat tr.p.open td.nm::before{content:"▼ "}
.pheat tr.j td.nm{padding-left:22px;color:#444}
.pheat tr.j td.sh{color:#666}
.pheat tr.j.grp td.nm{color:#1d4ed8}
#tip{position:fixed;display:none;pointer-events:none;z-index:10;background:#fff;border:1px solid #d4d4d0;
 border-radius:6px;box-shadow:0 4px 12px rgba(0,0,0,.12);padding:6px 10px;font-size:.8rem;min-width:180px}
#tip .th{font-weight:600;margin-bottom:3px}#tip div{display:flex;gap:12px}
#tip b{margin-left:auto;font-variant-numeric:tabular-nums}
"""

PJS = """
(function () {
  if (typeof PDATA === 'undefined') return;
  var WD = ['月','火','水','木','金','土','日'];
  var tbl = document.getElementById('ptable'), tip = document.getElementById('tip');
  var days = [0,1,2,3,4], open = {};
  function val(v, h) {
    var s = 0, c = 0;
    days.forEach(function (d) { s += v[d * 24 + h]; c += PDATA.cnt[d * 24 + h]; });
    return c ? s / c : 0;
  }
  function esc(t) { return String(t).replace(/[&<>"]/g, function (c) { return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]; }); }
  function cell(v, vmax, pi, ji, h) {
    var a = Math.min(1, v / vmax), fg = a > 0.55 ? '#fff' : 'inherit';
    var t = v >= 0.05 ? v.toFixed(1) : (v === 0 ? '0' : '·');
    return '<td data-p="' + pi + '" data-j="' + ji + '" data-h="' + h + '" style="background:rgba(37,99,235,' + a.toFixed(2) +
           ');color:' + fg + '">' + t + '</td>';
  }
  function render() {
    var vmax = 0, ptotal = 0;
    PDATA.projects.forEach(function (p) { for (var h = 0; h < 24; h++) vmax = Math.max(vmax, val(p.v, h)); });
    vmax = vmax || 1;
    var out = ['<tr><th class="nm">プロジェクト / ジョブ</th><th class="sh">割合</th>'];
    for (var h = 0; h < 24; h++) out.push('<th>' + h + '時</th>');
    out.push('</tr>');
    PDATA.projects.forEach(function (p, pi) {
      out.push('<tr class="p' + (open[pi] ? ' open' : '') + '" data-p="' + pi + '"><td class="nm" title="' + esc(p.name) + '">' +
               esc(p.name) + '</td><td class="sh">' + p.share.toFixed(1) + '%</td>');
      for (var h = 0; h < 24; h++) out.push(cell(val(p.v, h), vmax, pi, -1, h));
      out.push('</tr>');
      if (open[pi]) p.jobs.forEach(function (j, ji) {
        out.push('<tr class="j' + (j.grp ? ' grp' : '') + '"><td class="nm" title="' + esc(j.name) + '">' + esc(j.name) +
                 '</td><td class="sh">' + j.share.toFixed(0) + '%</td>');
        for (var h = 0; h < 24; h++) out.push(cell(val(j.v, h), vmax, pi, ji, h));
        out.push('</tr>');
      });
    });
    tbl.innerHTML = out.join('');
  }
  tbl.addEventListener('click', function (ev) {
    var tr = ev.target.closest('tr.p');
    if (!tr || !ev.target.closest('td.nm')) return;
    var pi = +tr.getAttribute('data-p'); open[pi] = !open[pi]; render();
  });
  tbl.addEventListener('mousemove', function (ev) {
    var td = ev.target.closest('td[data-h]');
    if (!td) { tip.style.display = 'none'; return; }
    var p = PDATA.projects[+td.dataset.p], h = +td.dataset.h, ji = +td.dataset.j;
    var lab = document.querySelector('#pfilter button.on').textContent;
    var html = '<div class="th">' + esc(p.name) + ' ／ ' + lab + ' ' + h + '時</div>';
    if (ji < 0) {
      html += '<div>合計<b>' + val(p.v, h).toFixed(2) + '</b></div>';
      p.jobs.map(function (j) { return [j.name, val(j.v, h)]; })
            .sort(function (x, y) { return y[1] - x[1]; }).slice(0, 5)
            .forEach(function (x) { if (x[1] > 0) html += '<div>' + esc(x[0]) + '<b>' + x[1].toFixed(2) + '</b></div>'; });
    } else {
      var j = p.jobs[ji]; html += '<div>' + esc(j.name) + '<b>' + val(j.v, h).toFixed(2) + '</b></div>';
    }
    tip.innerHTML = html; tip.style.display = 'block';
    var w = tip.offsetWidth, hh = tip.offsetHeight, x = ev.clientX + 14, y = ev.clientY + 14;
    if (x + w > innerWidth - 8) x = ev.clientX - w - 14;
    if (y + hh > innerHeight - 8) y = ev.clientY - hh - 14;
    tip.style.left = x + 'px'; tip.style.top = y + 'px';
  });
  tbl.addEventListener('mouseleave', function () { tip.style.display = 'none'; });
  document.getElementById('pfilter').addEventListener('click', function (ev) {
    var b = ev.target.closest('button'); if (!b) return;
    document.querySelectorAll('#pfilter button').forEach(function (x) { x.classList.remove('on'); });
    b.classList.add('on');
    days = b.dataset.d.split('').map(Number); render();
  });
  render();
})();
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
    ap = argparse.ArgumentParser(description="曜日 × 時刻の平均同時実行数（全ジョブ / 指定したジョブのグループ）")
    ap.add_argument("inputs", nargs="*", default=["jobs_*.tsv"])
    ap.add_argument("-g", "--group", action="append", default=[], help="グループ '表示名=正規表現'（複数指定可）")
    ap.add_argument("--groups", help="グループを書いたファイル（1 行 1 つ、'表示名=正規表現'）")
    ap.add_argument("--weeks", type=int, default=12, help="直近何週を平均するか（0 なら期間全体、既定 12）")
    ap.add_argument("--since", help="この日以降（例: 2026-07-01）。指定すると --weeks より優先")
    ap.add_argument("--until", help="この日より前（例: 2026-10-01）")
    ap.add_argument("--no-other", action="store_true", help="「指定外」の表を出さない")
    ap.add_argument("--same-scale", action="store_true", help="色の濃さを全ジョブの表と同じ基準にする（既定は表ごと）")
    ap.add_argument("--top", type=int, default=30, help="占有の多いジョブ名を何件表示するか（既定 30）")
    ap.add_argument("--tz", type=float, default=9, help="UTC からの時差（既定 9）")
    ap.add_argument("--project-top", type=int, default=15, help="プロジェクト × 時刻の表に出すプロジェクト数（既定 15）")
    ap.add_argument("--project-jobs", type=int, default=8, help="プロジェクトを開いたときに出すジョブ数（既定 8）")
    ap.add_argument("--names", help="プロジェクト ID と名前の対応表（CSV / TSV、1 列目 ID・2 列目 名前）。"
                    "省略時は projects.csv、なければ projects.tsv を探す")
    ap.add_argument("--outdir", default="report_heatmap")
    a = ap.parse_args()
    try:
        sys.stdout.reconfigure(errors="replace")
    except Exception:
        pass

    # グループ
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
    last_day = int(max(e for _, e, *_ in jobs.values()) // 86400)
    if a.since or a.until:
        d0 = (date.fromisoformat(a.since) - EPOCH).days if a.since else int(min(s for s, *_ in jobs.values()) // 86400)
        d1 = (date.fromisoformat(a.until) - EPOCH).days if a.until else last_day + 1
    elif a.weeks > 0:
        d1 = last_day + 1
        d0 = d1 - a.weeks * 7
    else:
        d0 = int(min(s for s, *_ in jobs.values()) // 86400)
        d1 = last_day + 1
    lo, hi = d0 * 86400, d1 * 86400
    h0, n = d0 * 24, (d1 - d0) * 24
    period = f"{EPOCH + timedelta(days=d0):%Y/%m/%d} 〜 {EPOCH + timedelta(days=d1 - 1):%Y/%m/%d}"
    print(f"期間: {period}（{(d1 - d0) / 7:.1f} 週）")

    # プロジェクト名（projects.tsv があれば、パスの最後を表示。重複するときは上の階層も付ける）
    pname = load_names(a.names)
    short = {pid: path.split("/")[-1] for pid, path in pname.items()}
    for depth in range(2, 10):
        dup = Counter(short.values())
        if all(v == 1 for v in dup.values()):
            break
        for pid, path in pname.items():
            if dup[short[pid]] > 1:
                short[pid] = "/".join(path.split("/")[-depth:])

    # ジョブ名 → グループ
    names = sorted({j[2] for j in jobs.values()})
    name_group = {}
    for nm in names:
        name_group[nm] = next((lb for lb, rx in groups if rx.search(nm)), OTHER)

    # 1 時間ごとの占有秒（グループごと）
    keys = [ALL] + labels + ([] if a.no_other or not groups else [OTHER])
    busy = {k: [0.0] * n for k in keys}
    name_stat = defaultdict(lambda: [0, 0.0])        # ジョブ名 → [回数, 占有秒]（期間内）
    pjsum = defaultdict(lambda: [0.0] * 168)        # (プロジェクト, ジョブ名 or グループ) → 曜日×時刻の占有時間の合計
    for s, e, nm, pid in jobs.values():
        s, e = max(s, lo), min(e, hi)
        if e <= s:
            continue
        g = name_group[nm]
        targets = [busy[ALL]] + ([busy[g]] if g in busy else [])
        jk = g if g != OTHER else nm                 # グループに入ったジョブはグループ名でまとめる
        pj = pjsum[(pid, jk)]
        for hh in range(int(s // 3600), int((e - 1e-9) // 3600) + 1):
            ov = min(e, (hh + 1) * 3600) - max(s, hh * 3600)
            if ov > 0:
                for t in targets:
                    t[hh - h0] += ov
                pj[((hh // 24 + 3) % 7) * 24 + hh % 24] += ov / 3600   # 1970-01-01 は木曜
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

    # グループごとの情報
    ginfo = {}
    for k in keys:
        members = sorted(((nm, *name_stat[nm]) for nm in names
                          if nm in name_stat and (k == ALL or name_group[nm] == k)), key=lambda x: -x[2])
        sec = sum(busy[k])
        ginfo[k] = {"members": members, "jobs": sum(m[1] for m in members), "hours": sec / 3600,
                    "share": sec / total_sec * 100, "avg": sec / (n * 3600),
                    "peak": max(max(r) for r in cells[k]),
                    "peak_at": max(((w, h) for w in range(7) for h in range(24)), key=lambda wh: cells[k][wh[0]][wh[1]])}

    # プロジェクト × ジョブ
    cnt168 = [cnt[w][h] for w in range(7) for h in range(24)]
    ptot = defaultdict(lambda: [0.0] * 168)
    pjobs = defaultdict(list)
    for (pid, jk), arr in pjsum.items():
        t = ptot[pid]
        for i, v in enumerate(arr):
            t[i] += v
        pjobs[pid].append((jk, arr))
    total_h = total_sec / 3600
    pdata = []
    for pid, arr in sorted(ptot.items(), key=lambda kv: -sum(kv[1]))[:a.project_top]:
        js = sorted(pjobs[pid], key=lambda x: -sum(x[1]))
        rows = [{"name": jk, "grp": jk in labels, "share": sum(ar) / sum(arr) * 100, "v": [round(x, 4) for x in ar]}
                for jk, ar in js[:a.project_jobs]]
        if len(js) > a.project_jobs:
            rest = [0.0] * 168
            for _, ar in js[a.project_jobs:]:
                for i, v in enumerate(ar):
                    rest[i] += v
            rows.append({"name": f"その他 {len(js) - a.project_jobs} 種類", "grp": False,
                         "share": sum(rest) / sum(arr) * 100, "v": [round(x, 4) for x in rest]})
        pdata.append({"name": short.get(pid, pid), "share": sum(arr) / total_h * 100,
                      "v": [round(x, 4) for x in arr], "jobs": rows})
    n_proj = len(ptot)
    if pname:
        missing = [pid for pid in ptot if pid not in pname]
        if missing:
            print(f"対応表にないプロジェクト ID: {len(missing)} 件（ID のまま表示）例: {', '.join(missing[:5])}")

    # ------------------------------------------------------------- CSV
    os.makedirs(a.outdir, exist_ok=True)
    with open(os.path.join(a.outdir, "heatmap_cells.csv"), "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["グループ", "曜日", "時", "平均同時実行数"])
        for k in keys:
            for wd in range(7):
                for hr in range(24):
                    w.writerow([k, WEEKDAYS[wd], hr, round(cells[k][wd][hr], 3)])
    with open(os.path.join(a.outdir, "project_job_cells.csv"), "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["プロジェクトID", "プロジェクト", "ジョブ名またはグループ", "曜日", "時", "平均同時実行数"])
        for (pid, jk), arr in sorted(pjsum.items()):
            for i, v in enumerate(arr):
                if v:
                    w.writerow([pid, short.get(pid, pid), jk, WEEKDAYS[i // 24], i % 24,
                                round(v / cnt168[i], 4) if cnt168[i] else 0])
    ranked = sorted(name_stat.items(), key=lambda kv: -kv[1][1])
    with open(os.path.join(a.outdir, "job_names.csv"), "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["ジョブ名", "回数", "占有時間h", "全体に占める割合%", "グループ"])
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
    H.append(f"<h2>{no}. 占有時間の多いジョブ名（上位 {a.top}）</h2>")
    H.append(table(["ジョブ名", "回数", "占有h", "全ジョブに占める割合"] + (["グループ"] if groups else []),
                   [[nm, f"{c:,}", f"{sec / 3600:,.1f}", f"{sec / total_sec * 100:.1f}%"] + ([name_group[nm]] if groups else [])
                    for nm, (c, sec) in ranked[:a.top]]))
    H.append("<p class='fn'>※全件は job_names.csv。グループの指定（-g）を考えるときの参考に</p>")
    no += 1

    for k in keys:
        if k == OTHER and not ginfo[k]["jobs"]:
            continue
        section(no, k)
        no += 1
        if k == ALL:
            H.append(f"<h2>{no}. プロジェクト × 時刻（上位 {len(pdata)} / {n_proj} プロジェクト）</h2>")
            H.append("<div class='meta'>行をクリックすると、そのプロジェクトのジョブ別に開きます。"
                     "セルにマウスを乗せると内訳を表示します。</div>")
            H.append("<div class='seg' id='pfilter'>" + "".join(
                f"<button data-d='{d}'{' class=on' if i == 1 else ''}>{lb}</button>"
                for i, (lb, d) in enumerate([("全曜日", "0123456"), ("平日", "01234"), ("土日", "56")]
                                            + [(w, str(i)) for i, w in enumerate(WEEKDAYS)])) + "</div>")
            H.append("<div class='tw'><table class='pheat' id='ptable'></table></div>")
            H.append("<p class='fn'>※値は選んだ曜日の平均同時実行数。色の濃さは表示中のプロジェクト行の最大値が基準"
                     + ("。グループ（-g）に入ったジョブはグループ名でまとめています" if groups else "") + "</p>")
            H.append(f"<script>const PDATA={json.dumps({'cnt': cnt168, 'projects': pdata}, ensure_ascii=False)};</script>")
            no += 1

    if groups:
        H.append(f"<h2>{no}. グループの比較</h2>")
        H.append(table(["グループ", "ジョブ名の種類", "ジョブ数", "占有h", "全ジョブに占める割合", "平均同時", "一番多い時間帯", "その時間帯の平均同時"],
                       [[k, len(ginfo[k]["members"]), f"{ginfo[k]['jobs']:,}", f"{ginfo[k]['hours']:,.0f}",
                         f"{ginfo[k]['share']:.1f}%", f"{ginfo[k]['avg']:.2f}",
                         f"{WEEKDAYS[ginfo[k]['peak_at'][0]]} {ginfo[k]['peak_at'][1]}時", f"{ginfo[k]['peak']:.2f}"]
                        for k in keys if ginfo[k]["jobs"]]))
        no += 1

    H.append(f"</main><div id='tip'></div><script>{PJS}</script></body></html>")
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
