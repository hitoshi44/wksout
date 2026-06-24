package templates

import (
	"reflect"
	"testing"

	"repo-tracker/internal/model"
)

func TestExtractVars(t *testing.T) {
	raw := `
build:
  stage: build
  variables:
    NODE_VERSION: "20"
  script:
    - docker login -u $CI_REGISTRY_USER -p $CI_REGISTRY_PASSWORD $CI_REGISTRY
    - npm publish --token ${NPM_TOKEN}
    - echo "$NODE_VERSION on $CI_PROJECT_PATH"
`
	got := extractVars(raw)
	// CI_* は除外。NODE_VERSION / NPM_TOKEN のみ。ソート済み。
	want := []string{"NODE_VERSION", "NPM_TOKEN"}
	if !reflect.DeepEqual(got, want) {
		t.Fatalf("extractVars = %v, want %v", got, want)
	}
}

func TestExtractVarsDedupAndEmpty(t *testing.T) {
	if got := extractVars(""); !reflect.DeepEqual(got, []string{}) {
		t.Fatalf("empty raw should give [], got %v", got)
	}
	if got := extractVars("$FOO $FOO ${FOO}"); !reflect.DeepEqual(got, []string{"FOO"}) {
		t.Fatalf("dedup failed: %v", got)
	}
	// 位置引数 ($1) は拾わない。
	if got := extractVars("echo $1 $2"); !reflect.DeepEqual(got, []string{}) {
		t.Fatalf("positional args should be ignored, got %v", got)
	}
}

func TestIsExcludedVar(t *testing.T) {
	cases := map[string]bool{
		"CI_REGISTRY": true,
		"CI_":         true,
		"NPM_TOKEN":   false,
		"GITLAB_USER": false, // 今は CI_* のみ除外
		"MY_CI_VAR":   false, // 接頭辞一致のみ
	}
	for name, want := range cases {
		if got := isExcludedVar(name); got != want {
			t.Errorf("isExcludedVar(%q) = %v, want %v", name, got, want)
		}
	}
}

func TestTemplateStages(t *testing.T) {
	build, test := "build", "test"

	// stages: 宣言があればそれを優先。
	declared := model.NewParsedGitlabCi()
	declared.Stages = []string{"a", "b"}
	if got := templateStages(declared); !reflect.DeepEqual(got, []string{"a", "b"}) {
		t.Fatalf("declared stages = %v, want [a b]", got)
	}

	// 宣言が無ければ job の stage を出現順で重複除去。
	p := model.NewParsedGitlabCi()
	p.Jobs = []model.CiJob{
		{Name: "j1", Stage: &build},
		{Name: "j2", Stage: &test},
		{Name: "j3", Stage: &build},
		{Name: "j4"}, // stage 無しは無視
	}
	if got := templateStages(p); !reflect.DeepEqual(got, []string{"build", "test"}) {
		t.Fatalf("derived stages = %v, want [build test]", got)
	}
}
