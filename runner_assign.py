#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import argparse
import csv
import glob
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
                if row and row[0].strip().isdigit():
                    row += [""] * (6 - len(row))
                    m[row[0].strip()] = row[:6]
    return m


# ------------------------------------------------------------------ API
def fetch_runner(host, token, rid, ctx):
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


# ------------------------------------------------------------------ メイン
def main():
    ap = argparse.ArgumentParser(description="ジョブ・パイプラインの runner（所属グループ）割り当て")
    ap.add_argument("inputs", nargs="*", default=["jobs_*.tsv"])
    ap.add_argument("--host", default=os.environ.get("GITLAB_HOST", ""),
                    help="GitLab の URL（例: https://gitlab.example.com）。環境変数 GITLAB_HOST でも可")
    ap.add_argument("--no-api", action="store_true", help="API を呼ばず、runners.tsv にある分だけ使う")
    ap.add_argument("--cache", default="runners.tsv", help="runner 情報の保存先（既定 runners.tsv）")
    ap.add_argument("--since", help="この日以降に開始したジョブだけ（例: 2026-09-01）。省略時は自動")
    ap.add_argument("--until", help="この日より前に開始したジョブだけ")
    ap.add_argument("--coverage", type=float, default=80,
                    help="自動で期間を選ぶとき、runner_id が残っているジョブが何 %% 以上の週を使うか（既定 80）")
    ap.add_argument("--tz", type=float, default=9, help="表示タイムゾーンの UTC からの時差（既定 9）")
    ap.add_argument("--names", default="projects.tsv", help="プロジェクト ID<TAB>パス（任意）")
    ap.add_argument("--teams", default="teams.csv", help="プロジェクト ID,プロジェクト名（任意）")
    ap.add_argument("--outdir", default="runner_assign")
    a = ap.parse_args()
    try:
        sys.stdout.reconfigure(errors="replace")
    except Exception:
        pass

    jobs = load_jobs(a.inputs, a.tz * 3600)
    if not jobs:
        sys.exit("開始済みのジョブがありません")
    paths = load_map(a.names, "\t")
    tnames = load_map(a.teams, ",")
    if paths:
        print(f"プロジェクトのパス: {a.names} から {len(paths)} 件")
    if tnames:
        print(f"プロジェクト名: {a.teams} から {len(tnames)} 件")

    def pname(pid):
        return tnames.get(pid) or (paths[pid].split("/")[-1] if pid in paths else f"#{pid}")

    def dts(sec):
        return (EPOCH_DT + timedelta(seconds=sec)).strftime("%Y-%m-%d %H:%M") if sec is not None else ""

    def week_of(sec):
        d = int(sec // 86400)
        return d - (EPOCH + timedelta(days=d)).weekday()   # 週の月曜（通し日）

    # --- 週ごとの runner_id の残り具合 → 期間を決める ---
    wk_tot, wk_ok = Counter(), Counter()
    for j in jobs:
        w = week_of(j["s"])
        wk_tot[w] += 1
        if j["runner_id"]:
            wk_ok[w] += 1
    weeks = sorted(wk_tot)
    os.makedirs(a.outdir, exist_ok=True)

    def wcsv(name, headers, rows):
        with open(os.path.join(a.outdir, name), "w", encoding="utf-8-sig", newline="") as f:
            w = csv.writer(f)
            w.writerow(headers)
            w.writerows(rows)

    wcsv("coverage_weekly.csv", ["週(月曜)", "開始済みジョブ数", "runner_id ありのジョブ数", "割合%"],
         [[(EPOCH + timedelta(days=w)).isoformat(), wk_tot[w], wk_ok[w], round(wk_ok[w] / wk_tot[w] * 100, 1)]
          for w in weeks])

    def local_sec(d):
        return (date.fromisoformat(d) - EPOCH).days * 86400
    if a.since:
        lo = local_sec(a.since)
    else:
        start = None
        for w in reversed(weeks):
            if wk_ok[w] / wk_tot[w] * 100 >= a.coverage:
                start = w
            else:
                break
        if start is None:
            sys.exit(f"runner_id が残っているジョブが {a.coverage:g}% 以上の週がありません。"
                     f"{a.outdir}/coverage_weekly.csv を見て --since で期間を指定してください")
        lo = start * 86400
    hi = local_sec(a.until) if a.until else math.inf
    target = [j for j in jobs if lo <= j["s"] < hi]
    if not target:
        sys.exit("対象期間にジョブがありません")
    first = min(j["s"] for j in target)
    last = max(j["s"] for j in target)
    n_rid = sum(1 for j in target if j["runner_id"])
    print(f"対象期間: {dts(first)[:10]} 〜 {dts(last)[:10]}  ジョブ {len(target):,} 件"
          f"（runner_id あり {n_rid / len(target) * 100:.0f}%）")

    # --- runner ID → 所属グループ ---
    cache = load_runner_cache(a.cache)
    need = sorted({j["runner_id"] for j in target if j["runner_id"]} - set(cache), key=int)
    print(f"runner ID: 対象期間に {len({j['runner_id'] for j in target if j['runner_id']})} 個"
          f" / {a.cache} に保存済み {len(cache)} 個 / 未取得 {len(need)} 個")
    if need and not a.no_api:
        token = os.environ.get("TOKEN", "")
        if not a.host or not token:
            sys.exit("未取得の runner があります。--host（または GITLAB_HOST）と環境変数 TOKEN を設定するか、"
                     "--no-api で取得せずに進めてください")
        ctx = ssl.create_default_context()
        if os.environ.get("REQUESTS_CA_BUNDLE") or os.environ.get("SSL_CERT_FILE"):
            ctx.load_verify_locations(os.environ.get("REQUESTS_CA_BUNDLE") or os.environ.get("SSL_CERT_FILE"))
        n_err = 0
        with open(a.cache, "a", encoding="utf-8", newline="") as f:
            for i, rid in enumerate(need, 1):
                row = fetch_runner(a.host, token, rid, ctx)
                if isinstance(row, tuple):           # 接続できない：止めて原因を表示
                    sys.exit(f"GitLab に接続できません（runner {rid}）: {row[1]}")
                if row[1] == "error":           # 失敗は保存しない（次回また取りに行く）
                    n_err += 1
                    if n_err <= 3:
                        print(f"  runner {rid}: 取得失敗 {row[2]}")
                else:
                    f.write("\t".join(row) + "\n")
                cache[rid] = row
                if i % 50 == 0:
                    print(f"  {i} / {len(need)}")
        print(f"取得: {len(need)} 個（失敗 {n_err} 個）→ {a.cache}")

    def group_of(j):
        rid = j["runner_id"]
        if not rid:
            return "(runner 情報なし)"
        c = cache.get(rid)
        if c is None:
            return "(所属未取得)"
        if c[1] == "error":
            return f"(取得失敗 {c[2]})"
        kind = KIND.get(c[1], c[1])
        return f"{c[2]}（{kind}）" if c[2] else f"（{kind}）"

    for j in target:
        j["group"] = group_of(j)

    # --- 所属グループ別 ---
    gs = defaultdict(lambda: {"n": 0, "pl": set(), "proj": set(), "rid": set(), "q": [], "sec": 0.0})
    for j in target:
        g = gs[j["group"]]
        g["n"] += 1
        g["pl"].add((j["project"], j["pipeline"]))
        g["proj"].add(j["project"])
        if j["runner_id"]:
            g["rid"].add(j["runner_id"])
        if j["queued"] is not None:
            g["q"].append(j["queued"])
        if j["e"] is not None:
            g["sec"] += j["e"] - j["s"]
    grp_order = sorted(gs, key=lambda k: -gs[k]["n"])
    wcsv("summary_group.csv", ["所属グループ", "ジョブ数", "割合%", "パイプライン数", "プロジェクト数", "runner ID 数",
                               "実行時間h", "待ちp50秒", "待ちp95秒", "待ち最大秒"],
         [[k, gs[k]["n"], round(gs[k]["n"] / len(target) * 100, 1), len(gs[k]["pl"]), len(gs[k]["proj"]),
           len(gs[k]["rid"]), round(gs[k]["sec"] / 3600, 1), r1(percentile(gs[k]["q"], 50)),
           r1(percentile(gs[k]["q"], 95)), r1(max(gs[k]["q"], default=None))] for k in grp_order])

    # --- プロジェクト × 所属グループ ---
    pg = defaultdict(Counter)
    pq = defaultdict(list)
    for j in target:
        pg[j["project"]][j["group"]] += 1
        if j["queued"] is not None:
            pq[j["project"]].append(j["queued"])
    proj_order = sorted(pg, key=lambda p: -sum(pg[p].values()))
    wcsv("project_group.csv", ["プロジェクトID", "プロジェクト名", "パス", "ジョブ数", "待ちp95秒"] + grp_order,
         [[p, pname(p), paths.get(p, ""), sum(pg[p].values()), r1(percentile(pq[p], 95))]
          + [pg[p][g] for g in grp_order] for p in proj_order])

    # --- パイプライン ---
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
    wcsv("pipelines_assign.csv", ["pipeline_id", "プロジェクトID", "プロジェクト名", "最初のジョブ開始", "最後のジョブ終了",
                                  "ジョブ数", "所属グループ数", "所属グループ別ジョブ数", "待ち最大秒", "待ち合計秒",
                                  "パイプラインのパス"],
         [[pid_, p, pname(p), dts(pl["s"]), dts(pl["e"]), pl["n"], len(pl["g"]),
           " / ".join(f"{g}: {c}" for g, c in pl["g"].most_common()),
           r1(max(pl["q"], default=None)), r1(sum(pl["q"])),
           f"{paths[p]}/-/pipelines/{pid_}" if p in paths else ""]
          for (p, pid_), pl in sorted(pls.items(), key=lambda kv: kv[1]["s"])])

    # --- ジョブ ---
    wcsv("jobs_assign.csv", ["job_id", "pipeline_id", "プロジェクトID", "プロジェクト名", "ジョブ名", "status", "開始",
                             "待ち秒", "実行秒", "runner_id", "runner 名", "所属グループ", "ジョブのパス"],
         [[j["jid"], j["pipeline"], j["project"], pname(j["project"]), j["name"], j["status"], dts(j["s"]),
           r1(j["queued"]), round(j["e"] - j["s"]) if j["e"] is not None else "", j["runner_id"], j["runner"],
           j["group"], f"{paths[j['project']]}/-/jobs/{j['jid']}" if j["project"] in paths else ""]
          for j in sorted(target, key=lambda x: x["s"])])

    # --- 画面に要点 ---
    print("\n== 所属グループ別 ==")
    print(f"  {'所属グループ':<36} {'ジョブ':>7} {'割合':>6} {'プロジェクト':>6} {'runner':>6} {'待ちp95':>8}")
    for k in grp_order:
        g = gs[k]
        p95 = percentile(g["q"], 95)
        print(f"  {k[:36]:<36} {g['n']:>7,} {g['n'] / len(target) * 100:>5.1f}% {len(g['proj']):>6} "
              f"{len(g['rid']):>6} {('-' if p95 is None else f'{p95:.0f}秒'):>8}")
    print(f"\n出力: {a.outdir}/（summary_group.csv, project_group.csv, pipelines_assign.csv, jobs_assign.csv）")


if __name__ == "__main__":
    main()
