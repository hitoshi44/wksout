// Package gitlab: GitLab REST API クライアント (必要な分だけ)。
// レート制御は 1 req/sec。
//
// プロキシは http.Transport がデフォルトで HTTP_PROXY / HTTPS_PROXY を見るので追加対応不要。
// TLS は crypto/tls (純 Go) で、Windows の schannel は経由しない。
package gitlab

import (
	"encoding/base64"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"strings"
	"sync"
	"time"

	"repo-tracker/internal/model"
)

// Client はレート制御と http.Client を持つ。
type Client struct {
	http  *http.Client
	token string

	// 1 fetch / 1 sec を守るためのロック。
	mu    sync.Mutex
	last  time.Time
	delay time.Duration
}

func New(token string) *Client {
	return &Client{
		http:  &http.Client{Timeout: 60 * time.Second},
		token: token,
		delay: time.Second,
	}
}

// sendJSON: GET → JSON デコード。1 req/sec のレート制限を強制する。
func (c *Client) sendJSON(rawURL string, out interface{}) error {
	c.wait()
	req, err := http.NewRequest(http.MethodGet, rawURL, nil)
	if err != nil {
		return err
	}
	req.Header.Set("Accept", "application/json")
	req.Header.Set("User-Agent", "repo-tracker/0.1")
	if c.token != "" {
		req.Header.Set("PRIVATE-TOKEN", c.token)
	}
	resp, err := c.http.Do(req)
	if err != nil {
		return err
	}
	defer resp.Body.Close()
	body, err := io.ReadAll(resp.Body)
	if err != nil {
		return err
	}
	if resp.StatusCode < 200 || resp.StatusCode >= 300 {
		return fmt.Errorf("HTTP %d: %s", resp.StatusCode, string(body))
	}
	return json.Unmarshal(body, out)
}

// 直前のリクエストから delay 経過するまで sleep。
func (c *Client) wait() {
	c.mu.Lock()
	defer c.mu.Unlock()
	if !c.last.IsZero() {
		elapsed := time.Since(c.last)
		if elapsed < c.delay {
			time.Sleep(c.delay - elapsed)
		}
	}
	c.last = time.Now()
}

// ---------- /projects/:id ----------

type ProjectMeta struct {
	ID                uint64 `json:"id"`
	Name              string `json:"name"`
	PathWithNamespace string `json:"path_with_namespace"`
	WebURL            string `json:"web_url"`
	DefaultBranch     string `json:"default_branch"`
	// LastActivityAt は更新検知用の軽量シグナル。push / issue / MR 等で更新される。
	// default branch への push は必ずこれを動かすので、コード変更を取りこぼさない
	// (代わりに無関係な活動でも再取得しうる — 安全側に倒している)。
	LastActivityAt string `json:"last_activity_at"`
}

func (c *Client) FetchProjectMeta(baseURL, project string) (*ProjectMeta, error) {
	u := fmt.Sprintf("%s/api/v4/projects/%s",
		trimTrailingSlash(baseURL),
		url.PathEscape(project),
	)
	var m ProjectMeta
	if err := c.sendJSON(u, &m); err != nil {
		return nil, err
	}
	return &m, nil
}

// ---------- /projects/:id/repository/tree (walked) ----------

type treeEntry struct {
	Name string `json:"name"`
	Type string `json:"type"` // "blob" or "tree"
	Path string `json:"path"`
}

// ListTrackedFiles: tree API を nest 回まで再帰して、targets に含まれる blob 名のみ返す。
func (c *Client) ListTrackedFiles(
	baseURL, project string,
	nest uint32,
	targets []string,
) ([]model.RepoFileRef, error) {
	var out []model.RepoFileRef
	if err := c.visitDir(baseURL, project, "", nest, targets, &out); err != nil {
		return nil, err
	}
	return out, nil
}

func (c *Client) visitDir(
	baseURL, project, path string,
	remaining uint32,
	targets []string,
	out *[]model.RepoFileRef,
) error {
	entries, err := c.fetchTree(baseURL, project, path)
	if err != nil {
		return err
	}
	for _, e := range entries {
		switch {
		case e.Type == "blob" && contains(targets, e.Name):
			if kind, ok := model.ClassifyFileName(e.Name); ok {
				*out = append(*out, model.RepoFileRef{Type: kind, Path: e.Path})
			}
		case e.Type == "tree" && remaining > 0:
			if err := c.visitDir(baseURL, project, e.Path, remaining-1, targets, out); err != nil {
				return err
			}
		}
	}
	return nil
}

func (c *Client) fetchTree(baseURL, project, path string) ([]treeEntry, error) {
	u := fmt.Sprintf("%s/api/v4/projects/%s/repository/tree?per_page=100",
		trimTrailingSlash(baseURL), url.PathEscape(project))
	if path != "" {
		u += "&path=" + url.QueryEscape(path)
	}
	var entries []treeEntry
	if err := c.sendJSON(u, &entries); err != nil {
		return nil, err
	}
	return entries, nil
}

// ListYAMLFiles: dir 配下を再帰して *.yml / *.yaml の blob path を返す。
// ci_templates のテンプレ収集用 (targets フィルタを使う ListTrackedFiles とは別系統)。
// dir が空ならリポジトリ全体を走査する。
func (c *Client) ListYAMLFiles(baseURL, project, dir string) ([]string, error) {
	var out []string
	if err := c.walkYAML(baseURL, project, dir, &out); err != nil {
		return nil, err
	}
	return out, nil
}

func (c *Client) walkYAML(baseURL, project, path string, out *[]string) error {
	entries, err := c.fetchTree(baseURL, project, path)
	if err != nil {
		return err
	}
	for _, e := range entries {
		switch e.Type {
		case "blob":
			if isYAMLName(e.Name) {
				*out = append(*out, e.Path)
			}
		case "tree":
			if err := c.walkYAML(baseURL, project, e.Path, out); err != nil {
				return err
			}
		}
	}
	return nil
}

func isYAMLName(name string) bool {
	return strings.HasSuffix(name, ".yml") || strings.HasSuffix(name, ".yaml")
}

// ---------- /projects/:id/repository/files/:path ----------

type RawFile struct {
	BlobSHA string
	Size    uint64
	Raw     string
}

type fileResp struct {
	Size     uint64 `json:"size"`
	Encoding string `json:"encoding"`
	BlobID   string `json:"blob_id"`
	Content  string `json:"content"`
}

func (c *Client) FetchFile(baseURL, project, ref, path string) (*RawFile, error) {
	u := fmt.Sprintf("%s/api/v4/projects/%s/repository/files/%s?ref=%s",
		trimTrailingSlash(baseURL),
		url.PathEscape(project),
		url.PathEscape(path),
		url.QueryEscape(ref),
	)
	var r fileResp
	if err := c.sendJSON(u, &r); err != nil {
		return nil, err
	}
	if r.Encoding != "base64" {
		return nil, fmt.Errorf("unexpected encoding: %s", r.Encoding)
	}
	decoded, err := base64.StdEncoding.DecodeString(r.Content)
	if err != nil {
		return nil, fmt.Errorf("base64 decode: %w", err)
	}
	return &RawFile{
		BlobSHA: r.BlobID,
		Size:    r.Size,
		Raw:     string(decoded),
	}, nil
}

// ---------- helpers ----------

func trimTrailingSlash(s string) string {
	for len(s) > 0 && s[len(s)-1] == '/' {
		s = s[:len(s)-1]
	}
	return s
}

func contains(xs []string, v string) bool {
	for _, x := range xs {
		if x == v {
			return true
		}
	}
	return false
}
