// Package parser: kind 別の raw → ParsedFile 変換。
package parser

import (
	"fmt"

	"repo-tracker/internal/model"
)

func Parse(kind string, raw string) (interface{}, error) {
	switch kind {
	case model.KindPackageJson:
		return ParsePackageJson(raw)
	case model.KindPomXml:
		return ParsePomXml(raw)
	case model.KindGitlabCi:
		return ParseGitlabCi(raw)
	case model.KindDockerfile:
		// Dockerfile は構造化しない (raw のみ保持)。empty parsed を返す。
		return model.ParsedUnstructured{}, nil
	}
	return nil, fmt.Errorf("unknown kind: %s", kind)
}
