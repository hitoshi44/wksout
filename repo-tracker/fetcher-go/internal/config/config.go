// Package config: config.yml + repos.csv 読み込み。
package config

import (
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"strconv"
	"strings"

	"gopkg.in/yaml.v3"
)

type AppConfig struct {
	GitLab      GitLabCfg        `yaml:"gitlab"`
	Defaults    Defaults         `yaml:"defaults"`
	ReposFile   string           `yaml:"repos_file"`
	OutputDir   string           `yaml:"output_dir"`
	CITemplates []CITemplateRepo `yaml:"ci_templates"`
}

// CITemplateRepo: ci_templates の 1 エントリ。
// 既存 repos.csv ベースの収集とは独立した系統で、ここで指定したリポの README と
// テンプレ yml ディレクトリだけを集める (internal/templates が処理)。省略可。
type CITemplateRepo struct {
	Repo         string `yaml:"repo"`          // project path ("group/repo") か numeric id。API の :id に使う。
	Readme       string `yaml:"readme"`        // README のパス (任意。空ならスキップ)。
	TemplatesDir string `yaml:"templates_dir"` // この配下の *.yml / *.yaml を収集。
	Ref          string `yaml:"ref"`           // 任意。空なら default branch。
}

type GitLabCfg struct {
	URL      string `yaml:"url"`
	TokenEnv string `yaml:"token_env"`
}

type Defaults struct {
	Nest uint32 `yaml:"nest"`
}

// RepoEntry: repos.csv 1 行を表す。
// API 呼び出しは ID を使う (`/api/v4/projects/:id`)。
// URL は記録用 (将来エラー時のヒントなど、fetcher 内では使わない)。
type RepoEntry struct {
	ID   string
	URL  string
	Nest uint32
}

// デフォルト値。yaml が省略しているフィールドに適用する。
const (
	defaultNest      = uint32(1)
	defaultOutputDir = "site/data"
)

func LoadConfig(path string) (*AppConfig, error) {
	data, err := os.ReadFile(path)
	if err != nil {
		return nil, fmt.Errorf("read %s: %w", path, err)
	}
	var cfg AppConfig
	if err := yaml.Unmarshal(data, &cfg); err != nil {
		return nil, fmt.Errorf("parse %s: %w", path, err)
	}
	if cfg.Defaults.Nest == 0 {
		cfg.Defaults.Nest = defaultNest
	}
	if cfg.OutputDir == "" {
		cfg.OutputDir = defaultOutputDir
	}
	if cfg.ReposFile == "" {
		return nil, fmt.Errorf("%s: repos_file is required", path)
	}
	if cfg.GitLab.URL == "" {
		return nil, fmt.Errorf("%s: gitlab.url is required", path)
	}
	return &cfg, nil
}

// ResolveRelative: 相対パスを configPath のディレクトリ基準で解決する。
func ResolveRelative(configPath, value string) string {
	if filepath.IsAbs(value) {
		return value
	}
	base := filepath.Dir(configPath)
	if base == "" {
		base = "."
	}
	return filepath.Join(base, value)
}

// LoadRepos: ファイルを読んで ParseReposLines に渡すだけ。
func LoadRepos(filePath string, defaultNest uint32) ([]RepoEntry, error) {
	data, err := os.ReadFile(filePath)
	if err != nil {
		return nil, fmt.Errorf("read %s: %w", filePath, err)
	}
	entries, err := ParseReposLines(string(data), defaultNest)
	if err != nil {
		return nil, fmt.Errorf("%s: %w", filePath, err)
	}
	return entries, nil
}

// ParseReposLines: `id,url[,nest]` 行リストを解釈 (純関数)。
// # 始まりの行と空行はスキップ。列数 2 か 3 以外、または nest がパースできない場合エラー。
// id を使って GitLab API を叩くので url の中身は検証しない (空文字だけは弾く)。
func ParseReposLines(text string, defaultNest uint32) ([]RepoEntry, error) {
	var out []RepoEntry
	lines := strings.Split(text, "\n")
	for i, line := range lines {
		s := strings.TrimSpace(line)
		if s == "" || strings.HasPrefix(s, "#") {
			continue
		}
		parts := strings.Split(s, ",")
		if len(parts) != 2 && len(parts) != 3 {
			return nil, fmt.Errorf(
				"line %d: expected 'id,url[,nest]' (2 or 3 fields), got %d fields",
				i+1, len(parts))
		}
		for j := range parts {
			parts[j] = strings.TrimSpace(parts[j])
		}
		id, url := parts[0], parts[1]
		nest := defaultNest
		if len(parts) == 3 {
			n, err := strconv.ParseUint(parts[2], 10, 32)
			if err != nil {
				return nil, fmt.Errorf("line %d: nest: %w", i+1, err)
			}
			nest = uint32(n)
		}
		if id == "" {
			return nil, fmt.Errorf("line %d: empty id", i+1)
		}
		if url == "" {
			return nil, fmt.Errorf("line %d: empty url", i+1)
		}
		out = append(out, RepoEntry{ID: id, URL: url, Nest: nest})
	}
	return out, nil
}

// UrlToPath: `https://gitlab.com/group/repo[.git][/]` → `group/repo`。
// gitlabURL の prefix を剥がし、末尾 `/` と `.git` を落とす。
func UrlToPath(url, gitlabURL string) (string, error) {
	url = strings.TrimSpace(url)
	base := strings.TrimRight(strings.TrimSpace(gitlabURL), "/")
	stripped, ok := strings.CutPrefix(url, base)
	if !ok {
		return "", fmt.Errorf("url %q does not start with gitlab url %q", url, base)
	}
	cleaned := strings.TrimLeft(stripped, "/")
	cleaned = strings.TrimRight(cleaned, "/")
	cleaned = strings.TrimSuffix(cleaned, ".git")
	cleaned = strings.TrimRight(cleaned, "/")
	if cleaned == "" {
		return "", errors.New(fmt.Sprintf("url %q extracted to empty path", url))
	}
	return cleaned, nil
}
