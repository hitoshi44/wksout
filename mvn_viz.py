#!/usr/bin/env python3
"""
Maven パイプライン可視化ツール

ジョブごとの mvnw コマンドを書いた YAML を読み込み、
「ジョブ × フェーズ」のマトリクスを HTML に出力する。
前のジョブ（または同じジョブの前のコマンド）で実行済みのフェーズを「重複」として色分けする。

使い方:
    python maven_phase_viz.py pipeline.yml -o pipeline.html
    python maven_phase_viz.py pipeline.yml -e PACKAGES=verify -e OPTS=-DskipTests
    python maven_phase_viz.py pipeline.yml --all-phases     # 中間フェーズも全部表示

入力フォーマット（どれでも可）:
    - job1
      - install javadoc:aggregate
      - verify
    - job2:
      - ./mvnw -P gitlab -pl test $PACKAGES $OPTS

    build-java:
      - "./mvnw install"

外部ライブラリ不要（標準ライブラリのみ）。
"""

from __future__ import annotations

import argparse
import html
import os
import re
import shlex
import sys
from dataclasses import dataclass, field

# ---------------------------------------------------------------------------
# Maven ライフサイクル定義
# ---------------------------------------------------------------------------

CLEAN = ["pre-clean", "clean", "post-clean"]
DEFAULT = [
    "validate", "initialize",
    "generate-sources", "process-sources",
    "generate-resources", "process-resources",
    "compile", "process-classes",
    "generate-test-sources", "process-test-sources",
    "generate-test-resources", "process-test-resources",
    "test-compile", "process-test-classes",
    "test",
    "prepare-package", "package",
    "pre-integration-test", "integration-test", "post-integration-test",
    "verify", "install", "deploy",
]
SITE = ["pre-site", "site", "post-site", "site-deploy"]
LIFECYCLES = [("clean", CLEAN), ("default", DEFAULT), ("site", SITE)]

PHASE_INFO = {p: (lc, i) for lc, phases in LIFECYCLES for i, p in enumerate(phases)}

# 通常表示するフェーズ（--all-phases なしの場合）
KEY_PHASES = {
    "clean", "validate", "compile", "test-compile", "test", "package",
    "integration-test", "verify", "install", "deploy", "site", "site-deploy",
}

# ライフサイクルを fork するゴール -> fork 先のフェーズ
# 各ゴールの公式ドキュメント「Invokes the execution of the following lifecycle phase
# prior to executing itself」の記載に基づく（maven-javadoc-plugin / maven-source-plugin の現行版）。
# javadoc:jar / javadoc:test-jar / *-no-fork は fork しない。
FORKS = {
    "javadoc:aggregate": "compile",
    "javadoc:aggregate-jar": "compile",
    "javadoc:test-aggregate": "test-compile",
    "javadoc:test-aggregate-jar": "test-compile",
    "javadoc:javadoc": "generate-sources",
    "javadoc:test-javadoc": "generate-test-sources",
    "source:jar": "generate-sources",
    "source:test-jar": "generate-sources",
}

MAVEN_EXECUTABLES = {"mvnw", "mvn", "mvnw.cmd", "mvn.cmd", "mvnd"}

# 次のトークンを引数として取るオプション
OPTS_WITH_ARG = {
    "-f", "--file", "-s", "--settings", "-gs", "--global-settings",
    "-T", "--threads", "-rf", "--resume-from", "-t", "--toolchains",
    "-l", "--log-file", "-D", "--define", "-b", "--builder",
}


def expand_phase(phase: str) -> list[str]:
    """phase までの、同じライフサイクルの全フェーズを返す。"""
    lc, idx = PHASE_INFO[phase]
    return dict(LIFECYCLES)[lc][: idx + 1]


# ---------------------------------------------------------------------------
# データ構造
# ---------------------------------------------------------------------------

@dataclass
class Scope:
    modules: frozenset[str] | None  # None = 全モジュール
    also_make: bool = False

    def label(self) -> str:
        if self.modules is None:
            return "全モジュール"
        s = "-pl " + ",".join(sorted(self.modules))
        return s + (" -am" if self.also_make else "")


def coverage(earlier: Scope, cur: Scope) -> str:
    """earlier の実行が cur をどれだけカバーしているか: full / partial / none"""
    if earlier.modules is None:
        return "full"
    if cur.modules is None:
        return "partial"
    if cur.also_make:  # 依存モジュールの実際の範囲は不明
        return "partial" if earlier.modules & cur.modules else "none"
    if cur.modules <= earlier.modules:
        return "full"
    if cur.modules & earlier.modules:
        return "partial"
    return "none"


