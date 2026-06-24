package parser

import "testing"

func TestCiStagesDefaultVariables(t *testing.T) {
	raw := `
stages: [build, test]
default:
  image: node:18
  tags: [docker, linux]
variables:
  CI_REGISTRY: registry.example.com
  TIMEOUT: 30
`
	p, err := ParseGitlabCi(raw)
	if err != nil {
		t.Fatal(err)
	}
	if len(p.Stages) != 2 || p.Stages[0] != "build" || p.Stages[1] != "test" {
		t.Errorf("stages %+v", p.Stages)
	}
	if p.Default == nil || p.Default.Image == nil || *p.Default.Image != "node:18" {
		t.Errorf("default image %+v", p.Default)
	}
	if len(p.Default.Tags) != 2 || p.Default.Tags[0] != "docker" {
		t.Errorf("default tags %+v", p.Default.Tags)
	}
	if p.Variables["CI_REGISTRY"] != "registry.example.com" {
		t.Errorf("variables %+v", p.Variables)
	}
	if p.Variables["TIMEOUT"] != "30" {
		t.Errorf("variables timeout %+v", p.Variables)
	}
}

func TestCiVariablesObjectFormExtractsValue(t *testing.T) {
	raw := `
variables:
  DEPLOY_TARGET:
    value: staging
    description: deploy target
`
	p, _ := ParseGitlabCi(raw)
	if p.Variables["DEPLOY_TARGET"] != "staging" {
		t.Errorf("got %+v", p.Variables)
	}
}

func TestCiIncludeSingleStringIsLocal(t *testing.T) {
	p, _ := ParseGitlabCi("include: ci/build.yml")
	if len(p.Includes) != 1 || p.Includes[0].Type != "local" || p.Includes[0].File != "ci/build.yml" {
		t.Errorf("got %+v", p.Includes)
	}
}

func TestCiIncludeAll5Types(t *testing.T) {
	raw := `
include:
  - local: ci/a.yml
  - project: group/templates
    ref: main
    file: ci/java.yml
  - project: group/templates
    files: [a.yml, b.yml]
  - remote: https://example.com/ci.yml
  - template: Jobs/SAST.gitlab-ci.yml
  - component: gitlab.com/comp@1.0
`
	p, err := ParseGitlabCi(raw)
	if err != nil {
		t.Fatal(err)
	}
	if len(p.Includes) != 6 {
		t.Fatalf("len %d", len(p.Includes))
	}
	types := []string{"local", "project", "project", "remote", "template", "component"}
	for i, expected := range types {
		if p.Includes[i].Type != expected {
			t.Errorf("[%d] %s, got %+v", i, expected, p.Includes[i])
		}
	}
	if p.Includes[1].Project != "group/templates" || p.Includes[1].Ref != "main" {
		t.Errorf("project[1] %+v", p.Includes[1])
	}
	if len(p.Includes[1].Files) != 1 || p.Includes[1].Files[0] != "ci/java.yml" {
		t.Errorf("project[1] files %+v", p.Includes[1].Files)
	}
	if len(p.Includes[2].Files) != 2 || p.Includes[2].Files[0] != "a.yml" || p.Includes[2].Files[1] != "b.yml" {
		t.Errorf("project[2] files %+v", p.Includes[2].Files)
	}
}

func TestCiJobsExcludedFromReservedKeys(t *testing.T) {
	raw := `
stages: [build]
build:
  stage: build
  image: node:18
  script:
    - npm ci
    - npm run build
  tags: [docker]
  extends: .base
  needs: [lint]
lint:
  script: eslint .
`
	p, _ := ParseGitlabCi(raw)
	names := map[string]bool{}
	for _, j := range p.Jobs {
		names[j.Name] = true
	}
	if !names["build"] || !names["lint"] {
		t.Errorf("missing jobs %+v", names)
	}
	if names["stages"] {
		t.Errorf("stages should not be a job")
	}
	for i := range p.Jobs {
		j := &p.Jobs[i]
		switch j.Name {
		case "build":
			if j.Stage == nil || *j.Stage != "build" {
				t.Errorf("build.stage %+v", j.Stage)
			}
			if j.Image == nil || *j.Image != "node:18" {
				t.Errorf("build.image %+v", j.Image)
			}
			if len(j.Tags) != 1 || j.Tags[0] != "docker" {
				t.Errorf("build.tags %+v", j.Tags)
			}
			if len(j.Extends) != 1 || j.Extends[0] != ".base" {
				t.Errorf("build.extends %+v", j.Extends)
			}
			if len(j.Needs) != 1 || j.Needs[0] != "lint" {
				t.Errorf("build.needs %+v", j.Needs)
			}
			if len(j.Script) != 2 || j.Script[0] != "npm ci" || j.Script[1] != "npm run build" {
				t.Errorf("build.script %+v", j.Script)
			}
		case "lint":
			if len(j.Script) != 1 || j.Script[0] != "eslint ." {
				t.Errorf("lint.script %+v", j.Script)
			}
		}
	}
}

func TestCiNeedsWithObjectForm(t *testing.T) {
	raw := `
build:
  needs:
    - lint
    - { job: test, artifacts: true }
`
	p, _ := ParseGitlabCi(raw)
	if len(p.Jobs[0].Needs) != 2 || p.Jobs[0].Needs[0] != "lint" || p.Jobs[0].Needs[1] != "test" {
		t.Errorf("got %+v", p.Jobs[0].Needs)
	}
}

func TestCiImageObjectForm(t *testing.T) {
	raw := `
build:
  image:
    name: node:18
    entrypoint: ["/bin/sh"]
`
	p, _ := ParseGitlabCi(raw)
	if p.Jobs[0].Image == nil || *p.Jobs[0].Image != "node:18" {
		t.Errorf("got %+v", p.Jobs[0].Image)
	}
}

func TestCiImagesFieldDedupsDefaultAndJobs(t *testing.T) {
	raw := `
default:
  image: node:18
build:
  image: node:18
test:
  image: alpine:3
`
	p, _ := ParseGitlabCi(raw)
	if len(p.Images) != 2 || p.Images[0] != "node:18" || p.Images[1] != "alpine:3" {
		t.Errorf("got %+v", p.Images)
	}
}

func TestCiEmptyYamlReturnsDefault(t *testing.T) {
	p, err := ParseGitlabCi("")
	if err != nil {
		t.Fatal(err)
	}
	if len(p.Stages) != 0 || len(p.Jobs) != 0 {
		t.Errorf("expected empty: %+v", p)
	}
}

func TestCiRejectsNonMappingRoot(t *testing.T) {
	if _, err := ParseGitlabCi("- a\n- b\n"); err == nil {
		t.Fatal("expected error")
	}
}

func TestCiRejectsMalformedYaml(t *testing.T) {
	if _, err := ParseGitlabCi("stages: [build, test\n"); err == nil {
		t.Fatal("expected error")
	}
}
