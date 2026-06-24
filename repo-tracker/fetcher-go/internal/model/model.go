// Package model: fetcher が site/data/ に書き出す JSON 形状の型。
// フロント (site/js) が読む JSON 形式に一致させる。
//
// 命名規則:
//   - JSON タグは snake_case / camelCase / kebab-case をフロントの期待に合わせる
//   - 空でも JSON に出るべき map/slice はコンストラクタで初期化 (omitempty 付けない)
//   - 任意フィールドは *T + omitempty
package model

// FileKind の値 (TrackedFile.Type, RepoFileRef.Type で使う文字列)。
const (
	KindPackageJson = "package-json"
	KindPomXml      = "pom-xml"
	KindGitlabCi    = "gitlab-ci"
	KindDockerfile  = "dockerfile"
)

// ---------- Repository / repos.json ----------

type Repository struct {
	ID        uint64        `json:"id"`
	Path      string        `json:"path"`
	Name      string        `json:"name"`
	URL       string        `json:"url"`
	FetchedAt string        `json:"fetched_at"`
	Files     []RepoFileRef `json:"files"`
}

type RepoFileRef struct {
	Type string `json:"type"`
	Path string `json:"path"`
}

// ---------- TrackedFile / files/<id>/<path>.json ----------

type TrackedFile struct {
	RepoID  uint64      `json:"repo_id"`
	Path    string      `json:"path"`
	Type    string      `json:"type"`
	BlobSHA string      `json:"blob_sha"`
	Size    uint64      `json:"size"`
	Raw     string      `json:"raw"`
	Parsed  interface{} `json:"parsed"`
}

// ---------- RawEntry / *-raws.json ----------

type RawEntry struct {
	RepoID uint64 `json:"repo_id"`
	Path   string `json:"path"`
	Raw    string `json:"raw"`
}

// ---------- parsed: package.json ----------

type ParsedPackageJson struct {
	Name             *string           `json:"name,omitempty"`
	Version          *string           `json:"version,omitempty"`
	Dependencies     map[string]string `json:"dependencies"`
	DevDependencies  map[string]string `json:"devDependencies"`
	PeerDependencies map[string]string `json:"peerDependencies"`
	Scripts          map[string]string `json:"scripts"`
	Engines          map[string]string `json:"engines"`
}

func NewParsedPackageJson() *ParsedPackageJson {
	return &ParsedPackageJson{
		Dependencies:     map[string]string{},
		DevDependencies:  map[string]string{},
		PeerDependencies: map[string]string{},
		Scripts:          map[string]string{},
		Engines:          map[string]string{},
	}
}

// ---------- parsed: pom.xml ----------

type ParsedPomXml struct {
	GroupId      *string           `json:"groupId,omitempty"`
	ArtifactId   *string           `json:"artifactId,omitempty"`
	Version      *string           `json:"version,omitempty"`
	Parent       *PomCoordinate    `json:"parent,omitempty"`
	Properties   map[string]string `json:"properties"`
	Dependencies []PomDependency   `json:"dependencies"`
	Plugins      []PomPlugin       `json:"plugins"`
}

func NewParsedPomXml() *ParsedPomXml {
	return &ParsedPomXml{
		Properties:   map[string]string{},
		Dependencies: []PomDependency{},
		Plugins:      []PomPlugin{},
	}
}

type PomCoordinate struct {
	GroupId    string `json:"groupId"`
	ArtifactId string `json:"artifactId"`
	Version    string `json:"version"`
}

type PomDependency struct {
	GroupId    string  `json:"groupId"`
	ArtifactId string  `json:"artifactId"`
	Version    *string `json:"version,omitempty"`
	Scope      *string `json:"scope,omitempty"`
}

type PomPlugin struct {
	GroupId    string  `json:"groupId"`
	ArtifactId string  `json:"artifactId"`
	Version    *string `json:"version,omitempty"`
}

// ---------- parsed: .gitlab-ci.yml ----------

