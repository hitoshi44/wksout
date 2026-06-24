# Repo Tracker 設計

## 概要

GitLab API を使って、複数リポジトリの `.gitlab-ci.yml` / `package.json` / `pom.xml` を定期的に収集し、検索可能な静的サイトとして公開する。fetcher はローカルで動作し、成果物（JSON / HTML / JS）を S3 に置く。サーバサイドは持たない。

## 全体構成

```
 ┌─────────────┐    ┌───────────┐
 │ config.yml  │───▶│  Fetcher  │── GitLab API (env: GITLAB_TOKEN)
 │ - repo list │    │  (local)  │
 └─────────────┘    └─────┬─────┘
                          │ aws put-object
                          ▼
                   ┌────────────┐    ┌──────────────┐
                   │ S3 bucket  │───▶│ 静的サイト   │
                   │ (静的)     │    │ HTML + JS    │
                   └────────────┘    └──────────────┘
```

3 つの独立部品で構成する:

| 部品 | 責務 | 動く場所 |
|---|---|---|
| Fetcher | GitLab を叩く / パース / S3 へ書く | ローカル（OS の cron などから起動） |
| 静的サイト | JSON を読んで描画 + 全文検索 | S3 + ブラウザ |
| Config | 追跡対象の宣言（明示リスト） | リポジトリ内 or fetcher のローカル |

スケジューラは fetcher 内部に持たず、OS 側（`cron` / `launchd` 等）から起動する。

## Fetcher の動作仕様

| 項目 | 仕様 |
|---|---|
| 実装言語 | Go (標準 `net/http` + `crypto/tls`) |
| 認証 | 環境変数 `GITLAB_TOKEN` を読む（S3 は AWS の通常の認証経路） |
| 対象選定 | config の明示リスト |
| ブランチ | default branch のみ |
| ファイル探索 | tree API を深さ N まで GET。デフォルト N=1 (root + root 直下の tree)。repo ごとに `depth: N` で上書き可 |
| ページング | しない。1 階層あたり 100 件超は非対応 (per_page=100 固定) |
| 拾うファイル | `.gitlab-ci.yml`, `package.json`, `pom.xml`, `Dockerfile` |
| レート制御 | 1 fetch ごとに 1 秒 sleep |
| 更新スキップ | repo ごとに meta の `last_activity_at` を `data/.meta-cache.json` に記録。次回、前回値と一致する repo は **tree/files の取得を省き、前回の成果物 (repos.json + *-raws.json + files/) を持ち越す** (meta の 1 req だけは毎回行う)。`--force` / `-f` で無視して全件再取得。`last_activity_at` は default branch への push で必ず動くのでコード変更は取りこぼさない (無関係な活動でも再取得しうる安全側)。 |
| エラー処理 | 2 段階で吸収。**(a) HTTP エラー (meta / tree / files API)** は repo 単位でスキップ (`warning: skip id=...`)。空 repository の 404 tree や個別 repo の権限失敗が全体を止めないように。**(b) parse 失敗** はファイル単位でスキップ相当 (`warning: parse ... failed`)、empty parsed で raw のみ保存して続行。S3 への部分書き込みはしない (全件メモリに集めて最後にまとめて書き出す)。 |
| 履歴 | 持たない。毎回 S3 を上書き (更新スキップ分は前回値をそのまま持ち越して書き出す) |

探索範囲のイメージ (depth=1): `repo/package.json`, `repo/frontend/package.json` は拾う。`repo/services/api/backend/pom.xml` のような 2 階層以上は拾わない。特定 repo だけ深く掘りたい場合は config 側で `depth` を上書きする。

### 設定ファイル

2 ファイル構成。共通設定は yaml、追跡対象リストは CSV (外部 sh で生成する想定で疎結合)。

`config.yml`:

```yaml
gitlab:
  url: https://gitlab.example.com
  token_env: GITLAB_TOKEN
defaults:
  nest: 1                 # repos.csv で nest 省略時のデフォルト
repos_file: repos.csv     # config.yml と同じディレクトリ基準で解決
```

`repos.csv` (ヘッダなし、`#` でコメント可):

```
# id,url[,nest]
1234,https://gitlab.example.com/group/repo-a
5678,https://gitlab.example.com/group/sub/repo-b,3
9012,https://gitlab.example.com/group/repo-c,0
```

