#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import argparse
import bisect
import csv
import glob
import html
import json
import math
import os
import ssl
import sys
import urllib.error
import urllib.request
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta

COLS = ["project", "pipeline_id", "job_id", "name", "stage", "status", "duration",
        "queued_duration", "created_at", "started_at", "finished_at", "runner_id",
        "runner", "tags"]
EPOCH = date(1970, 1, 1)
EPOCH_DT = datetime(1970, 1, 1)
KIND = {"group_type": "group", "project_type": "project", "instance_type": "instance"}
WEEKDAYS = ["月", "火", "水", "木", "金", "土", "日"]


# ------------------------------------------------------------------ 共通
def parse_ts(s):
    try:
        return datetime.fromisoformat(s).timestamp() if s else None
    except ValueError:
        return None


def to_float(s):
    try:
        return float(s) if s else None
    except ValueError:
        return None


def percentile(vals, p):
    if not vals:
        return None
    v = sorted(vals)
    return v[max(0, min(len(v) - 1, math.ceil(p / 100 * len(v)) - 1))]


def r1(v):
    return None if v is None else round(v, 1)


# ------------------------------------------------------------------ 読み込み
def load_jobs(patterns, tzoff):
    files = []
    for p in patterns:
        hit = sorted(glob.glob(p)) if any(c in p for c in "*?[") else [p]
        files += [f for f in hit if f not in files and os.path.isfile(f)]
    if not files:
        sys.exit(f"入力ファイルが見つかりません: {' '.join(patterns)}")
    jobs = {}
    for path in files:
        with open(path, encoding="utf-8", errors="replace", newline="") as f:
            for row in csv.reader(f, delimiter="\t", quoting=csv.QUOTE_NONE):
                if not row or row[0] == "project":
                    continue
                row += [""] * (len(COLS) - len(row))
                r = dict(zip(COLS, row))
                try:
                    jid = int(r["job_id"])
                except ValueError:
                    continue
                s, e = parse_ts(r["started_at"]), parse_ts(r["finished_at"])
                if s is None:
                    continue           # 開始していないジョブは runner に割り当てられていない
                j = {"jid": jid, "pipeline": r["pipeline_id"].strip(), "project": r["project"].strip(),
                     "name": r["name"], "status": r["status"], "s": s + tzoff,
                     "e": e + tzoff if e is not None else None, "queued": to_float(r["queued_duration"]),
                     "runner_id": r["runner_id"].strip(), "runner": r["runner"].strip()}
                old = jobs.get(jid)
                if old is None or (old["e"] is None and j["e"] is not None) or (not old["runner_id"] and j["runner_id"]):
                    jobs[jid] = j
    print(f"入力: {len(files)} ファイル / 開始済みのジョブ {len(jobs):,} 件")
    return list(jobs.values())


def load_map(path, sep):
    m = {}
    if path and os.path.isfile(path):
        with open(path, encoding="utf-8-sig", errors="replace", newline="") as f:
            for row in csv.reader(f, delimiter=sep):
                if len(row) >= 2 and row[0].strip().isdigit() and row[1].strip():
                    m[row[0].strip()] = row[1].strip()
    return m


def load_runner_cache(path):
    """runners.tsv（make_runners.sh と同じ形式）: id 種類 所属 名前 タグ 作成日時"""
    m = {}
    if os.path.isfile(path):
        with open(path, encoding="utf-8", errors="replace", newline="") as f:
            for row in csv.reader(f, delimiter="\t", quoting=csv.QUOTE_NONE):
                if row and row[0].strip().isdigit() and len(row) > 1 and row[1] != "error":
                    row += [""] * (6 - len(row))
                    m[row[0].strip()] = row[:6]
    return m


# ------------------------------------------------------------------ API
def fetch_runner(host, token, rid, ctx):
    """成功: [id, 種類, 所属, 名前, タグ, 作成日時]、HTTP エラー: [id, 'error', 'HTTP xxx', ...]、
    接続できない: (None, 理由)"""
    req = urllib.request.Request(f"{host.rstrip('/')}/api/v4/runners/{rid}",
                                 headers={"PRIVATE-TOKEN": token})
    try:
        with urllib.request.urlopen(req, timeout=60, context=ctx) as res:
            d = json.load(res)
    except urllib.error.HTTPError as ex:
        return [rid, "error", f"HTTP {ex.code}", "", "", ""]
    except (urllib.error.URLError, TimeoutError, OSError) as ex:
        return None, str(ex)
    kind = d.get("runner_type") or ""
    if kind == "group_type":
        owner = ",".join(g.get("web_url", "").split("://", 1)[-1].split("/", 1)[-1].removeprefix("groups/")
                         for g in d.get("groups") or [])
    elif kind == "project_type":
        owner = ",".join(p.get("path_with_namespace", "") for p in d.get("projects") or [])
    else:
        owner = ""
    desc = (d.get("description") or "").replace("\t", " ").replace("\n", " ")
    return [rid, kind, owner, desc, ",".join(d.get("tag_list") or []), d.get("created_at") or ""]


