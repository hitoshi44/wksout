package parser

import (
	"encoding/json"
	"errors"
	"fmt"

	"repo-tracker/internal/model"
)

// ParsePackageJson: 値の型ゆれに寛容。dependencies / scripts / engines の値は
// 文字列だけ拾って、非文字列は黙って捨てる。root が object でない場合のみエラー。
func ParsePackageJson(raw string) (*model.ParsedPackageJson, error) {
	var root any
	if err := json.Unmarshal([]byte(raw), &root); err != nil {
		return nil, fmt.Errorf("package.json: %w", err)
	}
	obj, ok := root.(map[string]any)
	if !ok {
		return nil, errors.New("package.json: root is not an object")
	}

	out := model.NewParsedPackageJson()
	if s, ok := obj["name"].(string); ok {
		out.Name = &s
	}
	if s, ok := obj["version"].(string); ok {
		out.Version = &s
	}
	out.Dependencies = stringMap(obj["dependencies"])
	out.DevDependencies = stringMap(obj["devDependencies"])
	out.PeerDependencies = stringMap(obj["peerDependencies"])
	out.Scripts = stringMap(obj["scripts"])
	out.Engines = stringMap(obj["engines"])
	return out, nil
}

func stringMap(v any) map[string]string {
	out := map[string]string{}
	m, ok := v.(map[string]any)
	if !ok {
		return out
	}
	for k, vv := range m {
		if s, ok := vv.(string); ok {
			out[k] = s
		}
	}
	return out
}
