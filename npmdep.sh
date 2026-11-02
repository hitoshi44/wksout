#!/usr/bin/env bash
# 直接依存（本番のみ）の EOL 状況を npm だけで判定する
#
#   DEPRECATED : npm で非推奨になっている（確実に EOL）
#   MAINTAINED : 非推奨でなく、最新版のリリースが MONTHS ヶ月以内（メンテ中）
#   UNKNOWN    : どちらとも言えない → 要目視確認
#
# 使い方: package.json のあるディレクトリで
#   ./check-eol.sh          # 12ヶ月基準
#   MONTHS=24 ./check-eol.sh
#   INCLUDE_DEV=1 ./check-eol.sh   # devDependencies も含める
#   REGISTRY=https://registry.npmjs.org ./check-eol.sh   # 問い合わせ先を指定

MONTHS="${MONTHS:-12}"

if [ ! -f package.json ]; then
  echo "エラー: package.json がありません。プロジェクトのディレクトリで実行してください。" >&2
  exit 1
fi
if [ ! -d node_modules ]; then
  echo "エラー: node_modules がありません。先に npm install（または npm ci）を実行してください。" >&2
  exit 1
fi

# 基準日（MONTHS ヶ月前）を YYYY-MM-DD で求める（GNU date / macOS date 両対応）
threshold=$(date -u -d "-${MONTHS} months" +%Y-%m-%d 2>/dev/null \
         || date -u -v-"${MONTHS}"m +%Y-%m-%d)

# 問い合わせ先レジストリ（.npmrc の社内レジストリを使わず公開 npm を見たい場合など）
#   REGISTRY=https://registry.npmjs.org ./check-eol.sh
reg=""
[ -n "$REGISTRY" ] && reg="--registry=$REGISTRY"

omit="--omit=dev"
[ -n "$INCLUDE_DEV" ] && omit=""

printf "レジストリ: %s\n" "${REGISTRY:-$(npm config get registry 2>/dev/null)}"
printf "基準: 最新リリースが %s 以降なら MAINTAINED（%sヶ月以内）\n\n" "$threshold" "$MONTHS"
printf "%-10s  %-40s  %-12s  %-12s  %-10s  %s\n" STATUS PACKAGE CURRENT LATEST RELEASED NOTE
printf "%-10s  %-40s  %-12s  %-12s  %-10s  %s\n" ---------- ---------------------------------------- ------------ ------------ ---------- ----

# npm ls の通常のツリー表示を読む（1行目はプロジェクト自身なのでスキップ）
#   ├── react@18.3.1   /   +-- react@18.3.1   /   └── @scope/pkg@1.0.0 deduped
npm ls --depth=0 $omit 2>/dev/null | tail -n +2 \
| grep -v "UNMET" \
| sed -E 's/^[^@[:alnum:]]+//' | awk '{print $1}' \
| while IFS= read -r spec; do
    name=${spec%@*}
    cur=${spec##*@}
    [ -z "$name" ] || [ -z "$cur" ] || [ "$name" = "$spec" ] && continue

    out=$(npm view $reg "$name" dist-tags.latest 2>&1)
    latest=$(printf '%s\n' "$out" | grep -E '^[0-9]+\.[0-9]+\.[0-9]+' | tail -n 1)
    if [ -z "$latest" ]; then
      err=$(printf '%s\n' "$out" | grep -m1 -E 'npm (ERR!|error) (code|[0-9]{3})' || printf '%s\n' "$out" | head -n 1)
      printf "%-10s  %-40s  %-12s  %-12s  %-10s  %s\n" ERROR "$name" "$cur" "-" "-" "npm view 失敗: ${err:-（出力なし）}"
      continue
    fi

    released=$(npm view $reg "$name" "time[$latest]" 2>/dev/null | cut -c1-10)
    dep_cur=$(npm view $reg "$name@$cur" deprecated 2>/dev/null)
    dep_latest=$(npm view $reg "$name@$latest" deprecated 2>/dev/null)

    status=""; note=""
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

    printf "%-10s  %-40s  %-12s  %-12s  %-10s  %s\n" "$status" "$name" "$cur" "$latest" "$released" "$note"
  done