# ------------------------------------------------------------------ 集計の部品
def sweep(jobs, h0, n):
    """1 時間ごとの占有秒（busy）と最大同時実行数（hmax）"""
    busy = [0.0] * n
    hmax = [0] * n
    ev = []
    for j in jobs:
        s, e = j["s"], j["e"]
        ev.append((s, 1))
        ev.append((e, -1))
        for h in range(int(s // 3600), int(e // 3600) + 1):
            ov = min(e, (h + 1) * 3600) - max(s, h * 3600)
            if ov > 0:
                busy[h - h0] += ov
    ev.sort()                     # 同時刻は終了(-1)が先
    cur, last = 0, 0
    for t, d in ev:
        k = int(t // 3600) - h0
        if k > last:
            for x in range(last + 1, min(k, n - 1) + 1):
                hmax[x] = max(hmax[x], cur)
            last = k
        cur += d
        if 0 <= k < n and cur > hmax[k]:
            hmax[k] = cur
    return busy, hmax


def max_concurrency(jobs):
    ev = sorted([(j["s"], 1) for j in jobs] + [(j["e"], -1) for j in jobs])
    cur = mx = 0
    for _, d in ev:
        cur += d
        mx = max(mx, cur)
    return mx


# ------------------------------------------------------------------ HTML 部品
CSS = """
body{font-family:system-ui,-apple-system,"Segoe UI","Hiragino Sans","Yu Gothic UI",sans-serif;
 margin:0;background:#f7f7f5;color:#1f2328;line-height:1.6}
main{max-width:1180px;margin:0 auto;padding:24px 16px 64px}
h1{font-size:1.5rem;margin:0 0 4px}
h2{font-size:1.15rem;margin:40px 0 6px;border-bottom:1px solid #ddd;padding-bottom:4px}
.sub{color:#666;font-size:.9rem}.note{color:#555;font-size:.85rem;margin:4px 0 10px}
.fn{color:#666;font-size:.78rem;margin:4px 0 0}
.kpi{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:10px;margin:16px 0}
.kpi div{background:#fff;border:1px solid #e3e3e0;border-radius:8px;padding:10px 12px}
.kpi b{display:block;font-size:1.3rem}.kpi span{font-size:.78rem;color:#666}
.tw{overflow-x:auto}
table{border-collapse:collapse;font-size:.85rem;background:#fff}
th,td{border:1px solid #e3e3e0;padding:3px 7px;white-space:nowrap}
th{background:#f0f0ec;text-align:left;vertical-align:bottom;font-size:.78rem;cursor:pointer;user-select:none}
th:hover{background:#e6e6e0}th.asc::after{content:" ▲"}th.desc::after{content:" ▼"}
td.n{text-align:right;font-variant-numeric:tabular-nums}td.t{white-space:normal;max-width:22em;overflow-wrap:anywhere}
td.c{text-align:center}td.g{white-space:nowrap}
.legend{display:flex;flex-wrap:wrap;gap:6px 14px;font-size:.8rem;margin:6px 0 10px}
.legend i,.sw{display:inline-block;width:11px;height:11px;border-radius:2px;vertical-align:-1px;margin-right:4px}
.stack{display:flex;width:220px;height:12px;border-radius:3px;overflow:hidden;background:#eee}
.stack span{display:block;height:100%}
.bar{display:inline-block;width:70px;height:8px;background:#eee;border-radius:4px;vertical-align:middle;margin-right:6px}
.bar span{display:block;height:100%;border-radius:4px}
a{color:#1d4ed8;text-decoration:none}a:hover{text-decoration:underline}
.heat td.h{text-align:center;font-size:.75rem;min-width:2.3em;padding:3px 2px}
.chart{width:100%;height:auto;background:#fff;border:1px solid #e3e3e0;border-radius:8px}
.chart text{font-size:11px;fill:#666}
details{margin-top:8px}summary{cursor:pointer;color:#1d4ed8;font-size:.9rem}
"""

SORT_JS = """
document.querySelectorAll('table.sortable').forEach(function (t) {
  t.querySelectorAll('tr:first-child th').forEach(function (th, i) {
    th.addEventListener('click', function () {
      var desc = !th.classList.contains('desc');
      t.querySelectorAll('th').forEach(function (h) { h.classList.remove('asc', 'desc'); });
      th.classList.add(desc ? 'desc' : 'asc');
      var rows = Array.prototype.slice.call(t.querySelectorAll('tr')).slice(1);
      function key(r) {
        var c = r.children[i]; if (!c) return '';
        var v = c.getAttribute('data-v');
        if (v !== null && v !== '') return parseFloat(v);
        return c.textContent.trim();
      }
      rows.sort(function (a, b) {
        var x = key(a), y = key(b), r;
        if (typeof x === 'number' && typeof y === 'number') r = x - y;
        else if (typeof x === 'number') r = -1; else if (typeof y === 'number') r = 1;
        else r = String(x).localeCompare(String(y), 'ja');
        return desc ? -r : r;
      });
      rows.forEach(function (r) { t.appendChild(r); });
    });
  });
});
"""

PALETTE = ["#2563eb", "#16a34a", "#f59e0b", "#7c3aed", "#db2777", "#0891b2", "#65a30d", "#ea580c"]
GRAY = "#9ca3af"


def esc(v):
    return html.escape(str(v))


def num(v, d=0, unit=""):
    """数値セル：表示は丸め、並べ替え用に data-v に生の値を持たせる"""
    if v is None:
        return "<td class='n' data-v=''>-</td>"
    return f"<td class='n' data-v='{v}'>{v:,.{d}f}{unit}</td>"


def txt(v, cls="t"):
    return f"<td class='{cls}'>{esc(v)}</td>"


def table(headers, rows, cls="sortable"):
    h = [f"<div class='tw'><table class='{cls}'><tr>" + "".join(f"<th>{esc(x)}</th>" for x in headers) + "</tr>"]
    h += ["<tr>" + "".join(r) + "</tr>" for r in rows]
    h.append("</table></div>")
    return "".join(h)


def heat_cell(v, pct, d=1):
    """埋まり具合 pct（0〜100）で色を付けたセル"""
    if v is None:
        return "<td class='h n' data-v=''>-</td>"
    a = min(1.0, max(0.0, pct / 100))
    fg = "#fff" if a > 0.6 else "inherit"
    return (f"<td class='h n' data-v='{pct}' style='background:rgba(220,38,38,{a * 0.85:.2f});color:{fg}' "
            f"title='{pct:.0f}%'>{v:,.{d}f}</td>")


# ------------------------------------------------------------------ メイン
def main():
    ap = argparse.ArgumentParser(description="直近のジョブの runner グループへの割り当てと偏り")
    ap.add_argument("inputs", nargs="*", default=["jobs_*.tsv"])
    ap.add_argument("--host", default=os.environ.get("GITLAB_HOST", ""),
                    help="GitLab の URL（例: https://gitlab.example.com）。環境変数 GITLAB_HOST でも可")
    ap.add_argument("--no-api", action="store_true", help="API を呼ばず、runners.tsv にある分だけ使う")
    ap.add_argument("--cache", default="runners.tsv", help="runner 情報の保存先（既定 runners.tsv）")
    ap.add_argument("--months", type=float, default=3, help="最新のジョブから何か月さかのぼるか（既定 3）")
    ap.add_argument("--since", help="この日以降に開始したジョブだけ（--months より優先。例: 2026-07-15）")
    ap.add_argument("--until", help="この日より前に開始したジョブだけ")
    ap.add_argument("--busy-pct", type=float, default=10, help="混雑時とみなす上位 %%（既定 10）")
    ap.add_argument("--tz", type=float, default=9, help="表示タイムゾーンの UTC からの時差（既定 9）")
    ap.add_argument("--names", default="projects.tsv", help="プロジェクト ID<TAB>パス（任意）")
    ap.add_argument("--teams", default="teams.csv", help="プロジェクト ID,プロジェクト名（任意）")
    ap.add_argument("--top", type=int, default=30, help="HTML の表の表示件数（既定 30）")
    ap.add_argument("--outdir", default="runner_assign")
    a = ap.parse_args()
    try:
        sys.stdout.reconfigure(errors="replace")
    except Exception:
        pass

    alljobs = load_jobs(a.inputs, a.tz * 3600)
    if not alljobs:
        sys.exit("開始済みのジョブがありません")
    paths = load_map(a.names, "\t")
    tnames = load_map(a.teams, ",")
    if paths:
        print(f"プロジェクトのパス: {a.names} から {len(paths)} 件")
    if tnames:
        print(f"プロジェクト名: {a.teams} から {len(tnames)} 件")

    def pname(pid):
        """プロジェクト名（teams.csv → パスの末尾 → ID）"""
        return tnames.get(pid) or (paths[pid].split("/")[-1] if pid in paths else f"#{pid}")

    def plabel(pid):
        """表示用：teams.csv の名前に GitLab 上の名前を添えて区別する"""
        base = paths[pid].split("/")[-1] if pid in paths else f"#{pid}"
        return f"{tnames[pid]}（{base}）" if pid in tnames and tnames[pid] != base else base

    def dts(sec):
        return (EPOCH_DT + timedelta(seconds=sec)).strftime("%Y-%m-%d %H:%M") if sec is not None else ""

    def local_sec(d):
        return (date.fromisoformat(d) - EPOCH).days * 86400

    # ---------------------------------------------------------- 期間
    latest = max(j["s"] for j in alljobs)
    if a.since:
        lo = local_sec(a.since)
    else:
        lo = (int(latest // 86400) - round(a.months * 30.44) + 1) * 86400
    hi = local_sec(a.until) if a.until else math.inf
    target = [j for j in alljobs if lo <= j["s"] < hi]
    if not target:
        sys.exit("対象期間にジョブがありません")
    first = min(j["s"] for j in target)
    last = max(j["s"] for j in target)
    n_rid = sum(1 for j in target if j["runner_id"])
    period = f"{dts(first)[:10]} 〜 {dts(last)[:10]}"
    print(f"対象期間: {period}  ジョブ {len(target):,} 件（runner_id あり {n_rid / len(target) * 100:.0f}%）")

    # ---------------------------------------------------------- runner ID → 所属グループ
    cache = load_runner_cache(a.cache)
    ids = {j["runner_id"] for j in target if j["runner_id"]}
    need = sorted(ids - set(cache), key=int)
    print(f"runner ID: 対象期間に {len(ids)} 個 / {a.cache} に保存済み {len(ids) - len(need)} 個 / 未取得 {len(need)} 個")
    failed = {}
    if need and not a.no_api:
        token = os.environ.get("TOKEN", "")
        if not a.host or not token:
            sys.exit("未取得の runner があります。--host（または GITLAB_HOST）と環境変数 TOKEN を設定するか、"
                     "--no-api で取得せずに進めてください")
        ctx = ssl.create_default_context()
        ca = os.environ.get("REQUESTS_CA_BUNDLE") or os.environ.get("SSL_CERT_FILE")
        if ca:
            ctx.load_verify_locations(ca)
        with open(a.cache, "a", encoding="utf-8", newline="") as f:
            for i, rid in enumerate(need, 1):
                row = fetch_runner(a.host, token, rid, ctx)
                if isinstance(row, tuple):
                    sys.exit(f"GitLab に接続できません（runner {rid}）: {row[1]}")
                if row[1] == "error":          # 失敗は保存しない（次回また取りに行く）
                    failed[rid] = row[2]
                    if len(failed) <= 3:
                        print(f"  runner {rid}: 取得失敗 {row[2]}")
                else:
                    f.write("\t".join(row) + "\n")
                    cache[rid] = row
                if i % 50 == 0:
                    print(f"  {i} / {len(need)}")
        print(f"取得: {len(need)} 個（失敗 {len(failed)} 個）→ {a.cache}")

    def group_of(j):
        rid = j["runner_id"]
        if not rid:
            return "(runner 情報なし)"
        if rid in failed:
            return f"(取得失敗 {failed[rid]})"
        c = cache.get(rid)
        if c is None:
            return "(所属未取得)"
        kind = KIND.get(c[1], c[1])
        if kind == "group":
            return c[2] or "(group)"
        return f"{c[2]}（{kind}）" if c[2] else f"（{kind}）"

    for j in target:
        j["group"] = group_of(j)

    os.makedirs(a.outdir, exist_ok=True)

    def wcsv(name, headers, rows):
        with open(os.path.join(a.outdir, name), "w", encoding="utf-8-sig", newline="") as f:
            w = csv.writer(f)
            w.writerow(headers)
            w.writerows(rows)

    # ---------------------------------------------------------- 負荷（同時実行数）
    done = [j for j in target if j["e"] is not None and j["e"] > j["s"]]
    h0 = int(min(j["s"] for j in done) // 3600)
    n = int(max(j["e"] for j in done) // 3600) - h0 + 1
    busy_all, _ = sweep(done, h0, n)
    avgc = [b / 3600 for b in busy_all]
    thr = percentile(avgc, 100 - a.busy_pct) or 0
    busyset = [v >= thr and v > 0 for v in avgc]
    nb = sum(busyset)

    by_g = defaultdict(list)
    for j in done:
        by_g[j["group"]].append(j)
    gcount = Counter(j["group"] for j in target)
    grp_order = sorted(gcount, key=lambda g: (g.startswith("("), -gcount[g]))
    known = [g for g in grp_order if not g.startswith("(")]
    color = {g: PALETTE[i % len(PALETTE)] for i, g in enumerate(known)}
    for g in grp_order:
        color.setdefault(g, GRAY)

    G = {}
    for g in grp_order:
        gj = by_g.get(g, [])
        busy, hmax = sweep(gj, h0, n) if gj else ([0.0] * n, [0] * n)
        rmax = Counter()        # runner ID ごとの最大同時実行数
        per_r = defaultdict(list)
        for j in gj:
            if j["runner_id"]:
                per_r[j["runner_id"]].append(j)
        for rid, rj in per_r.items():
            rmax[rid] = max_concurrency(rj)
        cap = percentile(hmax, 99) or max(hmax, default=0) or 1
        gq = [j["queued"] for j in target if j["group"] == g and j["queued"] is not None]
        bavg = sum(b for b, f in zip(busy, busyset) if f) / (nb * 3600) if nb else 0
        starts = sorted(j["s"] for j in gj)
        ends = sorted(j["e"] for j in gj)
        G[g] = {"n": gcount[g], "busy": busy, "hmax": hmax, "sec": sum(busy), "peak": max(hmax, default=0),
                "cap": cap, "bavg": bavg, "fill": bavg / cap * 100, "q": gq,
                "pl": len({(j["project"], j["pipeline"]) for j in target if j["group"] == g}),
                "proj": len({j["project"] for j in target if j["group"] == g}),
                "rids": len(per_r), "rmax": max(rmax.values(), default=None),
                "rmax_mode": Counter(rmax.values()).most_common(1)[0][0] if rmax else None,
                "starts": starts, "ends": ends, "per_r": per_r, "rmax_by": rmax}
    tot_sec = sum(G[g]["sec"] for g in grp_order) or 1

    def running_at(g, t):
        return bisect.bisect_right(G[g]["starts"], t) - bisect.bisect_right(G[g]["ends"], t)

    # 時刻別（平日）の平均同時実行数
    hsum = {g: [0.0] * 24 for g in grp_order}
    hcnt = [0] * 24
    for k in range(n):
        hh = h0 + k
        d = (EPOCH + timedelta(days=hh // 24)).weekday()
        if d >= 5:
            continue
        hcnt[hh % 24] += 1
        for g in grp_order:
            hsum[g][hh % 24] += G[g]["busy"][k] / 3600
    hourly = {g: [hsum[g][h] / hcnt[h] if hcnt[h] else 0 for h in range(24)] for g in grp_order}

    # 長い待ち（期間全体の p95 超え）が始まった瞬間の、各グループの埋まり具合
    allq = [j["queued"] for j in target if j["queued"] is not None]
    wait_thr = percentile(allq, 95) or 0
    longs = [j for j in target if j["queued"] is not None and j["queued"] > wait_thr]
    wb = defaultdict(lambda: {"n": 0, "q": [], "fill": defaultdict(float), "ran": Counter()})
    for j in longs:
        t = j["s"] - j["queued"]          # 待ち始めた時刻
        w = wb[pname(j["project"])]
        w["n"] += 1
        w["q"].append(j["queued"])
        w["ran"][j["group"]] += 1
        for g in known:
            w["fill"][g] += min(100.0, running_at(g, t) / G[g]["cap"] * 100)
    wb_rows = sorted(wb.items(), key=lambda kv: -kv[1]["n"])

    # ---------------------------------------------------------- プロジェクト × グループ
    pg = defaultdict(Counter)
    pq = defaultdict(list)
    for j in target:
        pg[j["project"]][j["group"]] += 1
        if j["queued"] is not None:
            pq[j["project"]].append(j["queued"])
    proj_order = sorted(pg, key=lambda p: -sum(pg[p].values()))

    # ---------------------------------------------------------- パイプライン
    pls = {}
    for j in target:
        k = (j["project"], j["pipeline"])
        pl = pls.get(k)
        if pl is None:
            pl = pls[k] = {"s": j["s"], "e": j["e"], "n": 0, "g": Counter(), "q": []}
        pl["s"] = min(pl["s"], j["s"])
        if j["e"] is not None and (pl["e"] is None or j["e"] > pl["e"]):
            pl["e"] = j["e"]
        pl["n"] += 1
        pl["g"][j["group"]] += 1
        if j["queued"] is not None:
            pl["q"].append(j["queued"])

    # ---------------------------------------------------------- 週ごとの占有時間（グループ別）
    def week_of(sec):
        d = int(sec // 86400)
        return d - (EPOCH + timedelta(days=d)).weekday()
    wsec = defaultdict(lambda: defaultdict(float))
    for g in grp_order:
        for k, b in enumerate(G[g]["busy"]):
            if b:
                wsec[week_of((h0 + k) * 3600)][g] += b / 3600
    weeks = sorted(wsec)

    # ---------------------------------------------------------- CSV
    wcsv("summary_group.csv", ["runner グループ", "ジョブ数", "割合%", "実行時間h", "実行時間の割合%", "パイプライン数",
                               "プロジェクト数", "runner ID 数", "最大同時実行", "上限の目安(同時実行p99)",
                               "runner 1 台の最大同時(最頻値)", "混雑時の平均同時", "混雑時の埋まり具合%",
                               "待ちp50秒", "待ちp95秒", "待ち最大秒"],
         [[g, G[g]["n"], round(G[g]["n"] / len(target) * 100, 1), round(G[g]["sec"] / 3600, 1),
           round(G[g]["sec"] / tot_sec * 100, 1), G[g]["pl"], G[g]["proj"], G[g]["rids"], G[g]["peak"], G[g]["cap"],
           G[g]["rmax_mode"], round(G[g]["bavg"], 2), round(G[g]["fill"], 1), r1(percentile(G[g]["q"], 50)),
           r1(percentile(G[g]["q"], 95)), r1(max(G[g]["q"], default=None))] for g in grp_order])
    wcsv("hourly_group.csv", ["runner グループ"] + [f"{h}時" for h in range(24)],
         [[g] + [round(v, 2) for v in hourly[g]] for g in grp_order])
    wcsv("wait_bias.csv", ["プロジェクト名", "長い待ちの件数", "待ちp95秒"] + [f"{g} 埋まり%" for g in known]
         + [f"実行した runner: {g}" for g in grp_order],
         [[k, w["n"], r1(percentile(w["q"], 95))] + [round(w["fill"][g] / w["n"], 1) for g in known]
          + [w["ran"][g] for g in grp_order] for k, w in wb_rows])
    wcsv("project_group.csv", ["プロジェクトID", "プロジェクト名", "パス", "ジョブ数", "待ちp95秒"] + grp_order,
         [[p, pname(p), paths.get(p, ""), sum(pg[p].values()), r1(percentile(pq[p], 95))]
          + [pg[p][g] for g in grp_order] for p in proj_order])
    act_rows = []
    for g in grp_order:
        for rid, rj in G[g]["per_r"].items():
            act_rows.append([g, rid, rj[0]["runner"], dts(min(x["s"] for x in rj)), dts(max(x["e"] for x in rj)),
                             round((max(x["e"] for x in rj) - min(x["s"] for x in rj)) / 86400, 1), len(rj),
                             G[g]["rmax_by"][rid]])
    act_rows.sort(key=lambda r: (grp_order.index(r[0]), r[3]))
    wcsv("runners_activity.csv", ["runner グループ", "runner_id", "runner 名", "最初のジョブ開始", "最後のジョブ終了",
                                  "稼働日数", "ジョブ数", "最大同時実行"], act_rows)
    wcsv("pipelines_assign.csv", ["pipeline_id", "プロジェクトID", "プロジェクト名", "最初のジョブ開始", "最後のジョブ終了",
                                  "ジョブ数", "runner グループ数", "runner グループ別ジョブ数", "待ち最大秒", "待ち合計秒",
                                  "パイプラインのパス"],
         [[pid_, p, pname(p), dts(pl["s"]), dts(pl["e"]), pl["n"], len(pl["g"]),
           " / ".join(f"{g}: {c}" for g, c in pl["g"].most_common()),
           r1(max(pl["q"], default=None)), r1(sum(pl["q"])),
           f"{paths[p]}/-/pipelines/{pid_}" if p in paths else ""]
          for (p, pid_), pl in sorted(pls.items(), key=lambda kv: kv[1]["s"])])
    wcsv("jobs_assign.csv", ["job_id", "pipeline_id", "プロジェクトID", "プロジェクト名", "ジョブ名", "status", "開始",
                             "待ち秒", "実行秒", "runner_id", "runner 名", "runner グループ", "ジョブのパス"],
         [[j["jid"], j["pipeline"], j["project"], pname(j["project"]), j["name"], j["status"], dts(j["s"]),
           r1(j["queued"]), round(j["e"] - j["s"]) if j["e"] is not None else "", j["runner_id"], j["runner"],
           j["group"], f"{paths[j['project']]}/-/jobs/{j['jid']}" if j["project"] in paths else ""]
          for j in sorted(target, key=lambda x: x["s"])])

    # ---------------------------------------------------------- HTML
    host = a.host.rstrip("/")

    def link(label, path):
        if host and path:
            return f"<a href='{esc(host + '/' + path)}' target='_blank' rel='noopener'>{esc(label)}</a>"
        return esc(label)

    def stack(counter, total):
        segs = "".join(f"<span style='width:{c / total * 100:.2f}%;background:{color[g]}' "
                       f"title='{esc(g)}: {c:,}（{c / total * 100:.0f}%）'></span>"
                       for g, c in sorted(counter.items(), key=lambda kv: grp_order.index(kv[0])))
        return f"<td><div class='stack'>{segs}</div></td>"

    def sw(g):
        return f"<td class='g'><i class='sw' style='background:{color[g]}'></i>{esc(g)}</td>"

    def pbar(pct, c):
        return (f"<td data-v='{pct}'><span class='bar'><span style='width:{min(100, pct):.1f}%;background:{c}'>"
                f"</span></span>{pct:.0f}%</td>")

    legend = "<div class='legend'>" + "".join(
        f"<span><i style='background:{color[g]}'></i>{esc(g)}</span>" for g in grp_order) + "</div>"

    H = [f"<!doctype html><html lang='ja'><head><meta charset='utf-8'>"
         f"<meta name='viewport' content='width=device-width,initial-scale=1'>"
         f"<title>runner 割り当てと偏り</title><style>{CSS}</style></head><body><main>",
         "<h1>runner への割り当てと偏り</h1>",
         f"<div class='sub'>{period}（UTC{a.tz:+g}）／ runner グループ＝runner の所属グループ（GitLab API から取得）</div>",
         "<div class='kpi'>" + "".join(f"<div><span>{k}</span><b>{v}</b></div>" for k, v in [
             ("ジョブ", f"{len(target):,}"),
             ("パイプライン", f"{len(pls):,}"),
             ("プロジェクト", f"{len(pg):,}"),
             ("runner グループ", f"{len(known)}"),
             ("runner の情報が残っている割合", f"{n_rid / len(target) * 100:.0f}%"),
             ("待ち時間 p95", f"{wait_thr:,.0f} 秒"),
         ]) + "</div>"]

    # 1. グループ別
    H.append("<h2>1. runner グループ別の負荷</h2>")
    H.append("<p class='note'>ジョブ数と実行時間の割合が大きく違う、または混雑時の埋まり具合に差があれば、負荷が偏っています。</p>")
    rows = []
    for g in grp_order:
        x = G[g]
        rows.append([sw(g), num(x["n"]), pbar(x["n"] / len(target) * 100, color[g]),
                     pbar(x["sec"] / tot_sec * 100, color[g]), num(x["proj"]), num(x["rids"]),
                     num(x["peak"]), num(x["cap"]), num(x["rmax_mode"]),
                     num(x["bavg"], 1), pbar(x["fill"], "#dc2626"),
                     num(percentile(x["q"], 50), 0, " 秒"), num(percentile(x["q"], 95), 0, " 秒")])
    H.append(table(["runner グループ", "ジョブ数", "ジョブ数の割合", "実行時間の割合", "プロジェクト", "runner ID 数",
                    "最大同時", "上限の目安※1", "runner 1 台の最大同時※2", "混雑時※3の平均同時",
                    "混雑時※3の埋まり具合", "待ち p50", "待ち p95"], rows))
    H.append(f"<p class='fn'>※1 1 時間ごとの最大同時実行数の p99　※2 runner ID ごとの最大同時実行数で最も多い値"
             f"（concurrent の推定値）　※3 全体の同時実行数が上位 {a.busy_pct:g}% の時間</p>")

    # 2. 時刻別
    H.append("<h2>2. 時刻別の平均同時実行数（平日）</h2>")
    H.append("<p class='note'>数字は平均同時実行数、色は上限の目安に対する埋まり具合（濃いほど満杯に近い）。"
             "同じ時刻に濃いグループと薄いグループがあれば、空いている runner があるのに一部だけ混んでいます。</p>")
    rows = []
    for g in grp_order:
        rows.append([sw(g)] + [heat_cell(v, v / G[g]["cap"] * 100) for v in hourly[g]])
    H.append(table(["runner グループ"] + [str(h) for h in range(24)], rows, cls="heat"))

    # 3. 長い待ちの瞬間
    H.append(f"<h2>3. 長い待ちが起きた瞬間の、各 runner グループの埋まり具合</h2>")
    H.append(f"<p class='note'>待ち時間が {wait_thr:,.0f} 秒（期間全体の p95）を超えたジョブについて、"
             f"待ち始めた瞬間に各グループの runner がどれだけ埋まっていたか（上限の目安に対する %）の平均。"
             f"待っているプロジェクトが使える runner だけが満杯で、他が空いていれば、枠の分断が原因です。</p>")
    rows = []
    for k, w in wb_rows[:a.top]:
        rows.append([txt(k), num(w["n"]), num(percentile(w["q"], 95), 0, " 秒")]
                    + [heat_cell(w["fill"][g] / w["n"], w["fill"][g] / w["n"], 0) for g in known]
                    + [stack(w["ran"], w["n"])])
    H.append(table(["プロジェクト名", "長い待ちの件数", "待ち p95"] + [f"{g} 埋まり%" for g in known]
                   + ["最終的に実行した runner"], rows))

    # 4. 週ごとの推移
    H.append("<h2>4. 週ごとの実行時間（runner グループ別）</h2>")
    W, Hh, L, R, T, B = 920, 230, 50, 12, 14, 40
    vmax = max((sum(wsec[w].values()) for w in weeks), default=1) or 1
    step = 10 ** math.floor(math.log10(vmax))
    step = next(s for s in (step, step * 2, step * 5, step * 10) if vmax / s <= 5)
    ymax = math.ceil(vmax / step) * step
    bw = (W - L - R) / max(1, len(weeks))
    svg = [f"<svg viewBox='0 0 {W} {Hh}' class='chart'>"]
    for i in range(int(ymax / step) + 1):
        y = T + (Hh - T - B) * (1 - i * step / ymax)
        svg.append(f"<line x1='{L}' x2='{W - R}' y1='{y:.1f}' y2='{y:.1f}' stroke='#eee'/>"
                   f"<text x='{L - 6}' y='{y + 4:.1f}' text-anchor='end'>{i * step:,.0f}</text>")
    for i, w in enumerate(weeks):
        x = L + i * bw
        y = Hh - B
        for g in grp_order:
            v = wsec[w].get(g, 0)
            if v <= 0:
                continue
            hgt = (Hh - T - B) * v / ymax
            y -= hgt
            svg.append(f"<rect x='{x + 1:.1f}' y='{y:.1f}' width='{bw - 2:.1f}' height='{hgt:.1f}' fill='{color[g]}'>"
                       f"<title>{(EPOCH + timedelta(days=w)):%Y/%m/%d} の週 {esc(g)}: {v:,.0f} h</title></rect>")
        if i % max(1, len(weeks) // 8) == 0:
            svg.append(f"<text x='{x + bw / 2:.1f}' y='{Hh - B + 16}' text-anchor='middle'>"
                       f"{(EPOCH + timedelta(days=w)):%m/%d}</text>")
    svg.append(f"<text x='{L}' y='{Hh - 6}'>時間/週（週の月曜の日付）</text></svg>")
    H.append(legend + "".join(svg))

    # 5. プロジェクト別
    H.append(f"<h2>5. プロジェクト別の割り当て（ジョブ数の多い順に上位 {a.top}）</h2>")
    H.append("<p class='note'>各プロジェクトのジョブが、どの runner グループで実行されたか（ジョブ数の割合）。</p>")
    H.append(legend)
    rows = []
    for p in proj_order[:a.top]:
        tot = sum(pg[p].values())
        rows.append([f"<td class='t'>{link(plabel(p), paths.get(p, ''))}</td>", num(tot), stack(pg[p], tot)]
                    + [num(pg[p][g] / tot * 100 if pg[p][g] else None, 0, "%") for g in grp_order]
                    + [num(percentile(pq[p], 95), 0, " 秒")])
    H.append(table(["プロジェクト", "ジョブ数", "内訳"] + grp_order + ["待ち p95"], rows))

    # 6. 待ちが長いパイプライン・ジョブ
    H.append(f"<h2>6. 待ち時間の長いパイプライン（上位 {a.top}）</h2>")
    rows = []
    for (p, pid_), pl in sorted(pls.items(), key=lambda kv: -max(kv[1]["q"], default=0))[:a.top]:
        path = f"{paths[p]}/-/pipelines/{pid_}" if p in paths else ""
        rows.append([f"<td class='n'>{link('#' + pid_, path)}</td>", txt(plabel(p)), txt(dts(pl["s"]), "c"),
                     num(pl["n"]), stack(pl["g"], pl["n"]),
                     txt(" / ".join(f"{g}: {c}" for g, c in pl["g"].most_common())),
                     num(max(pl["q"], default=None), 0, " 秒")])
    H.append(table(["パイプライン", "プロジェクト", "開始", "ジョブ数", "内訳", "runner グループ別ジョブ数", "待ち 最大"], rows))

    H.append(f"<h2>7. 待ち時間の長いジョブ（上位 {a.top}）</h2>")
    rows = []
    for j in sorted((j for j in target if j["queued"] is not None), key=lambda j: -j["queued"])[:a.top]:
        path = f"{paths[j['project']]}/-/jobs/{j['jid']}" if j["project"] in paths else ""
        rows.append([f"<td class='n'>{link('#' + str(j['jid']), path)}</td>", txt(plabel(j["project"])),
                     txt(j["name"]), txt(dts(j["s"]), "c"), num(j["queued"], 0, " 秒"), sw(j["group"])])
    H.append(table(["ジョブ", "プロジェクト", "ジョブ名", "開始", "待ち", "runner グループ"], rows))

    # 8. runner ID ごと
    H.append("<h2>8. runner（ID）ごとの稼働</h2>")
    H.append("<p class='note'>runner は入れ替わるため ID が多数あります。最大同時実行数がそろっていれば、それが 1 台あたりの上限"
             "（concurrent）と考えられます。全件は runners_activity.csv。</p>")
    H.append(f"<details><summary>{len(act_rows)} 個の runner を表示</summary>")
    H.append(table(["runner グループ", "runner_id", "runner 名", "最初のジョブ", "最後のジョブ", "稼働日数", "ジョブ数", "最大同時"],
                   [[sw(r[0]), num(int(r[1])), txt(r[2]), txt(r[3], "c"), txt(r[4], "c"), num(r[5], 1), num(r[6]),
                     num(r[7])] for r in act_rows]))
    H.append("</details>")

    H.append(f"</main><script>{SORT_JS}</script></body></html>")
    with open(os.path.join(a.outdir, "report.html"), "w", encoding="utf-8") as f:
        f.write("".join(H))

    # ---------------------------------------------------------- 画面に要点
    print("\n== runner グループ別 ==")
    print(f"  {'runner グループ':<30} {'ジョブ':>7} {'時間割合':>7} {'上限目安':>6} {'混雑時埋まり':>9} {'待ちp95':>8}")
    for g in grp_order:
        x = G[g]
        p95 = percentile(x["q"], 95)
        print(f"  {g[:30]:<30} {x['n']:>7,} {x['sec'] / tot_sec * 100:>6.1f}% {x['cap']:>6} {x['fill']:>8.0f}% "
              f"{('-' if p95 is None else f'{p95:.0f}秒'):>8}")
    print(f"\n出力: {os.path.join(a.outdir, 'report.html')}（CSV も同じフォルダ）")


if __name__ == "__main__":
    main()
