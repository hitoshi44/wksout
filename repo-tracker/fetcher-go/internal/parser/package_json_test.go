package parser

import "testing"

func TestPackageJsonTypical(t *testing.T) {
	raw := `{
		"name": "my-app",
		"version": "1.2.3",
		"dependencies": { "lodash": "^4.17.21", "react": "^18.0.0" },
		"devDependencies": { "jest": "^29.0.0" },
		"scripts": { "build": "tsc", "test": "jest" },
		"engines": { "node": ">=18" }
	}`
	p, err := ParsePackageJson(raw)
	if err != nil {
		t.Fatal(err)
	}
	if p.Name == nil || *p.Name != "my-app" {
		t.Errorf("name: %+v", p.Name)
	}
	if p.Version == nil || *p.Version != "1.2.3" {
		t.Errorf("version: %+v", p.Version)
	}
	if p.Dependencies["lodash"] != "^4.17.21" {
		t.Errorf("dependencies: %+v", p.Dependencies)
	}
	if p.DevDependencies["jest"] != "^29.0.0" {
		t.Errorf("dev: %+v", p.DevDependencies)
	}
	if p.Scripts["build"] != "tsc" {
		t.Errorf("scripts: %+v", p.Scripts)
	}
	if p.Engines["node"] != ">=18" {
		t.Errorf("engines: %+v", p.Engines)
	}
}

func TestPackageJsonMissingFieldsDefaultToEmpty(t *testing.T) {
	p, err := ParsePackageJson(`{"name": "x"}`)
	if err != nil {
		t.Fatal(err)
	}
	if p.Name == nil || *p.Name != "x" {
		t.Errorf("name: %+v", p.Name)
	}
	if p.Version != nil {
		t.Errorf("version should be nil")
	}
	if len(p.Dependencies) != 0 || len(p.Scripts) != 0 {
		t.Errorf("expected empty maps")
	}
}

func TestPackageJsonNonStringDepValuesAreDropped(t *testing.T) {
	raw := `{
		"dependencies": {
			"lodash": "^4.0.0",
			"weird": { "version": "^1.0.0", "registry": "x" }
		}
	}`
	p, err := ParsePackageJson(raw)
	if err != nil {
		t.Fatal(err)
	}
	if p.Dependencies["lodash"] != "^4.0.0" {
		t.Errorf("lodash missing")
	}
	if _, ok := p.Dependencies["weird"]; ok {
		t.Errorf("weird should be dropped")
	}
}

func TestPackageJsonRejectsNonObjectRoot(t *testing.T) {
	if _, err := ParsePackageJson(`[]`); err == nil {
		t.Fatal("expected error for array root")
	}
	if _, err := ParsePackageJson(`"foo"`); err == nil {
		t.Fatal("expected error for string root")
	}
}

func TestPackageJsonRejectsMalformed(t *testing.T) {
	if _, err := ParsePackageJson(`{ not json`); err == nil {
		t.Fatal("expected error")
	}
}