- `id` 列を **GitLab API の endpoint で直接使う** (`/api/v4/projects/:id`)。
  path 抽出や gitlab.url との prefix 一致は不要、subpath instance や url 表記揺れに強い
- `url` は記録用 (エラー時のヒントなど、fetcher 内では使わない)
- `nest` 省略時は `defaults.nest`
- 起動: `go run ./cmd/repo-tracker path/to/config.yml` (省略時は cwd の `config.yml`)
- s3 周りは未実装 (`site/` をそのまま sync する想定なのでバケット名は持たない)

## S3 レイアウト

```
s3://bucket/
  index.html / style.css / vendor/   # 静的サイト本体
  js/...                              # フロント JS
  data/
    repos.json                      # fetch 時刻 + repo 一覧（軽量メタ）
    repos/<repo_id>.json            # repo 詳細
    files/<repo_id>/<path>.json     # 個別ファイル (raw + parsed)
    ci-raws.json                    # 全 .gitlab-ci.yml の raw をバンドル（全文検索用）
    pkg-raws.json                   # 全 package.json   の raw をバンドル（全文検索用）
    pom-raws.json                   # 全 pom.xml       の raw をバンドル（全文検索用）
    docker-raws.json                # 全 Dockerfile    の raw をバンドル（全文検索用）
```

ローカルでは `site/` 配下が同じレイアウト (`site/data/*.json` 等) になる。
fetcher (`go run ./cmd/repo-tracker`) は `site/data/` に直接書き込む。S3 へは `site/` をそのまま sync。

## データ構造

### repos.json (トップレベル)

```jsonc
{
  "fetched_at": "...",
  "repos": [ /* Repository[] */ ]
}
```

### Repository

```jsonc
{
  "id": 1234,
  "path": "group/repo",
  "name": "repo",
  "url": "...",
  "fetched_at": "...",
  "files": [
    {"type": "package-json", "path": "frontend/package.json"},
    {"type": "pom-xml",      "path": "pom.xml"},
    {"type": "gitlab-ci",    "path": ".gitlab-ci.yml"}
  ]
}
```

### TrackedFile (共通)

```jsonc
{
  "repo_id": 1234,
  "path": "frontend/package.json",
  "type": "package-json",
  "blob_sha": "...",
  "size": 1234,
  "raw": "...",
  "parsed": { /* 種別ごと */ }
}
```

### parsed: package.json

```jsonc
{
  "name": "...", "version": "...",
  "dependencies":    {"lodash": "^4.0.0"},
  "devDependencies": {},
  "peerDependencies":{},
  "scripts":         {"build": "..."},
  "engines":         {"node": ">=18"}
}
```

### parsed: pom.xml

```jsonc
{
  "groupId": "...", "artifactId": "...", "version": "...",
  "parent": {"groupId": "...", "artifactId": "...", "version": "..."},
  "properties": {"java.version": "17"},
  "dependencies": [
    {"groupId": "...", "artifactId": "...", "version": "...", "scope": "compile"}
  ],
  "plugins": [
    {"groupId": "...", "artifactId": "...", "version": "..."}
  ]
}
```

### parsed: .gitlab-ci.yml

```jsonc
{
  "stages": ["build", "test"],
  "default": {"image": "...", "tags": [...]},
  "variables": {...},
  "includes": [
    {"type": "local",    "file": "ci/build.yml"},
    {"type": "project",  "project": "group/templates", "ref": "main", "files": ["ci/java.yml"]},
    {"type": "remote",   "url": "https://..."},
    {"type": "template", "name": "Jobs/SAST.gitlab-ci.yml"},
    {"type": "component","component": "gitlab.com/group/comp@1.0"}
  ],
  "jobs": [
    {
      "name": "build", "stage": "build",
      "image": "node:18", "tags": ["docker"],
      "extends": [".base"], "needs": ["lint"],
      "script": ["npm ci", "npm run build"]
    }
  ],
  "images": ["node:18", "alpine:3"]
}
```

`includes` はフラット配列で、解決やネストは行わない。

### ci-raws.json / pkg-raws.json / pom-raws.json（全文検索用バンドル）

3 ファイルとも構造は同一。種別はファイル名で区別する。

```jsonc
[
  {"repo_id": 1234, "path": ".gitlab-ci.yml", "raw": "..."},
  {"repo_id": 5678, "path": ".gitlab-ci.yml", "raw": "..."}
]
```

## 静的サイト

