// repo-tracker (Go版): GitLab API から repo メタ + 追跡ファイルを集めて site/data/ に書き出す。
// 使い方: repo-tracker [path/to/config.yml]   (省略時 config.yml)
package main

import (
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"time"

	"repo-tracker/internal/config"
	"repo-tracker/internal/gitlab"
	"repo-tracker/internal/model"
	"repo-tracker/internal/parser"
	"repo-tracker/internal/templates"
)

func main() {
	if err := run(); err != nil {
		fmt.Fprintln(os.Stderr, "error:", err)
		os.Exit(1)
	}
}

type reposJson struct {
	FetchedAt string             `json:"fetched_at"`
	Repos     []model.Repository `json:"repos"`
}

func run() error {
	// --force / -f: キャッシュを無視して全 repo を再取得する。
	force := false
	args := os.Args[1:]
	configPath := "config.yml"
	for _, a := range args {
		switch a {
		case "--force", "-f":
			force = true
		default:
			configPath = a
		}
	}
	cfg, err := config.LoadConfig(configPath)
	if err != nil {
		return err
	}
	reposPath := config.ResolveRelative(configPath, cfg.ReposFile)
	entries, err := config.LoadRepos(reposPath, cfg.Defaults.Nest)
	if err != nil {
		return err
	}
	if len(entries) == 0 {
		return fmt.Errorf("no repos in %s", reposPath)
	}

	outDir := config.ResolveRelative(configPath, cfg.OutputDir)
	if err := os.MkdirAll(outDir, 0o755); err != nil {
		return err
	}

	baseURL := strings.TrimRight(cfg.GitLab.URL, "/")
	token := os.Getenv(cfg.GitLab.TokenEnv)
	targets := []string{".gitlab-ci.yml", "package.json", "pom.xml", "Dockerfile"}
	client := gitlab.New(token)

	fetchedAt := time.Now().UTC().Format(time.RFC3339)

	fmt.Printf("config: %s\n", configPath)
	fmt.Printf("repos:  %s (%d entries)\n", reposPath, len(entries))
	fmt.Printf("output: %s\n", outDir)

	// 前回の成果物とメタキャッシュを読む (更新なしの repo を再利用するため)。
	cachePath := filepath.Join(outDir, ".meta-cache.json")
	cache := loadMetaCache(cachePath)
	prev := loadPrevOutputs(outDir)
	newCache := metaCache{Repos: map[string]repoCacheEntry{}}

	var (
		repositories []model.Repository
		ciRaws       []model.RawEntry
		pkgRaws      []model.RawEntry
		pomRaws      []model.RawEntry
		dockerRaws   []model.RawEntry
		fileCount    int
	)

	skipped := 0
	reused := 0
	for _, entry := range entries {
		// メタだけ先に取得 (軽量: 1 req)。これで更新の有無を判定する。
		meta, err := client.FetchProjectMeta(baseURL, entry.ID)
		if err != nil {
			fmt.Fprintf(os.Stderr, "warning: skip id=%s (%s): meta: %v\n", entry.ID, entry.URL, err)
			skipped++
			continue
		}
		idStr := fmt.Sprintf("%d", meta.ID)

		// last_activity_at が前回と一致し、前回成果物が揃っていれば tree/files の
		// 取得をまるごとスキップして前回分を再利用する。
		if !force && meta.LastActivityAt != "" {
			if c, ok := cache.Repos[idStr]; ok && c.LastActivityAt == meta.LastActivityAt {
				if repo, raws, ok := prev.lookup(meta.ID); ok {
					repositories = append(repositories, repo)
					ciRaws = append(ciRaws, raws.ci...)
					pkgRaws = append(pkgRaws, raws.pkg...)
					pomRaws = append(pomRaws, raws.pom...)
					dockerRaws = append(dockerRaws, raws.docker...)
					newCache.Repos[idStr] = repoCacheEntry{LastActivityAt: meta.LastActivityAt}
					reused++
					fmt.Printf("  reuse %s (unchanged)\n", repo.Path)
					continue
				}
			}
		}

		res, err := processOneRepo(client, baseURL, entry, meta, targets, outDir, fetchedAt)
		if err != nil {
			fmt.Fprintf(os.Stderr, "warning: skip id=%s (%s): %v\n", entry.ID, entry.URL, err)
			skipped++
			continue
		}
		ciRaws = append(ciRaws, res.ciRaws...)
		pkgRaws = append(pkgRaws, res.pkgRaws...)
		pomRaws = append(pomRaws, res.pomRaws...)
		dockerRaws = append(dockerRaws, res.dockerRaws...)
		fileCount += res.filesWritten
		repositories = append(repositories, res.repo)
		newCache.Repos[idStr] = repoCacheEntry{LastActivityAt: meta.LastActivityAt}
	}

	rj := reposJson{FetchedAt: fetchedAt, Repos: repositories}
	if err := writeJSON(filepath.Join(outDir, "repos.json"), rj); err != nil {
		return err
	}
	if err := writeJSON(filepath.Join(outDir, "ci-raws.json"), nonNilSlice(ciRaws)); err != nil {
		return err
	}
	if err := writeJSON(filepath.Join(outDir, "pkg-raws.json"), nonNilSlice(pkgRaws)); err != nil {
		return err
	}
	if err := writeJSON(filepath.Join(outDir, "pom-raws.json"), nonNilSlice(pomRaws)); err != nil {
		return err
	}
	if err := writeJSON(filepath.Join(outDir, "docker-raws.json"), nonNilSlice(dockerRaws)); err != nil {
		return err
	}
	if err := writeJSON(cachePath, newCache); err != nil {
		return err
	}

	// ci_templates: 既存 repos.csv 系とは独立した収集。設定があれば templates.json を別出力。
	if len(cfg.CITemplates) > 0 {
		cat := templates.Collect(client, baseURL, cfg.CITemplates, fetchedAt)
		if err := writeJSON(filepath.Join(outDir, "templates.json"), cat); err != nil {
			return err
		}
		fmt.Printf("wrote %s: templates.json (%d template repo(s))\n", outDir, len(cat.Repos))
	}

	fmt.Printf("wrote %s: repos.json (%d ok, %d reused, %d skipped), ci-raws.json (%d), pkg-raws.json (%d), pom-raws.json (%d), docker-raws.json (%d), and %d file(s) under files/\n",
		outDir, len(repositories), reused, skipped, len(ciRaws), len(pkgRaws), len(pomRaws), len(dockerRaws), fileCount)
	for _, r := range repositories {
		fmt.Printf("  %s (%d file(s))\n", r.Path, len(r.Files))
	}
	return nil
}

