package templates

import (
	"fmt"
	"os"
	"sort"

	"repo-tracker/internal/config"
	"repo-tracker/internal/gitlab"
	"repo-tracker/internal/model"
	"repo-tracker/internal/parser"
)

// Collect は ci_templates の各エントリから README とテンプレ yml を集めて
// CITemplateCatalog を返す。repo 単位・ファイル単位の失敗は warning で吸収し、
// 取れた分だけ返す (1 リポの失敗で全体を落とさない)。
func Collect(client *gitlab.Client, baseURL string, entries []config.CITemplateRepo, fetchedAt string) *model.CITemplateCatalog {
	cat := &model.CITemplateCatalog{FetchedAt: fetchedAt, Repos: []model.CITemplateRepoData{}}
	for _, e := range entries {
		rd, err := collectOne(client, baseURL, e)
		if err != nil {
			fmt.Fprintf(os.Stderr, "warning: ci_templates skip %s: %v\n", e.Repo, err)
			continue
		}
		cat.Repos = append(cat.Repos, *rd)
	}
	return cat
}

func collectOne(client *gitlab.Client, baseURL string, e config.CITemplateRepo) (*model.CITemplateRepoData, error) {
	meta, err := client.FetchProjectMeta(baseURL, e.Repo)
	if err != nil {
		return nil, fmt.Errorf("meta: %w", err)
	}
	ref := e.Ref
	if ref == "" {
		ref = meta.DefaultBranch
	}
	rd := &model.CITemplateRepoData{
		Repo:      meta.PathWithNamespace,
		Name:      meta.Name,
		URL:       meta.WebURL,
		Ref:       ref,
		Templates: []model.CITemplateFile{},
	}

	// README は任意。取れなくても致命的でない (warning のみ)。
	if e.Readme != "" {
		if raw, err := client.FetchFile(baseURL, e.Repo, ref, e.Readme); err != nil {
			fmt.Fprintf(os.Stderr, "warning: ci_templates %s: readme %s: %v\n", e.Repo, e.Readme, err)
		} else {
			rd.Readme = raw.Raw
			rd.ReadmePath = e.Readme
		}
	}

	// templates_dir 配下の *.yml / *.yaml を 1 ファイル = 1 テンプレ単位で収集。
	paths, err := client.ListYAMLFiles(baseURL, e.Repo, e.TemplatesDir)
	if err != nil {
		return nil, fmt.Errorf("list %s: %w", e.TemplatesDir, err)
	}
	sort.Strings(paths)
	for _, p := range paths {
		raw, err := client.FetchFile(baseURL, e.Repo, ref, p)
		if err != nil {
			fmt.Fprintf(os.Stderr, "warning: ci_templates %s: file %s: %v\n", e.Repo, p, err)
			continue
		}
		rd.Templates = append(rd.Templates, buildTemplateFile(p, raw.Raw))
	}
	return rd, nil
}

// buildTemplateFile は 1 yml をパースして stage / jobs / vars をまとめる。
// パース失敗時も raw からの vars 抽出だけは成立させる。
func buildTemplateFile(path, raw string) model.CITemplateFile {
	tf := model.CITemplateFile{
		Path:   path,
		Name:   baseName(path),
		Stages: []string{},
		Jobs:   []string{},
		Vars:   extractVars(raw),
	}
	parsed, err := parser.ParseGitlabCi(raw)
	if err != nil {
		return tf
	}
	tf.Stages = templateStages(parsed)
	for _, j := range parsed.Jobs {
		tf.Jobs = append(tf.Jobs, j.Name)
	}
	return tf
}

// templateStages: `stages:` 宣言があればそれを、無ければ各 job の stage を
// 出現順で重複除去して返す (テンプレ片は stages: を持たないことが多い)。
func templateStages(p *model.ParsedGitlabCi) []string {
	if len(p.Stages) > 0 {
		return p.Stages
	}
	seen := map[string]bool{}
	out := []string{}
	for _, j := range p.Jobs {
		if j.Stage == nil || seen[*j.Stage] {
			continue
		}
		seen[*j.Stage] = true
		out = append(out, *j.Stage)
	}
	return out
}

func baseName(p string) string {
	for i := len(p) - 1; i >= 0; i-- {
		if p[i] == '/' {
			return p[i+1:]
		}
	}
	return p
}
