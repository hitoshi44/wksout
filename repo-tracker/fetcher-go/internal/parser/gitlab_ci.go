package parser

import (
	"errors"
	"fmt"

	"gopkg.in/yaml.v3"

	"repo-tracker/internal/model"
)

// 予約キー (job 名として扱わないキー一覧)。
var ciReservedKeys = map[string]bool{
	"default":       true,
	"include":       true,
	"stages":        true,
	"variables":     true,
	"workflow":      true,
	"image":         true,
	"services":      true,
	"cache":         true,
	"before_script": true,
	"after_script":  true,
	"pages":         true,
	"spec":          true,
}

func ParseGitlabCi(raw string) (*model.ParsedGitlabCi, error) {
	var doc yaml.Node
	if err := yaml.Unmarshal([]byte(raw), &doc); err != nil {
		return nil, fmt.Errorf(".gitlab-ci.yml: %w", err)
	}
	out := model.NewParsedGitlabCi()
	if doc.Kind == 0 || len(doc.Content) == 0 {
		// 空ファイル
		return out, nil
	}
	root := doc.Content[0]
	if root.Kind != yaml.MappingNode {
		return nil, errors.New(".gitlab-ci.yml: root is not a mapping")
	}

	if n := mapGet(root, "stages"); n != nil {
		out.Stages = asStringList(n)
	}
	if n := mapGet(root, "default"); n != nil {
		out.Default = parseCiDefault(n)
	}
	if n := mapGet(root, "variables"); n != nil {
		out.Variables = asStringMap(n)
	}
	if n := mapGet(root, "include"); n != nil {
		out.Includes = parseCiIncludes(n)
	}

	// jobs: 予約キー以外のトップレベル mapping エントリ
	for i := 0; i+1 < len(root.Content); i += 2 {
		k := root.Content[i]
		v := root.Content[i+1]
		if k.Kind != yaml.ScalarNode {
			continue
		}
		name := k.Value
		if ciReservedKeys[name] {
			continue
		}
		if job := parseCiJob(name, v); job != nil {
			out.Jobs = append(out.Jobs, *job)
		}
	}

	// images: default + 全 job の image を declaration 順で重複除去
	seen := map[string]bool{}
	if out.Default != nil && out.Default.Image != nil {
		img := *out.Default.Image
		if !seen[img] {
			seen[img] = true
			out.Images = append(out.Images, img)
		}
	}
	for _, j := range out.Jobs {
		if j.Image != nil && !seen[*j.Image] {
			seen[*j.Image] = true
			out.Images = append(out.Images, *j.Image)
		}
	}

	return out, nil
}

// ---------- helpers ----------

// MappingNode から key 文字列で値ノードを引く。
func mapGet(m *yaml.Node, key string) *yaml.Node {
	if m == nil || m.Kind != yaml.MappingNode {
		return nil
	}
	for i := 0; i+1 < len(m.Content); i += 2 {
		k := m.Content[i]
		if k.Kind == yaml.ScalarNode && k.Value == key {
			return m.Content[i+1]
		}
	}
	return nil
}

func asString(n *yaml.Node) (string, bool) {
	if n == nil || n.Kind != yaml.ScalarNode {
		return "", false
	}
	return n.Value, true
}

func asStringList(n *yaml.Node) []string {
	if n == nil {
		return nil
	}
	switch n.Kind {
	case yaml.ScalarNode:
		return []string{n.Value}
	case yaml.SequenceNode:
		out := make([]string, 0, len(n.Content))
		for _, c := range n.Content {
			if s, ok := asString(c); ok {
				out = append(out, s)
			}
		}
		return out
	}
	return nil
}

// variables: 値が string/bool/number、または {value: ..., description: ...} 形式
func asStringMap(n *yaml.Node) map[string]string {
	out := map[string]string{}
	if n == nil || n.Kind != yaml.MappingNode {
		return out
	}
	for i := 0; i+1 < len(n.Content); i += 2 {
		k := n.Content[i]
		v := n.Content[i+1]
		if k.Kind != yaml.ScalarNode {
			continue
		}
		if s, ok := valueToSimpleString(v); ok {
			out[k.Value] = s
		}
	}
	return out
}

func valueToSimpleString(n *yaml.Node) (string, bool) {
	if n == nil {
		return "", false
	}
	switch n.Kind {
	case yaml.ScalarNode:
		// yaml.v3 は scalar の型情報を Tag に持つが、ここでは値文字列をそのまま返す
		// (bool / number / string 全部 n.Value で取れる)
		// ただし、yaml 仕様で数値は表現が変わらないので strconv 経由は不要
		return n.Value, true
	case yaml.MappingNode:
		// `{value: ..., description: ...}` 形式
		if v, ok := asString(mapGet(n, "value")); ok {
			return v, true
		}
	}
	return "", false
}