@dataclass
class Command:
    job: str
    raw: str
    tasks: list[tuple[str, str]] = field(default_factory=list)  # ("phase"|"goal", name)
    scope: Scope = field(default_factory=lambda: Scope(None))
    profiles: list[str] = field(default_factory=list)
    skip_tests: bool = False       # -DskipTests: 単体テスト（surefire）の実行のみスキップ
    skip_its: bool = False         # -DskipITs: 結合テスト（failsafe）の実行をスキップ
    skip_test_compile: bool = False  # -Dmaven.test.skip=true: テストのコンパイルも含めて両方スキップ
    warnings: list[str] = field(default_factory=list)
    cells: dict = field(default_factory=dict)  # key -> Cell


@dataclass
class Cell:
    statuses: list[str] = field(default_factory=list)  # new / dup / partial / skip
    from_fork: bool = False


# ---------------------------------------------------------------------------
# 入力の読み込み
# ---------------------------------------------------------------------------

def _unquote(s: str) -> str:
    s = s.strip()
    if len(s) >= 2 and s[0] == s[-1] and s[0] in "\"'":
        return s[1:-1]
    return s


def parse_pipeline(text: str) -> list[tuple[str, list[str]]]:
    """インデントベースの緩い YAML パーサ。[(job名, [コマンド, ...]), ...] を返す。"""
    jobs: list[tuple[str, list[str]]] = []
    top_indent = None
    for lineno, line in enumerate(text.splitlines(), 1):
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        line = line.replace("\t", "    ")
        indent = len(line) - len(line.lstrip(" "))
        content = line.strip()
        if content.startswith("- "):
            content = content[2:].strip()
        elif content == "-":
            continue
        if top_indent is None:
            top_indent = indent

        if indent <= top_indent:
            # ジョブ行。"job: cmd" のようにインラインでコマンドが書かれている場合も許容
            if ": " in content:
                name, cmd = content.split(": ", 1)
                jobs.append((_unquote(name), [_unquote(cmd)] if cmd.strip() else []))
            else:
                jobs.append((_unquote(content.rstrip(":")), []))
        else:
            if not jobs:
                raise ValueError(f"{lineno} 行目: ジョブ名より前にコマンドがあります")
            jobs[-1][1].append(_unquote(content))
    return jobs


def _normalize_goal(token: str) -> str:
    parts = token.split(":")
    if len(parts) >= 3:  # groupId:artifactId[:version]:goal
        artifact = parts[1]
        prefix = re.sub(r"^maven-|-maven-plugin$|-plugin$", "", artifact)
        prefix = re.sub(r"-plugin$", "", prefix)
        return f"{prefix}:{parts[-1]}"
    return token


def parse_command(job: str, raw: str, env: dict[str, str]) -> Command:
    cmd = Command(job=job, raw=raw)

    def sub_var(m: re.Match) -> str:
        name = m.group(1) or m.group(2)
        return env.get(name, m.group(0))

    expanded = re.sub(r"\$\{(\w+)\}|\$(\w+)", sub_var, raw)
    try:
        tokens = shlex.split(expanded)
    except ValueError:
        tokens = expanded.split()

    while tokens and os.path.basename(tokens[0]) in MAVEN_EXECUTABLES:
        tokens.pop(0)

    modules: set[str] | None = None
    i = 0
    while i < len(tokens):
        t = tokens[i]
        nxt = tokens[i + 1] if i + 1 < len(tokens) else ""
        if t.startswith("$"):
            cmd.warnings.append(f"{t} は未展開（-e {t.strip('${}')}=... で指定できます）")
        elif t in ("-pl", "--projects"):
            modules = {m.strip().lstrip(":") for m in nxt.split(",") if m.strip()}
            i += 1
        elif t.startswith("--projects="):
            modules = {m.strip().lstrip(":") for m in t.split("=", 1)[1].split(",") if m.strip()}
        elif t in ("-am", "--also-make"):
            cmd.scope.also_make = True
        elif t in ("-amd", "--also-make-dependents"):
            cmd.scope.also_make = True
            cmd.warnings.append("-amd: 依存元モジュールの範囲は推定できないため一部重複扱い")
        elif t in ("-P", "--activate-profiles"):
            cmd.profiles += nxt.split(",")
            i += 1
        elif t.startswith("-P") and len(t) > 2:
            cmd.profiles += t[2:].split(",")
        elif t in ("-D", "--define"):
            _apply_define(cmd, nxt)
            i += 1
        elif t.startswith("-D"):
            _apply_define(cmd, t[2:])
        elif t in OPTS_WITH_ARG:
            i += 1
        elif t.startswith("-"):
            pass  # -B, -U, -q, -e などのフラグ
        elif ":" in t:
            goal = _normalize_goal(t)
            a, b = goal.split(":", 1)
            if goal not in FORKS and f"{b}:{a}" in FORKS:
                cmd.warnings.append(f"{goal} は {b}:{a} の書き間違い？（{b}:{a} として扱いました）")
                goal = f"{b}:{a}"
            cmd.tasks.append(("goal", goal))
        elif t in PHASE_INFO:
            cmd.tasks.append(("phase", t))
        else:
            cmd.warnings.append(f"不明なトークン: {t}")
        i += 1

    if modules is not None:
        cmd.scope.modules = frozenset(modules)
    if not cmd.tasks:
        cmd.warnings.append("フェーズ・ゴールが見つかりません")
    return cmd


