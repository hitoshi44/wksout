package main

import (
	"path/filepath"
	"testing"

	"repo-tracker/internal/model"
)

// 前回成果物を temp に書き出し → 読み戻して id で引けることを確認する。
// スキップ時の「前回分の持ち越し」がネットワークなしで成立するかの検証。
func TestLoadPrevOutputsAndLookup(t *testing.T) {
	dir := t.TempDir()

	rj := reposJson{
		FetchedAt: "2026-06-10T00:00:00Z",
		Repos: []model.Repository{
			{ID: 10, Path: "g/alpha", Files: []model.RepoFileRef{{Type: "package-json", Path: "package.json"}}},
			{ID: 11, Path: "g/beta"},
		},
	}
	if err := writeJSON(filepath.Join(dir, "repos.json"), rj); err != nil {
		t.Fatal(err)
	}
	if err := writeJSON(filepath.Join(dir, "pkg-raws.json"), []model.RawEntry{
		{RepoID: 10, Path: "package.json", Raw: `"react"`},
		{RepoID: 11, Path: "package.json", Raw: `"vue"`},
	}); err != nil {
		t.Fatal(err)
	}
	// 他の種別は空配列で書いておく (実運用と同じ)。
	for _, n := range []string{"ci-raws.json", "pom-raws.json", "docker-raws.json"} {
		if err := writeJSON(filepath.Join(dir, n), []model.RawEntry{}); err != nil {
			t.Fatal(err)
		}
	}

	prev := loadPrevOutputs(dir)

	repo, raws, ok := prev.lookup(10)
	if !ok {
		t.Fatal("repo 10 should be found")
	}
	if repo.Path != "g/alpha" {
		t.Fatalf("path = %q, want g/alpha", repo.Path)
	}
	if len(raws.pkg) != 1 || raws.pkg[0].Raw != `"react"` {
		t.Fatalf("pkg raws = %+v, want one react entry", raws.pkg)
	}
	if len(raws.ci) != 0 {
		t.Fatalf("ci raws = %+v, want empty", raws.ci)
	}

	// repo 11 はファイルなしでも repos.json にいれば ok。
	if _, _, ok := prev.lookup(11); !ok {
		t.Fatal("repo 11 should be found")
	}
	// 未知 id は見つからない → フル取得にフォールバックする。
	if _, _, ok := prev.lookup(999); ok {
		t.Fatal("repo 999 should NOT be found")
	}
}

// メタキャッシュの書き出し → 読み戻しが一致すること。
func TestMetaCacheRoundTrip(t *testing.T) {
	dir := t.TempDir()
	path := filepath.Join(dir, ".meta-cache.json")

	// キャッシュが無い場合は空 (nil でない) マップ。
	if c := loadMetaCache(path); c.Repos == nil || len(c.Repos) != 0 {
		t.Fatalf("missing cache should load empty, got %+v", c)
	}

	out := metaCache{Repos: map[string]repoCacheEntry{
		"10": {LastActivityAt: "2026-06-10T01:02:03Z"},
	}}
	if err := writeJSON(path, out); err != nil {
		t.Fatal(err)
	}
	got := loadMetaCache(path)
	if got.Repos["10"].LastActivityAt != "2026-06-10T01:02:03Z" {
		t.Fatalf("round-trip mismatch: %+v", got)
	}
}
