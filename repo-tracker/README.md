# repo-tracker

GitLab の複数リポジトリから設定ファイル（`.gitlab-ci.yml` / `package.json` / `pom.xml` / `Dockerfile`）を収集し、横断検索できる静的サイトを生成するツール。

- **fetcher**（Go）: GitLab API からデータを集めて `site/data/` に JSON を書き出す（ローカル実行）
- **site**: `site/` 配下の静的サイト。`site/data/` の JSON を読んでブラウザ上で検索する

設計の詳細は [DESIGN.md](./DESIGN.md) を参照。

## Quick start

### 1. 必要ファイルを用意

| ファイル / 環境変数 | 内容 | 用意の仕方 |
|---|---|---|
| `config.yml` | GitLab URL・トークン名・出力先などの設定 | `cp config.example.yml config.yml` して編集 |
| `repos.csv` | 追跡対象リスト `id,url[,nest]` | `cp repos.example.csv repos.csv` して編集 |
| `GITLAB_TOKEN`（環境変数） | GitLab アクセストークン（変数名は `config.yml` の `gitlab.token_env`） | `export GITLAB_TOKEN=glpat-xxxxx` |

`config.yml` と `repos.csv` は `.gitignore` 対象。出力先 `site/data/` は自動生成される。

### 2. fetcher を回す

```bash
export GITLAB_TOKEN=glpat-xxxxx
cd fetcher-go
go run ./cmd/repo-tracker ../config.yml      # 通常（更新なしの repo はスキップ）
go run ./cmd/repo-tracker ../config.yml -f    # 全件を強制再取得（キャッシュ無視）
```

- `config.yml` 内の相対パス（`repos_file` / `output_dir`）は **`config.yml` のあるディレクトリ基準**で解決される。
- 更新なしの repo は `data/.meta-cache.json` の `last_activity_at` で判定し、ファイル取得を省いて前回分を持ち越す。

### 3. サイトを見る

```bash
cd site
python -m http.server 8000
# http://localhost:8000/ を開く
```

> `index.html` を直接 `file://` で開くと ES module が読めないので、必ず HTTP サーバ経由で開く。

## 定期実行

スケジューラは持たない。`cron` / `launchd` などから上記の fetcher コマンドを定期実行する。デプロイは `site/` をそのまま S3 等へ sync すれば、同じ相対パスで動く。

## テスト

```bash
cd fetcher-go && go test ./...   # fetcher（Go）
# site のフロントは site/test/test.html をブラウザで開く
```