def _apply_define(cmd: Command, kv: str) -> None:
    key, _, val = kv.partition("=")
    truthy = val in ("", "true")
    if key == "skipTests" and truthy:
        cmd.skip_tests = True
    elif key == "skipITs" and truthy:
        cmd.skip_its = True
    elif key == "maven.test.skip" and truthy:
        cmd.skip_tests = cmd.skip_its = cmd.skip_test_compile = True


# ---------------------------------------------------------------------------
# 重複の解析
# ---------------------------------------------------------------------------

def analyze(commands: list[Command]) -> None:
    history: dict[tuple[str, str], list[Scope]] = {}

    for cmd in commands:
        def record(key: tuple[str, str], fork: bool = False) -> None:
            cell = cmd.cells.setdefault(key, Cell())
            cell.from_fork |= fork
            kind, name = key
            skipped = kind == "phase" and (
                (cmd.skip_tests and name == "test")
                or (cmd.skip_its and name == "integration-test")
                or (cmd.skip_test_compile and name in ("test-compile", "generate-test-sources",
                                                       "process-test-sources", "process-test-classes"))
            )
            if skipped:
                cell.statuses.append("skip")
                return
            if any(s != "skip" for s in cell.statuses):
                status = "dup"  # 同じコマンド内で2回目以降
            else:
                covs = [coverage(e, cmd.scope) for e in history.get(key, [])]
                status = "dup" if "full" in covs else "partial" if "partial" in covs else "new"
            cell.statuses.append(status)
            history.setdefault(key, []).append(cmd.scope)

        for kind, name in cmd.tasks:
            if kind == "phase":
                for p in expand_phase(name):
                    record(("phase", p))
            else:
                fork_to = FORKS.get(name)
                if fork_to:
                    for p in expand_phase(fork_to):
                        record(("phase", p), fork=True)
                record(("goal", name))


def columns(commands: list[Command], all_phases: bool) -> list[tuple[str, str]]:
    explicit = {n for c in commands for k, n in c.tasks if k == "phase"}
    reached = {n for c in commands for (k, n) in c.cells if k == "phase"}
    cols: list[tuple[str, str]] = []
    for _, phases in LIFECYCLES:
        for p in phases:
            if p in reached and (all_phases or p in KEY_PHASES or p in explicit):
                cols.append(("phase", p))
    seen = []
    for c in commands:
        for k, n in c.tasks:
            if k == "goal" and n not in seen:
                seen.append(n)
    cols += [("goal", g) for g in seen]
    return cols


# ---------------------------------------------------------------------------
# HTML 出力
# ---------------------------------------------------------------------------

LABELS = {"new": "実行", "dup": "重複", "partial": "一部重複", "skip": "スキップ"}