type repoResult struct {
	repo         model.Repository
	ciRaws       []model.RawEntry
	pkgRaws      []model.RawEntry
	pomRaws      []model.RawEntry
	dockerRaws   []model.RawEntry
	filesWritten int
}

// 1 repo 分の処理。HTTP エラーは Err を返す (呼び出し側でスキップ)。
// parse 失敗はファイル単位で吸収 (warning + raw のみ)。
// ファイルは即時書き出し。失敗時にゴミが残るが、repos.json に該当 id が
// 載らないのでフロントからは参照されない。
func processOneRepo(
	client *gitlab.Client,
	baseURL string,
	entry config.RepoEntry,
	meta *gitlab.ProjectMeta,
	targets []string,
	outDir, fetchedAt string,
) (*repoResult, error) {
	files, err := client.ListTrackedFiles(baseURL, entry.ID, entry.Nest, targets)
	if err != nil {
		return nil, fmt.Errorf("tree: %w", err)
	}

	res := &repoResult{
		repo: model.Repository{
			ID:        meta.ID,
			Path:      meta.PathWithNamespace,
			Name:      meta.Name,
			URL:       meta.WebURL,
			FetchedAt: fetchedAt,
			Files:     files,
		},
	}

	for _, f := range files {
		raw, err := client.FetchFile(baseURL, entry.ID, meta.DefaultBranch, f.Path)
		if err != nil {
			return nil, fmt.Errorf("file %s: %w", f.Path, err)
		}
		rawEntry := model.RawEntry{RepoID: meta.ID, Path: f.Path, Raw: raw.Raw}
		switch f.Type {
		case model.KindGitlabCi:
			res.ciRaws = append(res.ciRaws, rawEntry)
		case model.KindPackageJson:
			res.pkgRaws = append(res.pkgRaws, rawEntry)
		case model.KindPomXml:
			res.pomRaws = append(res.pomRaws, rawEntry)
		case model.KindDockerfile:
			res.dockerRaws = append(res.dockerRaws, rawEntry)
		}

		// parse 失敗時は警告して empty parsed で続行 (raw のみ保存)。
		parsed, perr := parser.Parse(f.Type, raw.Raw)
		if perr != nil {
			fmt.Fprintf(os.Stderr,
				"warning: parse %s (%s) failed: %v — raw のみで続行\n",
				f.Path, f.Type, perr)
			parsed = model.ParsedFor(f.Type)
		}
		tracked := model.TrackedFile{
			RepoID:  meta.ID,
			Path:    f.Path,
			Type:    f.Type,
			BlobSHA: raw.BlobSHA,
			Size:    raw.Size,
			Raw:     raw.Raw,
			Parsed:  parsed,
		}
		outPath := filepath.Join(outDir, "files", fmt.Sprintf("%d", meta.ID), f.Path+".json")
		if err := writeJSON(outPath, tracked); err != nil {
			return nil, err
		}
		res.filesWritten++
	}
	return res, nil
}

