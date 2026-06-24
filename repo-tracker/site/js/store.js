// データ取得 + メモリキャッシュ。
//
// fetcher (`go run ./cmd/repo-tracker`) は `site/data/` 配下に書き出すので、site/ から相対で './data/'。
// ローカルは `cd site && python -m http.server 8000` で http://localhost:8000/ を開く。
// S3 デプロイは site/ をそのまま sync すれば同じ相対パスで動く。

const DATA_BASE = './data/';

const cache = new Map();

async function loadJson(name) {
  if (cache.has(name)) return cache.get(name);
  const p = fetch(DATA_BASE + name).then(r => {
    if (!r.ok) throw new Error(`${name}: HTTP ${r.status}`);
    return r.json();
  });
  cache.set(name, p);
  return p;
}

export function loadRepos()      { return loadJson('repos.json'); }
export function loadCiRaws()     { return loadJson('ci-raws.json'); }
export function loadPkgRaws()    { return loadJson('pkg-raws.json'); }
export function loadPomRaws()    { return loadJson('pom-raws.json'); }
export function loadDockerRaws() { return loadJson('docker-raws.json'); }
export function loadTemplates()  { return loadJson('templates.json'); }
export function loadFile(repoId, path) {
  return loadJson(`files/${repoId}/${path}.json`);
}

// テスト用: キャッシュをクリアして注入できるようにする。
export function _resetCache() { cache.clear(); }
export function _injectCache(name, value) { cache.set(name, Promise.resolve(value)); }