CSS = """
:root{--bg:#fafaf8;--fg:#2c2c2a;--muted:#6b6a65;--line:#d9d7cf;--card:#fff;
--new-bg:#e1f5ee;--new-fg:#085041;--new-bd:#5dcaa5;
--dup-bg:#faece7;--dup-fg:#712b13;--dup-bd:#f0997b;
--part-bg:#faeeda;--part-fg:#633806;--part-bd:#ef9f27;
--skip-bg:#f1efe8;--skip-fg:#5f5e5a;--skip-bd:#b4b2a9;--warn:#a32d2d}
@media (prefers-color-scheme:dark){:root{--bg:#1f1f1d;--fg:#e8e6df;--muted:#a3a19a;--line:#3d3d3a;--card:#2a2a28;
--new-bg:#085041;--new-fg:#e1f5ee;--new-bd:#1d9e75;
--dup-bg:#712b13;--dup-fg:#faece7;--dup-bd:#d85a30;
--part-bg:#633806;--part-fg:#faeeda;--part-bd:#ba7517;
--skip-bg:#444441;--skip-fg:#d3d1c7;--skip-bd:#888780;--warn:#f09595}}
*{box-sizing:border-box}
body{margin:0;padding:24px 16px;background:var(--bg);color:var(--fg);
font-family:system-ui,-apple-system,"Hiragino Sans","Noto Sans JP",sans-serif;font-size:14px;line-height:1.6}
h1{font-size:20px;font-weight:600;margin:0 0 4px}
.sub{color:var(--muted);margin:0 0 20px}
.wrap{overflow-x:auto;background:var(--card);border:1px solid var(--line);border-radius:12px}
table{border-collapse:collapse;width:100%;min-width:720px}
th,td{padding:8px 10px;border-bottom:1px solid var(--line);text-align:center;vertical-align:middle}
thead th{font-weight:500;font-size:12px;color:var(--muted);white-space:nowrap}
thead th.goal{font-family:ui-monospace,Menlo,monospace}
th.job{text-align:left;white-space:nowrap;border-right:1px solid var(--line);font-size:14px}
td.cmd{text-align:left;min-width:220px}
td.cmd code{font-family:ui-monospace,Menlo,monospace;font-size:12px;word-break:break-all}
.badges{margin-top:4px;display:flex;flex-wrap:wrap;gap:4px}
.badge{font-size:11px;padding:1px 6px;border-radius:4px;border:1px solid var(--line);color:var(--muted)}
.warn{font-size:11px;color:var(--warn);margin-top:2px}
.cell{display:inline-block;min-width:64px;padding:4px 6px;border-radius:6px;font-size:12px;border:1px solid;white-space:nowrap}
.cell small{display:block;font-size:10px;opacity:.8}
.new{background:var(--new-bg);color:var(--new-fg);border-color:var(--new-bd)}
.dup{background:var(--dup-bg);color:var(--dup-fg);border-color:var(--dup-bd)}
.partial{background:var(--part-bg);color:var(--part-fg);border-color:var(--part-bd)}
.skip{background:var(--skip-bg);color:var(--skip-fg);border-color:var(--skip-bd);border-style:dashed}
td.sum{font-size:12px;white-space:nowrap}
tr.lastjob td,tr.lastjob th{border-bottom:2px solid var(--line)}
.legend{display:flex;flex-wrap:wrap;gap:16px;margin:16px 0 8px;font-size:12px;color:var(--muted);align-items:center}
.notes{font-size:12px;color:var(--muted);margin-top:12px;padding-left:18px}
"""


def render_cell(cell: Cell | None) -> str:
    if cell is None or not cell.statuses:
        return ""
    real = [s for s in cell.statuses if s != "skip"]
    if not real:
        cls = "skip"
    elif "dup" in real:
        cls = "dup"
    elif "partial" in real:
        cls = "partial"
    else:
        cls = "new"
    text = LABELS[cell.statuses[0]]
    if len(real) > 1:
        text += f" ×{len(real)}"
    sub = "<small>fork 含む</small>" if cell.from_fork else ""
    tip = " → ".join(LABELS[s] for s in cell.statuses)
    return f'<span class="cell {cls}" title="{html.escape(tip)}">{text}{sub}</span>'


