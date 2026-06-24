import { loadRepos, loadCiRaws, loadPkgRaws, loadPomRaws, loadDockerRaws } from '../store.js';
import { grep, highlightPositionsMulti } from '../grep.js';
import { html, escapeHtml, raw } from '../render.js';
import { buildQuery } from '../router.js';

const GREP_KINDS = [
  { id: 'ci',     label: '.gitlab-ci.yml' },
  { id: 'pkg',    label: 'package.json' },
  { id: 'pom',    label: 'pom.xml' },
  { id: 'docker', label: 'Dockerfile' },
];

export async function render(_params, query) {
  const q             = query.q || '';
  const enabledKinds  = (query.kinds || 'ci,pkg,pom,docker').split(',').filter(Boolean);
  const caseSensitive = query.cs === '1';

  const form = renderForm({ q, enabledKinds, caseSensitive });

  let results;
  if (!q) {
    results = `<p class="stub">クエリを入力してください。</p>`;
  } else {
    results = await renderGrepResults(q, enabledKinds, caseSensitive);
  }

  return {
    html: form + results,
    after: () => { bindForm(); bindRepoFilter(); },
  };
}

function renderForm({ q, enabledKinds, caseSensitive }) {
  const grepKinds = GREP_KINDS.map(k => html`
    <label><input type="checkbox" name="kinds" value="${k.id}"
      ${raw(enabledKinds.includes(k.id) ? 'checked' : '')}> ${k.label}</label>
  `).join('');

  return html`
    <form class="search-form" id="search-form">
      <input type="text" name="q" value="${q}" placeholder="例: react webpack  /  react | vue" autofocus>
      <button type="submit">検索</button>
      <small class="search-hint">スペース=AND &middot; <code>|</code>=OR で1ファイルに対してADN/OR検索ができます.<br>例: <code>react webpack</code>="react AND webpack" / <code>react | vue</code>="react OR vue" / <code>a\\|b</code>="a|b"</small>
      <fieldset><legend>対象</legend>${raw(grepKinds)}</fieldset>
      <label class="cs"><input type="checkbox" name="cs" ${raw(caseSensitive ? 'checked' : '')}> case sensitive</label>
    </form>
  `;
}

async function renderGrepResults(q, enabledKinds, caseSensitive) {
  const sources = [];
  if (enabledKinds.includes('ci'))     sources.push({ kind: 'ci',     entries: await loadCiRaws() });
  if (enabledKinds.includes('pkg'))    sources.push({ kind: 'pkg',    entries: await loadPkgRaws() });
  if (enabledKinds.includes('pom'))    sources.push({ kind: 'pom',    entries: await loadPomRaws() });
  if (enabledKinds.includes('docker')) sources.push({ kind: 'docker', entries: await loadDockerRaws() });

  const hits = grep(sources, q, { caseSensitive });
  if (hits.length === 0) return `<p>マッチなし</p>`;

  const repos = (await loadRepos()).repos || [];
  const repoPathById = new Map(repos.map(r => [r.id, r.path]));
  const repoUrlById  = new Map(repos.map(r => [r.id, r.url]));

  // リポジトリごとに緩くまとめる。出現順を保ちつつ repo_id 単位でグルーピングし、
  // マッチ件数の多いリポジトリを上に出す (「どのリポジトリ?」に答えやすく)。
  const groups = groupByRepo(hits, repoPathById);

  const toolbar = renderRepoFilter(groups, hits.length);

  const sections = groups.map(g => {
    const items = g.hits.map(h => renderMatch(h, g.label, caseSensitive, repoUrlById.get(g.repoId))).join('');
    return html`
      <details class="repo-group" data-repo-id="${String(g.repoId)}" open>
        <summary class="repo-group-summary">
          <span class="repo-group-name">${g.label}</span>
          <span class="repo-group-count">${g.hits.length}</span>
        </summary>
        ${raw(items)}
      </details>`;
  }).join('');

  return `<p class="results-summary">${hits.length} match(es) / ${groups.length} repo(s)</p>` + toolbar + sections;
}

