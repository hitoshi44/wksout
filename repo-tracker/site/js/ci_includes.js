// CI include の横断一覧。
//
// 全 repo の .gitlab-ci.yml を並列 fetch し、parsed.includes をフラットに展開する。

import { loadRepos, loadFile } from './store.js';
import { describeInclude } from './parsed_search.js';

export function isImplemented() {
  return true;
}

// 純関数: items は [{repo, file}]。テスト用。
// project 型の include のみ抽出する (他の type は対象外)。
export function buildRows(items) {
  const out = [];
  for (const { repo, file } of items) {
    const includes = (file.parsed || {}).includes || [];
    for (const inc of includes) {
      if (inc.type !== 'project') continue;
      out.push({
        repo_id: repo.id,
        repo_path: repo.path,
        path: file.path,
        ref: describeInclude(inc),
      });
    }
  }
  return out;
}

export async function loadAndBuildRows() {
  const repos = (await loadRepos()).repos || [];
  const tasks = [];
  for (const r of repos) {
    for (const f of r.files || []) {
      if (f.type === 'gitlab-ci') {
        tasks.push(
          loadFile(r.id, f.path)
            .then(file => ({ repo: r, file }))
            .catch(() => null),
        );
      }
    }
  }
  const items = (await Promise.all(tasks)).filter(Boolean);
  return buildRows(items);
}
