#!/usr/bin/env bash
# =====================================================================
# 直近 N 日のジョブについて「割り当てられた runner」を取得し、
# runner が取れるジョブ / 取れないジョブの違いを集計する
#
#   実行:   bash runner_jobs.sh                  （TOKEN を export 済みのシェルで）
#   試走:   DAYS_BACK=7 bash runner_jobs.sh
#
#   出力:   runner_jobs/rjobs_YYYY-MM-DD.tsv     14 日ごとの生データ
#           runner_jobs/all.tsv                  重複を除いた全ジョブ
#           runner_jobs/summary.txt              集計結果（画面にも表示）
#           runner_jobs/skipped.log              取得に失敗したもの
#
#   途中で止まっても、再実行すれば完成済みの期間は飛ばして続きから取る。
#   全期間取り終わっていれば、再実行は集計だけになる。
#   ※ jobs_*.tsv（ci_usage.py 用）とは別フォルダに出すので混ざらない
# =====================================================================
set -euo pipefail
trap 'echo "失敗: 行 $LINENO (exit $?)" >&2' ERR

HOST="https://gitlab.example.com"   # ← 実際の値に
GROUP_ID=123                        # ← 実際の値に
H="PRIVATE-TOKEN: $TOKEN"

DAYS_BACK=${DAYS_BACK:-90}          # 何日前から取るか
WINDOW=14                           # 1 ファイルあたりの日数
OUTDIR=runner_jobs
ALL="$OUTDIR/all.tsv"
SUMMARY="$OUTDIR/summary.txt"

api() {
  curl -sS --fail-with-body --retry 3 --retry-connrefused --retry-delay 5 --max-time 60 \
    -H "$H" "$1"
}

mkdir -p "$OUTDIR"

# ---------------------------------------------------------------------
# 1. プロジェクト一覧
# ---------------------------------------------------------------------
projects=(); page=1
while :; do
  ids=$(api "$HOST/api/v4/groups/$GROUP_ID/projects?include_subgroups=true&per_page=100&page=$page" \
    | jq -r '.[].id' | tr -d '\r')
  [ -z "$ids" ] && break
  projects+=($ids); page=$((page+1))
done
echo "プロジェクト: ${#projects[@]} 件"

# ---------------------------------------------------------------------
# 2. 期間ごとに パイプライン → ジョブ を取得
# ---------------------------------------------------------------------
HEADER="project	pipeline_id	pipeline_source	ref	job_id	name	stage	status	failure_reason	allow_failure	created_at	started_at	finished_at	duration	queued_duration	tags	runner_id	runner_description	runner_type	runner_is_shared	runner_status	manager_system_id	manager_version	manager_status	web_url"

