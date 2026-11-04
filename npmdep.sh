#!/usr/bin/env bash
# check-eol.sh の出力を、Excel で開ける CSV（UTF-8 BOM 付き）に変換してファイルに書き出す
#
# 使い方:
#   ./check-eol.sh | ./eol-to-csv.sh                 # → eol.csv に書き出す
#   ./check-eol.sh | ./eol-to-csv.sh - out.csv       # → 出力先を指定
#   ./eol-to-csv.sh result.txt                       # 保存済みの出力から変換 → eol.csv
#   ./eol-to-csv.sh result.txt out.csv

in="${1:--}"
out="${2:-eol.csv}"

if [ "$in" != "-" ] && [ ! -f "$in" ]; then
  echo "エラー: 入力ファイルが見つかりません: $in" >&2
  exit 1
fi

{
  # UTF-8 BOM（Excel で日本語が文字化けしないように必須）
  printf '\357\273\277'

  # データ行（先頭が STATUS 値の行）だけを拾う
  # 最初の5列は空白を含まないので空白で区切り、残りすべてを note とする
  cat "$in" | awk '
    function q(s) { gsub(/"/, "\"\"", s); return "\"" s "\"" }
    function row(a, b, c, d, e, f) {
      printf "%s,%s,%s,%s,%s,%s\r\n", q(a), q(b), q(c), q(d), q(e), q(f)
    }
    BEGIN { row("status", "package", "current", "latest", "released", "note") }
    $1 ~ /^(DEPRECATED|MAINTAINED|UNKNOWN|ERROR)$/ {
      note = $0
      for (i = 1; i <= 5; i++) sub(/^[ \t]*[^ \t]+/, "", note)
      sub(/^[ \t]+/, "", note); sub(/[ \t\r]+$/, "", note)
      row($1, $2, $3, $4, $5, note)
      n++
    }
    END { if (n == 0) print "警告: データ行が見つかりませんでした" > "/dev/stderr" }
  '
} > "$out"

echo "書き出しました: $out（UTF-8 BOM 付き）" >&2
