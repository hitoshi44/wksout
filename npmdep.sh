#!/usr/bin/env bash
# 直接依存（本番のみ）の EOL 状況を npm だけで判定し、画面表示 + CSV（UTF-8 BOM 付き）に書き出す
#
#   DEPRECATED : npm で非推奨になっている（確実に EOL）
#   MAINTAINED : 非推奨でなく、最新版のリリースが MONTHS ヶ月以内（メンテ中）
#   UNKNOWN    : どちらとも言えない → 要目視確認
#   ERROR      : npm view に失敗（社内パッケージ・レジストリ設定など）
#
# 使い方: package.json のあるディレクトリで
#   ./check-eol-csv.sh                 # → eol.csv に書き出す
#   ./check-eol-csv.sh out.csv         # → 出力先を指定
#   MONTHS=24 ./check-eol-csv.sh       # メンテ中とみなす期間（既定 12ヶ月）
#   INCLUDE_DEV=1 ./check-eol-csv.sh   # devDependencies も含める
#   REGISTRY=https://registry.npmjs.org ./check-eol-csv.sh   # 問い合わせ先を指定

OUT="${1:-eol.csv}"
MONTHS="${MONTHS:-12}"

if [ ! -f package.json ]; then
  echo "エラー: package.json がありません。プロジェクトのディレクトリで実行してください。" >&2
  exit 1
fi
if [ ! -d node_modules ]; then
  echo "エラー: node_modules がありません。先に npm install（または npm ci）を実行してください。" >&2
  exit 1
fi

reg=""
[ -n "$REGISTRY" ] && reg="--registry=$REGISTRY"

omit="--omit=dev"
[ -n "$INCLUDE_DEV" ] && omit=""

# 基準日（MONTHS ヶ月前）を YYYY-MM-DD で求める（GNU date / macOS date 両対応）
threshold=$(date -u -d "-${MONTHS} months" +%Y-%m-%d 2>/dev/null \
         || date -u -v-"${MONTHS}"m +%Y-%m-%d)

# CSV の1フィールドをクォートする（" は "" にエスケープ）
csvq() { printf '"%s"' "$(printf '%s' "$1" | sed 's/"/""/g')"; }

# 1行を画面と CSV の両方に出す
emit() {
  printf "%-10s  %-40s  %-12s  %-12s  %-10s  %s\n" "$1" "$2" "$3" "$4" "$5" "$6"
  printf '%s,%s,%s,%s,%s,%s\r\n' "$(csvq "$1")" "$(csvq "$2")" "$(csvq "$3")" \
    "$(csvq "$4")" "$(csvq "$5")" "$(csvq "$6")" >> "$OUT"
}

# CSV を BOM + ヘッダで初期化
printf '\357\273\277' > "$OUT"
printf '"status","package","current","latest","released","note"\r\n' >> "$OUT"

printf "レジストリ: %s\n" "${REGISTRY:-$(npm config get registry 2>/dev/null)}"
printf "基準: 最新リリースが %s 以降なら MAINTAINED（%sヶ月以内）\n\n" "$threshold" "$MONTHS"
printf "%-10s  %-40s  %-12s  %-12s  %-10s  %s\n" STATUS PACKAGE CURRENT LATEST RELEASED NOTE
printf "%-10s  %-40s  %-12s  %-12s  %-10s  %s\n" ---------- ---------------------------------------- ------------ ------------ ---------- ----

# npm ls の通常のツリー表示を読む（1行目はプロジェクト自身なのでスキップ）
#   ├── react@18.3.1   /   +-- react@18.3.1   /   └── @scope/pkg@1.0.0 deduped
specs=$(npm ls --depth=0 $omit 2>/dev/null | tail -n +2 \
        | grep -v "UNMET" \
        | sed -E 's/^[^@[:alnum:]]+//' | awk '{print $1}')

count=0
for spec in $specs; do
  name=${spec%@*}
  cur=${spec##*@}
  { [ -z "$name" ] || [ -z "$cur" ] || [ "$name" = "$spec" ]; } && continue
  count=$((count + 1))

  out=$(npm view $reg "$name" dist-tags.latest 2>&1)
  latest=$(printf '%s\n' "$out" | grep -E '^[0-9]+\.[0-9]+\.[0-9]+' | tail -n 1)
  if [ -z "$latest" ]; then
    err=$(printf '%s\n' "$out" | grep -m1 -E 'npm (ERR!|error) (code|[0-9]{3})' || printf '%s\n' "$out" | head -n 1)
    emit ERROR "$name" "$cur" "-" "-" "npm view 失敗: ${err:-（出力なし）}"
    continue
  fi

  released=$(npm view $reg "$name" "time[$latest]" 2>/dev/null | cut -c1-10)
  dep_cur=$(npm view $reg "$name@$cur" deprecated 2>/dev/null)
  dep_latest=$(npm view $reg "$name@$latest" deprecated 2>/dev/null)

  note=""
  if [ -n "$dep_latest" ]; then
    status="DEPRECATED"; note="パッケージ自体が非推奨: $dep_latest"
  elif [ -n "$dep_cur" ]; then
    status="DEPRECATED"; note="使用中の版が非推奨（$latest へ更新で解消）: $dep_cur"
  elif [[ "$released" > "$threshold" || "$released" == "$threshold" ]]; then
    status="MAINTAINED"
    [ "${cur%%.*}" != "${latest%%.*}" ] && note="メジャー版が古い（旧版のサポート状況は要確認）"
  else
    status="UNKNOWN"; note="最新リリースから ${MONTHS}ヶ月以上経過 → README/Issue を要確認"
  fi

  emit "$status" "$name" "$cur" "$latest" "$released" "$note"
done

echo
if [ "$count" -eq 0 ]; then
  echo "警告: 対象のパッケージが見つかりませんでした（npm ls --depth=0 $omit の出力を確認してください）" >&2
fi
echo "CSV を書き出しました: $OUT（${count}件、UTF-8 BOM 付き）"
