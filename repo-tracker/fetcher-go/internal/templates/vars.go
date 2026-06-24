// Package templates: ci_templates 設定で指定したリポから README とテンプレ yml を
// 集める、既存 fetcher (repos.csv 系) とは独立した収集系統。
package templates

import (
	"regexp"
	"sort"
	"strings"
)

// $VAR / ${VAR} の参照を拾う。名前は letter/underscore 始まり ($1 等の位置引数は拾わない)。
var varRefRe = regexp.MustCompile(`\$\{?([A-Za-z_][A-Za-z0-9_]*)\}?`)

// isExcludedVar は「要設定」カタログから隠す変数かを返す。
// 現状は GitLab が自動提供する CI_* のみ。除外条件をここ 1 箇所に切り出してあるので、
// 後で GITLAB_* など他の predefined を足すときは extractVars を触らず済む。
func isExcludedVar(name string) bool {
	return strings.HasPrefix(name, "CI_")
}

// extractVars は raw テキストから $VAR / ${VAR} 参照を抽出し、除外名を落として
// 重複除去・ソートして返す。定義 (variables:) と参照は区別しない —
// 機械抽出では「使われている」ことしか分からないため。空でも非 nil を返す。
func extractVars(raw string) []string {
	seen := map[string]bool{}
	out := []string{}
	for _, m := range varRefRe.FindAllStringSubmatch(raw, -1) {
		name := m[1]
		if isExcludedVar(name) || seen[name] {
			continue
		}
		seen[name] = true
		out = append(out, name)
	}
	sort.Strings(out)
	return out
}
