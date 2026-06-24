package config

import (
	"path/filepath"
	"testing"
)

// ---------- UrlToPath ----------

func TestUrlToPathBasic(t *testing.T) {
	got, err := UrlToPath("https://gitlab.com/group/repo", "https://gitlab.com")
	if err != nil || got != "group/repo" {
		t.Fatalf("got (%q, %v)", got, err)
	}
}
func TestUrlToPathStripsDotGit(t *testing.T) {
	got, _ := UrlToPath("https://gitlab.com/group/repo.git", "https://gitlab.com")
	if got != "group/repo" {
		t.Fatalf("got %q", got)
	}
}
func TestUrlToPathStripsTrailingSlash(t *testing.T) {
	got, _ := UrlToPath("https://gitlab.com/group/repo/", "https://gitlab.com")
	if got != "group/repo" {
		t.Fatalf("got %q", got)
	}
}
func TestUrlToPathHandlesBaseTrailingSlash(t *testing.T) {
	got, _ := UrlToPath("https://gitlab.com/group/repo", "https://gitlab.com/")
	if got != "group/repo" {
		t.Fatalf("got %q", got)
	}
}
func TestUrlToPathNested(t *testing.T) {
	got, _ := UrlToPath("https://gitlab.com/g/sub/r", "https://gitlab.com")
	if got != "g/sub/r" {
		t.Fatalf("got %q", got)
	}
}
func TestUrlToPathMismatchedBaseErrors(t *testing.T) {
	if _, err := UrlToPath("https://other.com/g/r", "https://gitlab.com"); err == nil {
		t.Fatal("expected error")
	}
}
func TestUrlToPathEmptyPathErrors(t *testing.T) {
	if _, err := UrlToPath("https://gitlab.com/", "https://gitlab.com"); err == nil {
		t.Fatal("expected error")
	}
}

// ---------- ParseReposLines ----------

func TestParseReposMinimal(t *testing.T) {
	csv := "1,https://gitlab.com/g/a\n2,https://gitlab.com/g/b,3\n"
	r, err := ParseReposLines(csv, 1)
	if err != nil {
		t.Fatal(err)
	}
	if len(r) != 2 {
		t.Fatalf("len %d", len(r))
	}
	if r[0] != (RepoEntry{ID: "1", URL: "https://gitlab.com/g/a", Nest: 1}) {
		t.Fatalf("r[0] = %+v", r[0])
	}
	if r[1] != (RepoEntry{ID: "2", URL: "https://gitlab.com/g/b", Nest: 3}) {
		t.Fatalf("r[1] = %+v", r[1])
	}
}
func TestParseReposSkipsBlankAndComments(t *testing.T) {
	csv := "# header\n\n1,https://gitlab.com/g/a\n  # indented comment\n2,https://gitlab.com/g/b,0\n"
	r, err := ParseReposLines(csv, 1)
	if err != nil {
		t.Fatal(err)
	}
	if len(r) != 2 {
		t.Fatalf("len %d", len(r))
	}
	if r[1].Nest != 0 {
		t.Fatalf("nest %d", r[1].Nest)
	}
}
func TestParseReposDefaultNestApplied(t *testing.T) {
	r, _ := ParseReposLines("1,https://gitlab.com/g/a\n", 5)
	if r[0].Nest != 5 {
		t.Fatalf("nest %d", r[0].Nest)
	}
}
func TestParseReposWrongColumnCountErrors(t *testing.T) {
	if _, err := ParseReposLines("1,url,3,extra\n", 1); err == nil {
		t.Fatal("expected error")
	}
	if _, err := ParseReposLines("only_one_field\n", 1); err == nil {
		t.Fatal("expected error")
	}
}
func TestParseReposBadNestErrors(t *testing.T) {
	if _, err := ParseReposLines("1,https://gitlab.com/g/a,not_a_number\n", 1); err == nil {
		t.Fatal("expected error")
	}
}
func TestParseReposEmptyIdErrors(t *testing.T) {
	if _, err := ParseReposLines(",https://gitlab.com/g/a\n", 1); err == nil {
		t.Fatal("expected error")
	}
}
func TestParseReposEmptyUrlErrors(t *testing.T) {
	if _, err := ParseReposLines("1,\n", 1); err == nil {
		t.Fatal("expected error")
	}
}
func TestParseReposTrimsWhitespace(t *testing.T) {
	r, err := ParseReposLines("  1 , https://gitlab.com/g/a , 2 \n", 1)
	if err != nil {
		t.Fatal(err)
	}
	if r[0] != (RepoEntry{ID: "1", URL: "https://gitlab.com/g/a", Nest: 2}) {
		t.Fatalf("r[0] = %+v", r[0])
	}
}

// ---------- ResolveRelative ----------

func TestResolveRelativeToConfigDir(t *testing.T) {
	got := ResolveRelative("/etc/repo-tracker/config.yml", "repos.csv")
	want := filepath.Join("/etc/repo-tracker", "repos.csv")
	if got != want {
		t.Fatalf("got %q want %q", got, want)
	}
}
func TestResolveRelativeAbsolutePassesThrough(t *testing.T) {
	got := ResolveRelative("/etc/config.yml", "/var/lib/repos.csv")
	if got != "/var/lib/repos.csv" {
		t.Fatalf("got %q", got)
	}
}
