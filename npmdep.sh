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

MONTHS="${MONTHS:-12}"

# 基準日（MONTHS ヶ月前）を YYYY-MM-DD で求める（GNU date / macOS date 両対応）
threshold=$(date -u -d "-${MONTHS} months" +%Y-%m-%d 2>/dev/null \
         || date -u -v-"${MONTHS}"m +%Y-%m-%d)

omit="--omit=dev"
[ -n "$INCLUDE_DEV" ] && omit=""

printf "基準: 最新リリースが %s 以降なら MAINTAINED（%sヶ月以内）\n\n" "$threshold" "$MONTHS"
printf "%-10s  %-40s  %-12s  %-12s  %-10s  %s\n" STATUS PACKAGE CURRENT LATEST RELEASED NOTE
printf "%-10s  %-40s  %-12s  %-12s  %-10s  %s\n" ---------- ---------------------------------------- ------------ ------------ ---------- ----

# 1行目はプロジェクト自身なのでスキップ
# 形式: /path/node_modules/<name>:<name>@<version>[:...]
npm ls --depth=0 $omit --parseable --long 2>/dev/null | tail -n +2 \
| while IFS= read -r line; do
    spec=$(printf '%s' "$line" | cut -d: -f2)
    name=${spec%@*}
    cur=${spec##*@}
    [ -z "$name" ] || [ -z "$cur" ] && continue

    latest=$(npm view "$name" dist-tags.latest 2>/dev/null)
    if [ -z "$latest" ]; then
      printf "%-10s  %-40s  %-12s  %-12s  %-10s  %s\n" UNKNOWN "$name" "$cur" "-" "-" "レジストリから取得できない（private?）"
      continue
    fi

    released=$(npm view "$name" "time[$latest]" 2>/dev/null | cut -c1-10)
    dep_cur=$(npm view "$name@$cur" deprecated 2>/dev/null)
    dep_latest=$(npm view "$name@$latest" deprecated 2>/dev/null)

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