JQ_JOB='
  .[] | [
    $pid, $plid, $src, $ref,
    .id, .name, .stage, .status, (.failure_reason // ""), (.allow_failure | tostring),
    (.created_at // ""), (.started_at // ""), (.finished_at // ""),
    (.duration // ""), (.queued_duration // ""),
    ((.tag_list // []) | join(",")),
    (.runner.id // ""), (.runner.description // ""), (.runner.runner_type // ""),
    (if .runner then (.runner.is_shared | tostring) else "" end),
    (.runner.status // ""),
    (.runner_manager.system_id // ""), (.runner_manager.version // ""), (.runner_manager.status // ""),
    (.web_url // "")
  ] | @tsv'

START=$(date -u -d "$DAYS_BACK days ago" +%Y-%m-%d)
END=$(date -u -d 'tomorrow' +%Y-%m-%d)
echo "期間: $START 〜 $END"

from=$START
while [[ "$from" < "$END" ]]; do
  to=$(date -u -d "$from + $WINDOW days" +%Y-%m-%d)
  out="$OUTDIR/rjobs_${from}.tsv"
  if [ -f "$out" ]; then echo "skip $out"; from=$to; continue; fi

  tmp="$out.part"
  echo "$HEADER" > "$tmp"
  echo "== $from 〜 $to =="

  i=0
  for pid in "${projects[@]}"; do
    i=$((i+1))
    page=1
    while :; do
      if ! resp=$(api "$HOST/api/v4/projects/$pid/pipelines?updated_after=${from}T00:00:00Z&updated_before=${to}T00:00:00Z&per_page=100&page=$page"); then
        echo -e "${from}\tpipelines\t${pid}\t${resp:0:200}" | tr -d '\r' >> "$OUTDIR/skipped.log"
        break
      fi
      pls=$(printf '%s' "$resp" | jq -r '.[] | [.id, (.source // ""), (.ref // "")] | @tsv' | tr -d '\r')
      [ -z "$pls" ] && break

      while IFS=$'\t' read -r plid src ref; do
        jp=1
        while :; do        # 1 パイプライン 100 ジョブ超もページを進めて取る
          if ! jresp=$(api "$HOST/api/v4/projects/$pid/pipelines/$plid/jobs?include_retried=true&per_page=100&page=$jp"); then
            echo -e "${from}\tjobs\t${pid}/${plid}\t${jresp:0:200}" | tr -d '\r' >> "$OUTDIR/skipped.log"
            break
          fi
          n=$(printf '%s' "$jresp" | jq 'length' | tr -d '\r')
          [ "$n" -eq 0 ] && break
          printf '%s' "$jresp" \
            | jq -r --arg pid "$pid" --arg plid "$plid" --arg src "$src" --arg ref "$ref" "$JQ_JOB" \
            | tr -d '\r' >> "$tmp"
          [ "$n" -lt 100 ] && break
          jp=$((jp+1))
        done
      done <<< "$pls"
      page=$((page+1))
    done
    [ $((i % 20)) -eq 0 ] && echo "  進捗 $i/${#projects[@]}"
  done

  mv "$tmp" "$out"
  echo "done $out（$(($(wc -l < "$out") - 1)) 件）"
  from=$to
done

# ---------------------------------------------------------------------
# 3. 重複除去（期間をまたいで同じジョブが出たら、runner が取れている行を優先）
# ---------------------------------------------------------------------
awk -F'\t' '
  FNR == 1 { if (!h) { print; h = 1 } next }
  { id = $5
    if (!(id in row) || ($17 != "" && rid[id] == "")) { row[id] = $0; rid[id] = $17 } }
  END { for (id in row) print row[id] }
' "$OUTDIR"/rjobs_*.tsv > "$ALL"

# ---------------------------------------------------------------------
# 4. 集計
#    列: 1 project  3 pipeline_source  6 name  8 status  9 failure_reason
#        11 created_at  12 started_at  16 tags  17 runner_id  19 runner_type
#        21 runner_status  22 manager_system_id
# ---------------------------------------------------------------------
breakdown() {   # $1=見出し  $2=キー（awk の式）  $3=対象の絞り込み（awk の条件・省略可）  $4=表示件数
  local title=$1 key=$2 cond=${3:-1} top=${4:-15}
  echo
  echo "## $title"
  printf '%-36s %8s %10s %10s %8s\n' "値" "件数" "runnerあり" "runnerなし" "なし率"
  awk -F'\t' -v OFS='\t' "
    NR > 1 && ($cond) {
      k = $key; if (k == \"\") k = \"(空)\"
      t[k]++; if (\$17 != \"\") w[k]++
    }
    END { for (k in t) print k, t[k], w[k] + 0, t[k] - w[k] }
  " "$ALL" \
  | sort -t$'\t' -k2,2nr | head -n "$top" \
  | awk -F'\t' '{ printf "%-36s %8d %10d %10d %7.1f%%\n", substr($1, 1, 36), $2, $3, $4, ($2 ? $4 * 100 / $2 : 0) }'
}

{
  echo "# runner が取れるジョブ / 取れないジョブの比較"
  echo "対象: $START 〜 $END（作成: $(date '+%Y-%m-%d %H:%M')）"
  awk -F'\t' 'NR > 1 { t++; if ($17 != "") w++ }
    END { printf "全ジョブ %d 件 / runner あり %d 件（%.1f%%）/ runner なし %d 件（%.1f%%）\n",
          t, w, (t ? w*100/t : 0), t-w, (t ? (t-w)*100/t : 0) }' "$ALL"

  echo
  echo "=== A. 全ジョブでの違い ==="
  breakdown "開始したか（started_at の有無）" '($12 == "" ? "未開始" : "開始済み")'
  breakdown "ジョブの状態（status）"          '$8'
  breakdown "作成月"                          'substr($11, 1, 7)'
  breakdown "パイプラインの起動元（source）"  '$3'
  breakdown "タグ"                            '$16'
  breakdown "プロジェクト"                    '$1'

  echo
  echo "=== B. 開始済みなのに runner が取れないジョブ（本命の調査対象）==="
  breakdown "状態"            '$8'                '$12 != "" && $17 == ""'
  breakdown "失敗理由"        '$9'                '$12 != "" && $17 == ""'
  breakdown "作成月"          'substr($11, 1, 7)' '$12 != "" && $17 == ""'
  breakdown "タグ"            '$16'               '$12 != "" && $17 == ""'
  breakdown "プロジェクト"    '$1'                '$12 != "" && $17 == ""'
  echo
  echo "例（画面で開いて確認する用・最大 5 件）:"
  awk -F'\t' 'NR > 1 && $12 != "" && $17 == "" { print "  " $25; if (++c >= 5) exit }' "$ALL"

  echo
  echo "=== C. runner が取れたジョブの runner の種類 ==="
  breakdown "runner の種類（runner_type）" '$19' '$17 != ""'
  breakdown "runner の現在の状態（status）" '$21' '$17 != ""'
  breakdown "runner manager が記録されているか" '($22 == "" ? "なし" : "あり")' '$17 != ""'
} | tee "$SUMMARY"

echo
echo "集計: $SUMMARY / 全データ: $ALL"
[ -s "$OUTDIR/skipped.log" ] && echo "取得できなかったもの: $OUTDIR/skipped.log（$(wc -l < "$OUTDIR/skipped.log") 件）"
exit 0