// ---------- default ----------

func parseCiDefault(n *yaml.Node) *model.CiDefault {
	if n == nil || n.Kind != yaml.MappingNode {
		return nil
	}
	d := &model.CiDefault{Tags: []string{}}
	if img := mapGet(n, "image"); img != nil {
		d.Image = extractCiImage(img)
	}
	if tags := mapGet(n, "tags"); tags != nil {
		d.Tags = asStringList(tags)
	}
	return d
}

func extractCiImage(n *yaml.Node) *string {
	if n == nil {
		return nil
	}
	switch n.Kind {
	case yaml.ScalarNode:
		s := n.Value
		return &s
	case yaml.MappingNode:
		if v, ok := asString(mapGet(n, "name")); ok {
			return &v
		}
	}
	return nil
}

// ---------- include ----------

func parseCiIncludes(n *yaml.Node) []model.CiInclude {
	if n == nil {
		return nil
	}
	switch n.Kind {
	case yaml.ScalarNode:
		return []model.CiInclude{{Type: "local", File: n.Value}}
	case yaml.SequenceNode:
		var out []model.CiInclude
		for _, c := range n.Content {
			if inc := parseSingleInclude(c); inc != nil {
				out = append(out, *inc)
			}
		}
		return out
	case yaml.MappingNode:
		if inc := parseSingleInclude(n); inc != nil {
			return []model.CiInclude{*inc}
		}
	}
	return nil
}

func parseSingleInclude(n *yaml.Node) *model.CiInclude {
	if n == nil {
		return nil
	}
	switch n.Kind {
	case yaml.ScalarNode:
		return &model.CiInclude{Type: "local", File: n.Value}
	case yaml.MappingNode:
		if s, ok := asString(mapGet(n, "local")); ok {
			return &model.CiInclude{Type: "local", File: s}
		}
		if s, ok := asString(mapGet(n, "remote")); ok {
			return &model.CiInclude{Type: "remote", URL: s}
		}
		if s, ok := asString(mapGet(n, "template")); ok {
			return &model.CiInclude{Type: "template", Name: s}
		}
		if s, ok := asString(mapGet(n, "component")); ok {
			return &model.CiInclude{Type: "component", Component: s}
		}
		if s, ok := asString(mapGet(n, "project")); ok {
			inc := &model.CiInclude{Type: "project", Project: s}
			if r, ok := asString(mapGet(n, "ref")); ok {
				inc.Ref = r
			}
			if f := mapGet(n, "file"); f != nil {
				inc.Files = asStringList(f)
			} else if fs := mapGet(n, "files"); fs != nil {
				inc.Files = asStringList(fs)
			}
			return inc
		}
	}
	return nil
}

// ---------- job ----------

func parseCiJob(name string, n *yaml.Node) *model.CiJob {
	if n == nil || n.Kind != yaml.MappingNode {
		return nil
	}
	j := &model.CiJob{
		Name:    name,
		Tags:    []string{},
		Extends: []string{},
		Needs:   []string{},
		Script:  []string{},
	}
	if s, ok := asString(mapGet(n, "stage")); ok {
		j.Stage = &s
	}
	if img := mapGet(n, "image"); img != nil {
		j.Image = extractCiImage(img)
	}
	if tags := mapGet(n, "tags"); tags != nil {
		j.Tags = asStringList(tags)
	}
	if ext := mapGet(n, "extends"); ext != nil {
		j.Extends = asStringList(ext)
	}
	if needs := mapGet(n, "needs"); needs != nil {
		j.Needs = parseCiNeeds(needs)
	}
	if script := mapGet(n, "script"); script != nil {
		j.Script = flattenCiScript(script)
	}
	return j
}

func parseCiNeeds(n *yaml.Node) []string {
	if n == nil || n.Kind != yaml.SequenceNode {
		return nil
	}
	var out []string
	for _, c := range n.Content {
		switch c.Kind {
		case yaml.ScalarNode:
			out = append(out, c.Value)
		case yaml.MappingNode:
			if s, ok := asString(mapGet(c, "job")); ok {
				out = append(out, s)
			}
		}
	}
	return out
}

// script: string | [string] | [string | [string]] (ネスト可)
func flattenCiScript(n *yaml.Node) []string {
	var out []string
	var push func(*yaml.Node)
	push = func(x *yaml.Node) {
		if x == nil {
			return
		}
		switch x.Kind {
		case yaml.ScalarNode:
			out = append(out, x.Value)
		case yaml.SequenceNode:
			for _, c := range x.Content {
				push(c)
			}
		}
	}
	push(n)
	return out
}