// ---------- メタキャッシュ (更新なし repo のスキップ用) ----------

// .meta-cache.json: repo_id -> last_activity_at。これだけ持っておき、
// 次回 meta の last_activity_at と一致したら tree/files の取得を省く。
type metaCache struct {
	Repos map[string]repoCacheEntry `json:"repos"`
}

type repoCacheEntry struct {
	LastActivityAt string `json:"last_activity_at"`
}

func loadMetaCache(path string) metaCache {
	c := metaCache{Repos: map[string]repoCacheEntry{}}
	var loaded metaCache
	if readJSON(path, &loaded) == nil && loaded.Repos != nil {
		c.Repos = loaded.Repos
	}
	return c
}

// 前回の成果物 (repos.json + *-raws.json) を id で引けるよう読み込む。
// スキップした repo の分を集約 JSON に持ち越すのに使う (ネットワークアクセスなし)。
type repoRaws struct {
	ci, pkg, pom, docker []model.RawEntry
}

type prevOutputs struct {
	repos  map[uint64]model.Repository
	ci     map[uint64][]model.RawEntry
	pkg    map[uint64][]model.RawEntry
	pom    map[uint64][]model.RawEntry
	docker map[uint64][]model.RawEntry
}

func (p prevOutputs) lookup(id uint64) (model.Repository, repoRaws, bool) {
	repo, ok := p.repos[id]
	if !ok {
		return model.Repository{}, repoRaws{}, false
	}
	return repo, repoRaws{
		ci:     p.ci[id],
		pkg:    p.pkg[id],
		pom:    p.pom[id],
		docker: p.docker[id],
	}, true
}

func loadPrevOutputs(outDir string) prevOutputs {
	p := prevOutputs{
		repos:  map[uint64]model.Repository{},
		ci:     loadRawsByRepo(filepath.Join(outDir, "ci-raws.json")),
		pkg:    loadRawsByRepo(filepath.Join(outDir, "pkg-raws.json")),
		pom:    loadRawsByRepo(filepath.Join(outDir, "pom-raws.json")),
		docker: loadRawsByRepo(filepath.Join(outDir, "docker-raws.json")),
	}
	var rj reposJson
	if readJSON(filepath.Join(outDir, "repos.json"), &rj) == nil {
		for _, r := range rj.Repos {
			p.repos[r.ID] = r
		}
	}
	return p
}

func loadRawsByRepo(path string) map[uint64][]model.RawEntry {
	m := map[uint64][]model.RawEntry{}
	var arr []model.RawEntry
	if readJSON(path, &arr) != nil {
		return m
	}
	for _, e := range arr {
		m[e.RepoID] = append(m[e.RepoID], e)
	}
	return m
}

func readJSON(path string, v any) error {
	buf, err := os.ReadFile(path)
	if err != nil {
		return err
	}
	return json.Unmarshal(buf, v)
}

func writeJSON(path string, v any) error {
	if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
		return err
	}
	buf, err := json.MarshalIndent(v, "", "  ")
	if err != nil {
		return err
	}
	return os.WriteFile(path, buf, 0o644)
}

// JSON で [] を出すために、nil slice を空 slice に置き換える。
func nonNilSlice(xs []model.RawEntry) []model.RawEntry {
	if xs == nil {
		return []model.RawEntry{}
	}
	return xs
}
