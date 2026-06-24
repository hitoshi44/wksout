package parser

import (
	"encoding/xml"
	"fmt"
	"io"
	"slices"
	"strings"

	"repo-tracker/internal/model"
)

// ParsePomXml: encoding/xml の Decoder で path ベースの状態マシン。
//
// 抽出する要素:
//   project.groupId / artifactId / version
//   project.parent.{groupId,artifactId,version}
//   project.properties.<任意キー>
//   project.dependencies.dependency.{groupId,artifactId,version,scope}
//   project.build.plugins.plugin.{groupId,artifactId,version}
func ParsePomXml(raw string) (*model.ParsedPomXml, error) {
	dec := xml.NewDecoder(strings.NewReader(raw))
	out := model.NewParsedPomXml()
	var stack []string

	var curParent *model.PomCoordinate
	var curDep *model.PomDependency
	var curPlugin *model.PomPlugin

	for {
		tok, err := dec.Token()
		if err == io.EOF {
			break
		}
		if err != nil {
			return nil, fmt.Errorf("pom.xml: %w", err)
		}
		switch t := tok.(type) {
		case xml.StartElement:
			name := t.Name.Local
			stack = append(stack, name)
			switch {
			case slices.Equal(stack, []string{"project", "parent"}):
				curParent = &model.PomCoordinate{}
			case slices.Equal(stack, []string{"project", "dependencies", "dependency"}):
				curDep = &model.PomDependency{}
			case slices.Equal(stack, []string{"project", "build", "plugins", "plugin"}):
				curPlugin = &model.PomPlugin{}
			}
		case xml.EndElement:
			if len(stack) == 0 {
				continue
			}
			popped := stack[len(stack)-1]
			stack = stack[:len(stack)-1]
			switch {
			case popped == "parent" && slices.Equal(stack, []string{"project"}):
				if curParent != nil {
					out.Parent = curParent
					curParent = nil
				}
			case popped == "dependency" && slices.Equal(stack, []string{"project", "dependencies"}):
				if curDep != nil {
					out.Dependencies = append(out.Dependencies, *curDep)
					curDep = nil
				}
			case popped == "plugin" && slices.Equal(stack, []string{"project", "build", "plugins"}):
				if curPlugin != nil {
					out.Plugins = append(out.Plugins, *curPlugin)
					curPlugin = nil
				}
			}
		case xml.CharData:
			text := strings.TrimSpace(string(t))
			if text == "" {
				continue
			}
			applyPomText(stack, text, out, curParent, curDep, curPlugin)
		}
	}
	return out, nil
}

func applyPomText(
	stack []string,
	text string,
	out *model.ParsedPomXml,
	parent *model.PomCoordinate,
	dep *model.PomDependency,
	plugin *model.PomPlugin,
) {
	switch {
	case slices.Equal(stack, []string{"project", "groupId"}):
		s := text
		out.GroupId = &s
	case slices.Equal(stack, []string{"project", "artifactId"}):
		s := text
		out.ArtifactId = &s
	case slices.Equal(stack, []string{"project", "version"}):
		s := text
		out.Version = &s

	case len(stack) == 3 && stack[0] == "project" && stack[1] == "parent" && parent != nil:
		switch stack[2] {
		case "groupId":
			parent.GroupId = text
		case "artifactId":
			parent.ArtifactId = text
		case "version":
			parent.Version = text
		}

	case len(stack) == 3 && stack[0] == "project" && stack[1] == "properties":
		out.Properties[stack[2]] = text

	case len(stack) == 4 && stack[0] == "project" && stack[1] == "dependencies" && stack[2] == "dependency" && dep != nil:
		switch stack[3] {
		case "groupId":
			dep.GroupId = text
		case "artifactId":
			dep.ArtifactId = text
		case "version":
			s := text
			dep.Version = &s
		case "scope":
			s := text
			dep.Scope = &s
		}

	case len(stack) == 5 && stack[0] == "project" && stack[1] == "build" && stack[2] == "plugins" && stack[3] == "plugin" && plugin != nil:
		switch stack[4] {
		case "groupId":
			plugin.GroupId = text
		case "artifactId":
			plugin.ArtifactId = text
		case "version":
			s := text
			plugin.Version = &s
		}
	}
}