// hits を repo_id ごとにまとめる。出現順で初出のグループを作り、
// 返すときはマッチ件数 desc → ラベル昇順で並べる。
function groupByRepo(hits, repoPathById) {
  const byId = new Map();
  for (const h of hits) {
    let g = byId.get(h.repo_id);
    if (!g) {
      g = { repoId: h.repo_id, label: repoPathById.get(h.repo_id) || `repo ${h.repo_id}`, hits: [] };
      byId.set(h.repo_id, g);
    }
    g.hits.push(h);
  }
  return [...byId.values()].sort((a, b) =>
    b.hits.length - a.hits.length || a.label.localeCompare(b.label));
}

function renderRepoFilter(groups, total) {
  const options = groups.map(g => html`
    <option value="${String(g.repoId)}">${g.label} (${g.hits.length})</option>
  `).join('');
  return html`
    <div class="filter-form results-toolbar">
      <label>リポジトリ
        <select id="repo-filter">
          <option value="">すべて (${total})</option>
          ${raw(options)}
        </select>
      </label>
    </div>`;
}

// repo の GitLab URL とファイルパスから blob URL を組む (ref は HEAD = default branch)。
export function gitlabFileUrl(repoUrl, path) {
  if (!repoUrl || !path) return null;
  return `${repoUrl}/-/blob/HEAD/${path}`;
}

function renderMatch(h, repoLabel, caseSensitive, repoUrl) {
  const positions = highlightPositionsMulti(h.line, h.terms, caseSensitive);
  const lineHtml = highlightLine(h.line, positions);
  const glUrl = gitlabFileUrl(repoUrl, h.path);
  const glLink = glUrl
    ? html` &middot; <a class="gl-link" href="${glUrl}" rel="noopener noreferrer" target="_blank">GitLab ↗</a>`
    : '';
  return html`
    <div class="match">
      <div class="match-header">
        <span class="kind">${h.kind}</span> &middot;
        <a href="#/file/${h.repo_id}/${encodeURIComponent(h.path)}">${repoLabel} / <span class="path">${h.path}</span></a>
        : line ${h.line_no}${raw(glLink)}
      </div>
      <pre>${raw(lineHtml)}</pre>
    </div>`;
}

function highlightLine(line, positions) {
  if (positions.length === 0) return escapeHtml(line);
  let out = '';
  let cursor = 0;
  for (const [s, e] of positions) {
    out += escapeHtml(line.slice(cursor, s));
    out += '<mark>' + escapeHtml(line.slice(s, e)) + '</mark>';
    cursor = e;
  }
  out += escapeHtml(line.slice(cursor));
  return out;
}

// フォーム submit を hash に変換するハンドラ。
// router の after フック経由で、innerHTML 反映直後に呼ばれる。
function bindForm() {
  const form = document.getElementById('search-form');
  if (!form) return;
  form.addEventListener('submit', e => {
    e.preventDefault();
    const fd = new FormData(form);
    const params = {
      q: fd.get('q') || '',
      kinds: fd.getAll('kinds').join(','),
      cs: fd.get('cs') ? '1' : '',
    };
    location.hash = '#/search?' + buildQuery(params);
  });
}

// プルダウンでリポジトリを選ぶと、そのグループだけ残して他を隠す。
// 再フェッチせず DOM 上で絞り込むだけ (filter-form と同じ軽量方式)。
function bindRepoFilter() {
  const sel = document.getElementById('repo-filter');
  if (!sel) return;
  sel.addEventListener('change', () => {
    const v = sel.value;
    for (const g of document.querySelectorAll('.repo-group')) {
      g.style.display = (!v || g.dataset.repoId === v) ? '' : 'none';
    }
  });
}

export { highlightLine, groupByRepo };