type ParsedGitlabCi struct {
	Stages    []string          `json:"stages"`
	Default   *CiDefault        `json:"default,omitempty"`
	Variables map[string]string `json:"variables"`
	Includes  []CiInclude       `json:"includes"`
	Jobs      []CiJob           `json:"jobs"`
	Images    []string          `json:"images"`
}

func NewParsedGitlabCi() *ParsedGitlabCi {
	return &ParsedGitlabCi{
		Stages:    []string{},
		Variables: map[string]string{},
		Includes:  []CiInclude{},
		Jobs:      []CiJob{},
		Images:    []string{},
	}
}

type CiDefault struct {
	Image *string  `json:"image,omitempty"`
	Tags  []string `json:"tags"`
}

// CiInclude は tagged union を 1 つの struct に畳んだもの。
// Type ごとに使うフィールドが違い、不使用フィールドは omitempty で省略する。
//
// type=local     : File
// type=remote    : URL
// type=template  : Name
// type=component : Component
// type=project   : Project (+ Ref?, Files[])
type CiInclude struct {
	Type      string   `json:"type"`
	File      string   `json:"file,omitempty"`
	URL       string   `json:"url,omitempty"`
	Name      string   `json:"name,omitempty"`
	Component string   `json:"component,omitempty"`
	Project   string   `json:"project,omitempty"`
	Ref       string   `json:"ref,omitempty"`
	Files     []string `json:"files,omitempty"`
}

type CiJob struct {
	Name    string   `json:"name"`
	Stage   *string  `json:"stage,omitempty"`
	Image   *string  `json:"image,omitempty"`
	Tags    []string `json:"tags"`
	Extends []string `json:"extends"`
	Needs   []string `json:"needs"`
	Script  []string `json:"script"`
}

// ParsedUnstructured: 構造化しない kind (Dockerfile) 用の空 parsed。JSON では `{}`。
type ParsedUnstructured struct{}

// ParsedFor は kind に応じて初期化済みの parsed 構造体を返す。
func ParsedFor(kind string) interface{} {
	switch kind {
	case KindPackageJson:
		return NewParsedPackageJson()
	case KindPomXml:
		return NewParsedPomXml()
	case KindGitlabCi:
		return NewParsedGitlabCi()
	case KindDockerfile:
		return ParsedUnstructured{}
	}
	return nil
}

// ClassifyFileName: 拾うファイル名から FileKind 文字列を返す。
func ClassifyFileName(name string) (string, bool) {
	switch name {
	case "package.json":
		return KindPackageJson, true
	case "pom.xml":
		return KindPomXml, true
	case ".gitlab-ci.yml":
		return KindGitlabCi, true
	case "Dockerfile":
		return KindDockerfile, true
	}
	return "", false
}

// ---------- CI template catalog / templates.json ----------
//
// 既存の repos.json 系とは別系統の出力。ci_templates 設定で指定したリポごとに
// README と templates_dir 配下の yml をまとめ、各 yml が参照する環境変数
// (CI_* は除外済み) を載せる。フロントの #/templates ビューが読む。

type CITemplateCatalog struct {
	FetchedAt string               `json:"fetched_at"`
	Repos     []CITemplateRepoData `json:"repos"`
}

type CITemplateRepoData struct {
	Repo       string           `json:"repo"`
	Name       string           `json:"name"`
	URL        string           `json:"url"`
	Ref        string           `json:"ref,omitempty"` // 収集したブランチ。フロントが GitLab blob URL を組むのに使う。
	ReadmePath string           `json:"readme_path,omitempty"`
	Readme     string           `json:"readme"`
	Templates  []CITemplateFile `json:"templates"`
}

type CITemplateFile struct {
	Path   string   `json:"path"`
	Name   string   `json:"name"`
	Stages []string `json:"stages"`
	Jobs   []string `json:"jobs"`
	Vars   []string `json:"vars"` // 参照される環境変数。CI_* 除外・重複除去・ソート済み。
}
