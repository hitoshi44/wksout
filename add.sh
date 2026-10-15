#!/usr/bin/env bash
# jobs_*.tsv に出てくる runner ID の所属グループを API で取得して runners.tsv に保存する。
# 取得済みの ID は飛ばすので、ジョブを追加取得したあとに再実行すれば差分だけ取りに行く。
#
# 使い方（jobs_*.tsv があるフォルダで）:
#   export TOKEN=glpat-xxxx
#   bash make_runners.sh
#
# runners.tsv の列: runner_id  種類  所属グループ  名前(description)  タグ  作成日時
#   種類     : group_type / project_type / instance_type（取得失敗は error）
#   所属グループ: group runner はグループのパス（例: mygroup/team1）
#                project runner は割り当てプロジェクトのパス（カンマ区切り）
# 取得失敗（error）の行は、消してから再実行すると取り直す。
set -euo pipefail
trap 'echo "失敗: 行 $LINENO (exit $?)" >&2' ERR

HOST="https://gitlab.example.com"   # ← 実際の値に
H="PRIVATE-TOKEN: $TOKEN"
OUT=runners.tsv

api() {
  curl -sS --fail-with-body --retry 3 --retry-connrefused --retry-delay 5 --max-time 60 \
    -H "$H" "$1"
}

touch "$OUT"

# jobs_*.tsv の 12 列目（runner_id）から、数字の ID だけを重複なしで取り出す
all_ids=$(awk -F'\t' '$1!="project" && $12 ~ /^[0-9]+$/ {print $12}' jobs_*.tsv | tr -d '\r' | sort -u)
done_ids=$(cut -f1 "$OUT" | tr -d '\r' | sort -u)
todo=$(comm -23 <(printf '%s\n' "$all_ids") <(printf '%s\n' "$done_ids") | grep . || true)

n_all=$(printf '%s\n' "$all_ids" | grep -c . || true)
n_todo=$(printf '%s\n' "$todo" | grep -c . || true)
echo "runner ID: 全 $n_all 件 / 未取得 $n_todo 件"
[ "$n_todo" -eq 0 ] && { echo "取得するものはありません"; exit 0; }

i=0; ng=0
for id in $todo; do
  i=$((i+1))
  if resp=$(api "$HOST/api/v4/runners/$id" 2>&1); then
    printf '%s' "$resp" | jq -r --arg id "$id" '
      [ $id,
        (.runner_type // ""),
        ( if .runner_type == "group_type" then
            ((.groups // []) | map(.web_url | sub("^https?://[^/]+/(groups/)?"; "")) | join(","))
          elif .runner_type == "project_type" then
            ((.projects // []) | map(.path_with_namespace) | join(","))
          else "" end ),
        ((.description // "") | gsub("[\t\r\n]"; " ")),
        ((.tag_list // []) | join(",")),
        (.created_at // "")
      ] | @tsv' | tr -d '\r' >> "$OUT"
  else
    printf '%s\terror\t\t\t\t\n' "$id" >> "$OUT"
    ng=$((ng+1))
    # 最初の数件だけエラー内容を表示
    if [ "$ng" -le 3 ]; then echo "  runner $id: 取得失敗 → ${resp:0:200}"; fi
  fi
  if [ $((i % 50)) -eq 0 ]; then echo "  $i / $n_todo"; fi
done

echo "完了: $i 件（失敗 $ng 件）→ $OUT"
echo "== 所属グループ別の runner 数 =="
awk -F'\t' '{print ($2=="error" ? "(取得失敗)" : ($3=="" ? "(" $2 ")" : $3))}' "$OUT" | sort | uniq -c | sort -rn