- **vanilla JS**（必要になれば Svelte などに移行可）
- 画面構成（最小）:

  | 画面 | 役割 |
  |---|---|
  | トップ | 追跡 repo 一覧、検索ボックス |
  | 検索結果 | クエリにマッチした repo / ファイル断片 |
  | repo 詳細 | その repo にある追跡ファイル一覧 |
  | ファイル詳細 | raw と parsed の両方を表示 |

## 検索

クライアントサイドの grep ベース。事前インデックスは張らない。

主な検索軸:

- 依存ライブラリ名（`lodash` → `package.json` の dependencies にヒット）
- Docker image（`node:18` → `.gitlab-ci.yml` の image にヒット）
- `.gitlab-ci.yml` の全文検索
- CI template の依存関係（`includes` を見て、特定 template を使っている repo を抽出）

実装は: `ci-raws.json` + 必要に応じて `files/<id>/<path>.json` をクライアントに読ませて文字列マッチ。

## 実装メモ

### ディレクトリ構成

```
repo-tracker/
  config.example.yml         # 共通サンプル
  repos.example.csv          # 共通サンプル
  fetcher-go/                # Go 版 (net/http + crypto/tls)
    go.mod / go.sum
    cmd/repo-tracker/main.go
    internal/{config,model,gitlab,parser}/
  site/                      # 静的サイト (vanilla JS, water.css 同梱)
    data/                    # fetcher の成果物が出る (config.output_dir 既定)
```

fetcher は `config.yml` + `repos.csv` を読み、`site/data/` レイアウトの JSON を書く。
JSON フォーマット (Repository / TrackedFile / RawEntry / Parsed*)。フロントは fetcher を意識しない。

**TLS/プロキシ**: `crypto/tls` の純 Go 実装で Windows でも schannel を経由せず、`HTTP_PROXY` を自動認識する。

### 依存 module

**Go 版** (`fetcher-go/`):

| module | 用途 |
|---|---|
| net/http (標準) | HTTP クライアント。`HTTP_PROXY` 環境変数を自動認識 |
| crypto/tls (標準) | TLS。純 Go 実装で Windows でも schannel を経由しない |
| encoding/json (標準) | JSON / package.json |
| encoding/xml (標準) | pom.xml |
| gopkg.in/yaml.v3 | config.yml + .gitlab-ci.yml (唯一の外部依存) |

### 利用する GitLab API

| エンドポイント | 用途 |
|---|---|
| `GET /projects/:id` | Repository メタ（id, name, default_branch, web_url, last_activity_at）。`default_branch` はファイル取得時の `ref` に使うために内部で読む（出力 JSON には載せない）。`last_activity_at` は更新スキップ判定のキャッシュキー（同上） |
| `GET /projects/:id/repository/tree?per_page=100&path=...` | ファイル一覧。`path` を切り替えて nest 段まで再帰 |
| `GET /projects/:id/repository/files/:path?ref=...` | raw + `blob_id` + `size` を一発取得（content は base64） |

レート制御 (1 fetch / 秒) は `gitlab.go` 内の共通 GET ヘルパに集約。すべての GET レスポンス受信後に 1 秒 sleep。

### ファイル種別の判定

ファイル名の exact match:

| name | FileKind |
|---|---|
| `package.json` | `PackageJson` |
| `pom.xml` | `PomXml` |
| `.gitlab-ci.yml` | `GitlabCi` |
| `Dockerfile` | `Dockerfile` (構造化しない、raw のみ) |

`gitlab::list_tracked_files` の `targets: &[&str]` で「拾うファイル名のリスト」を呼び出し側から渡せる。種別分類は内部の `classify` が担当。

### 認証

`GITLAB_TOKEN` 環境変数があれば `PRIVATE-TOKEN` ヘッダを付ける。無い場合は付けない（公開リポジトリは token 無しで読める）。

### per_page=100 固定の影響

DESIGN 通りページングしない。root の entries が 100 件を超える repo（例: `gitlab-org/gitlab-foss`）では末尾の項目が拾えなくなる。深く掘りたい / 多い repo は config 側で `depth` を上げるか、ページング非対応を受け入れる前提。

## あえて入れないもの

- 履歴 / snapshot（最新のみ）
- CI include の DAG / グラフ表現（includes はフラット配列のみ）
- 集計済みインデックス（`deps.json` 等）
- fetcher 内部のスケジューラ
- 部分失敗の救済（全失敗に寄せる）

将来必要になったら追加で良い。データ構造を壊さず後付けできる範囲に収めている。

