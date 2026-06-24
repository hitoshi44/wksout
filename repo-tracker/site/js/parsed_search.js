// parsed フィールドベースの検索。
//
// データソースは `files/<repo_id>/<path>.json` (個別 TrackedFile)。
// 検索 1 回で対象 kind の全ファイルを並列 fetch し、parsed 上を線形に走査する。
// (集約済み索引は DESIGN「集計済みインデックスは入れない」に従い作らない)

import { loadRepos, loadFile } from './store.js';

export const PARSED_MODES = [
  { id: 'dep',     label: '依存ライブラリ (package.json)', kind: 'package-json' },
  { id: 'mvn',     label: 'Maven 依存 (pom.xml)',         kind: 'pom-xml' },
  { id: 'image',   label: 'Docker image (.gitlab-ci.yml)', kind: 'gitlab-ci' },
  { id: 'include', label: 'CI include',                   kind: 'gitlab-ci' },
];

export function isImplemented(mode) {
  return PARSED_MODES.some(m => m.id === mode);
}

export function getMode(id) {
  return PARSED_MODES.find(m => m.id === id);
}

// kind に該当する全 (repo, file) を並列 load。
async function collectFiles(kind) {
  const repos = (await loadRepos()).repos || [];
  const tasks = [];
  for (const r of repos) {
    for (const f of r.files || []) {
      if (f.type === kind) {
        tasks.push(
          loadFile(r.id, f.path)
            .then(file => ({ repo: r, file }))
            .catch(e => ({ repo: r, file: null, error: e })),
        );
      }
    }
  }
  return Promise.all(tasks);
}

export async function search(mode, query) {
  const m = getMode(mode);
  if (!m) throw new Error('unknown mode: ' + mode);
  const items = (await collectFiles(m.kind)).filter(x => x.file);
  return runSearch(mode, query, items);
}

// 純関数: items は [{repo, file}] の配列。テストはこの関数を直接呼ぶ。
export function runSearch(mode, query, items) {
  const needle = (query || '').toLowerCase();
  if (!needle) return [];
  switch (mode) {
    case 'dep':     return searchDep(items, needle);
    case 'mvn':     return searchMvn(items, needle);
    case 'image':   return searchImage(items, needle);
    case 'include': return searchInclude(items, needle);
    default:        return [];
  }
}

function searchDep(items, needle) {
  const out = [];
  for (const { repo, file } of items) {
    const p = file.parsed || {};
    for (const scope of ['dependencies', 'devDependencies', 'peerDependencies']) {
      const m = p[scope] || {};
      for (const [name, version] of Object.entries(m)) {
        if (name.toLowerCase().includes(needle)) {
          out.push({ repo_id: repo.id, repo_path: repo.path, path: file.path, scope, name, version });
        }
      }
    }
  }
  return out;
}

function searchMvn(items, needle) {
  const out = [];
  for (const { repo, file } of items) {
    const deps = (file.parsed || {}).dependencies || [];
    for (const d of deps) {
      const coord = `${d.groupId || ''}:${d.artifactId || ''}`;
      if (coord.toLowerCase().includes(needle)) {
        out.push({
          repo_id: repo.id, repo_path: repo.path, path: file.path,
          groupId: d.groupId, artifactId: d.artifactId, version: d.version, scope: d.scope,
        });
      }
    }
  }
  return out;
}

function searchImage(items, needle) {
  const out = [];
  for (const { repo, file } of items) {
    const images = (file.parsed || {}).images || [];
    for (const img of images) {
      if (img.toLowerCase().includes(needle)) {
        out.push({ repo_id: repo.id, repo_path: repo.path, path: file.path, image: img });
      }
    }
  }
  return out;
}

function searchInclude(items, needle) {
  const out = [];
  for (const { repo, file } of items) {
    const includes = (file.parsed || {}).includes || [];
    for (const inc of includes) {
      const ref = describeInclude(inc);
      if (ref.toLowerCase().includes(needle)) {
        out.push({ repo_id: repo.id, repo_path: repo.path, path: file.path, type: inc.type, ref });
      }
    }
  }
  return out;
}

export function describeInclude(inc) {
  if (!inc || !inc.type) return '';
  switch (inc.type) {
    case 'local':     return inc.file || '';
    case 'remote':    return inc.url || '';
    case 'template':  return inc.name || '';
    case 'component': return inc.component || '';
    case 'project': {
      const head = (inc.project || '') + (inc.ref ? '@' + inc.ref : '');
      const files = (inc.files || []).join(', ');
      return files ? `${head}: ${files}` : head;
    }
    default: return '';
  }
}