def render_html(commands: list[Command], cols: list[tuple[str, str]], title: str) -> str:
    esc = html.escape
    head = "".join(
        f'<th class="{k}">{esc(n)}</th>' for k, n in cols
    )
    rows = []
    total_dup = total_cells = 0
    jobs: list[tuple[str, list[Command]]] = []
    for c in commands:
        if jobs and jobs[-1][0] == c.job:
            jobs[-1][1].append(c)
        else:
            jobs.append((c.job, [c]))

    for job, cmds in jobs:
        for i, c in enumerate(cmds):
            badges = [c.scope.label()]
            if c.profiles:
                badges.append("-P " + ",".join(c.profiles))
            if c.skip_test_compile:
                badges.append("maven.test.skip")
            else:
                if c.skip_tests:
                    badges.append("skipTests")
                if c.skip_its:
                    badges.append("skipITs")
            badge_html = "".join(f'<span class="badge">{esc(b)}</span>' for b in badges)
            warn_html = "".join(f'<div class="warn">⚠ {esc(w)}</div>' for w in c.warnings)
            cells = [c.cells.get(col) for col in cols]
            n_dup = sum(1 for x in cells if x and any(s in ("dup", "partial") for s in x.statuses))
            n_run = sum(1 for x in cells if x and any(s != "skip" for s in x.statuses))
            total_dup += n_dup
            total_cells += n_run
            tr_cls = ' class="lastjob"' if i == len(cmds) - 1 else ""
            job_th = (f'<th class="job" rowspan="{len(cmds)}">{esc(job)}</th>' if i == 0 else "")
            tds = "".join(f"<td>{render_cell(x)}</td>" for x in cells)
            sum_txt = f"重複 {n_dup} / {n_run}" if n_run else "-"
            rows.append(
                f"<tr{tr_cls}>{job_th}"
                f'<td class="cmd"><code>{esc(c.raw)}</code><div class="badges">{badge_html}</div>{warn_html}</td>'
                f'{tds}<td class="sum">{sum_txt}</td></tr>'
            )

    legend = "".join(
        f'<span><span class="cell {k}">{v}</span></span>'
        for k, v in [("new", "実行"), ("dup", "重複"), ("partial", "一部重複"), ("skip", "スキップ")]
    )
    return f"""<!doctype html>
<html lang="ja"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{esc(title)}</title><style>{CSS}</style></head>
<body>
<h1>{esc(title)}</h1>
<p class="sub">{len(jobs)} ジョブ / {len(commands)} コマンド ・ 表示中のマスのうち重複 {total_dup} / {total_cells}</p>
<div class="wrap"><table>
<thead><tr><th class="job">ジョブ</th><th style="text-align:left">コマンド</th>{head}<th>重複</th></tr></thead>
<tbody>{"".join(rows)}</tbody>
</table></div>
<div class="legend">{legend}</div>
<ul class="notes">
<li>フェーズを指定すると、同じライフサイクルの前のフェーズもすべて実行される前提で展開しています。</li>
<li>「重複」= それより前のジョブ・コマンドで、同じかより広いモジュール範囲ですでに実行済み。「一部重複」= 範囲が一部だけ重なる。</li>
<li>「×2」= 同じコマンド内で2回実行（fork するゴールによる再実行など）。</li>
<li>fork するゴールは maven-javadoc-plugin / maven-source-plugin の公式ドキュメントの記載に基づいて登録しています。javadoc:jar と -no-fork 版は fork しません。</li>
<li>-DskipTests は単体テスト（surefire）だけをスキップ扱いにしています。Failsafe 3.6.0 以降は -DskipTests で結合テストがスキップされないためです（古い Failsafe ではスキップされます）。結合テストは -DskipITs、両方は -Dmaven.test.skip=true。</li>
<li>ジョブ間で target/ やローカルリポジトリを共有していても、Maven はフェーズを再実行します（コンパイルの差分スキップは除く）。</li>
</ul>
</body></html>"""


# ---------------------------------------------------------------------------

def main() -> int:
    ap = argparse.ArgumentParser(description="Maven パイプラインのフェーズ重複を HTML で可視化")
    ap.add_argument("input", help="ジョブとコマンドを書いた YAML ファイル（- で標準入力）")
    ap.add_argument("-o", "--output", default="pipeline.html", help="出力 HTML（既定: pipeline.html）")
    ap.add_argument("-e", "--env", action="append", default=[], metavar="KEY=VAL",
                    help="$KEY を展開する値（複数指定可）")
    ap.add_argument("--all-phases", action="store_true", help="中間フェーズもすべて列に表示")
    ap.add_argument("--title", default="Maven パイプライン フェーズ実行マップ")
    args = ap.parse_args()

    text = sys.stdin.read() if args.input == "-" else open(args.input, encoding="utf-8").read()
    env = {}
    for kv in args.env:
        k, _, v = kv.partition("=")
        env[k] = v

    jobs = parse_pipeline(text)
    if not jobs:
        print("ジョブが見つかりません", file=sys.stderr)
        return 1
    commands = [parse_command(job, raw, env) for job, cmds in jobs for raw in cmds]
    analyze(commands)
    cols = columns(commands, args.all_phases)

    with open(args.output, "w", encoding="utf-8") as f:
        f.write(render_html(commands, cols, args.title))
    print(f"wrote {args.output}")
    for c in commands:
        for w in c.warnings:
            print(f"[{c.job}] {w}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
