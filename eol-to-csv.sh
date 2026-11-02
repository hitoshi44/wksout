#!/usr/bin/env bash
# check-eol.sh の出力を CSV に変換する
#
# 使い方:
#   ./check-eol.sh | ./eol-to-csv.sh > eol.csv     # パイプで渡す
#   ./eol-to-csv.sh result.txt > eol.csv           # 保存済みの出力ファイルを渡す
#   NO_BOM=1 ./eol-to-csv.sh result.txt > eol.csv  # BOM なし（Excel 以外で使う場合）
#
# 既定では Excel で文字化けしないよう UTF-8 BOM を付ける

[ -z "$NO_BOM" ] && printf '\357\273\277'

echo 'status,package,current,latest,released,note'

# データ行（先頭が STATUS 値の行）だけを拾う
# 最初の5列は空白を含まないので空白で区切り、残りすべてを note とする
cat "${@:--}" | awk '
  function q(s) { gsub(/"/, "\"\"", s); return "\"" s "\"" }
  $1 ~ /^(DEPRECATED|MAINTAINED|UNKNOWN|ERROR)$/ {
    note = $0
    for (i = 1; i <= 5; i++) sub(/^[ \t]*[^ \t]+/, "", note)
    sub(/^[ \t]+/, "", note); sub(/[ \t\r]+$/, "", note)
    print q($1) "," q($2) "," q($3) "," q($4) "," q($5) "," q(note)
  }
'
