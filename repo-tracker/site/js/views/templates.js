// CI テンプレートカタログ (#/templates)。
//
// templates.json (fetcher の ci_templates 収集の出力) を読み、左にテンプレ配布リポ一覧、
// 右に選択リポの README と yml ごとの stage / 参照環境変数 (CI_* は fetcher 側で除外済み)
// を表示する。stage プルダウン + 環境変数テキストの 2 条件で AND 絞り込みできる。

import { loadTemplates } from '../store.js';
import { html, escapeHtml, raw } from '../render.js';

// stage 名 → バッジ配色クラス (style.css の .st-*)。未知 stage は .st-other。
const STAGE_CLASS = {
  build: 'st-build', test: 'st-test', deploy: 'st-deploy',
  release: 'st-release', documentation: 'st-other', review: 'st-other',
};
function stageClass(s) { return STAGE_CLASS[s] || 'st-other'; }

// --- 純関数 (テスト対象) -------------------------------------------------

// テンプレ 1 件が stage / var クエリ両方に一致するか (空クエリは無条件一致 = AND)。
export function templateMatches(t, stageQ, varQ) {
  const s = (stageQ || '').trim().toLowerCase();
  const v = (varQ || '').trim().toLowerCase();
  const stageOk = !s || (t.stages || []).some(x => x.toLowerCase().includes(s));
  const varOk   = !v || (t.vars   || []).some(x => x.toLowerCase().includes(v));
  return stageOk && varOk;
}

// repo に一致テンプレが 1 件でもあるか。
export function repoHasMatch(repo, stageQ, varQ) {
  return (repo.templates || []).some(t => templateMatches(t, stageQ, varQ));
}

// repo + テンプレファイルから GitLab の blob URL を組む。url か path が無ければ null。
export function fileUrl(repo, t) {
  if (!repo || !repo.url || !t || !t.path) return null;
  const ref = repo.ref || 'HEAD';
  return `${repo.url}/-/blob/${ref}/${t.path}`;
}

// repo 全テンプレの参照変数を重複除去・ソートして返す。
export function collectVars(repo) {
  const set = new Set();
  for (const t of repo.templates || []) for (const v of t.vars || []) set.add(v);
  return [...set].sort();
}

// --- 描画 ----------------------------------------------------------------

export async function render() {
  const data = await loadTemplates().catch(() => null);
  const repos = (data && data.repos) || [];
  if (!repos.length) {
    return `
      <section>
        <p class="stub">templates.json がありません。<code>config.yml</code> に
        <code>ci_templates</code> を設定して fetcher を実行してください。</p>
      </section>`;
  }

  let active = repos[0].repo;
  let stageQ = '';
  let varQ = '';

  // stage プルダウンの選択肢: 全テンプレに出てくる stage を重複除去・ソート。
  const allStages = [...new Set(
    repos.flatMap(r => (r.templates || []).flatMap(t => t.stages || []))
  )].sort();
  const stageOptions = ['<option value="">(すべての stage)</option>']
    .concat(allStages.map(s => html`<option value="${s}">${s}</option>`))
    .join('');

  const shell = `
    <section class="tmpl">
      <p class="list-meta">fetched_at: ${escapeHtml((data && data.fetched_at) || '')} / ${repos.length} repo(s)</p>
      <div class="filter-form tmpl-search">
        <label>stage <select id="tmpl-stage">${stageOptions}</select></label>
        <label>環境変数 <input type="text" id="tmpl-var" placeholder="例: TOKEN" autocomplete="off"></label>
        <span class="filter-count" id="tmpl-count"></span>
      </div>
      <div class="tmpl-layout">
        <nav class="tmpl-repolist" id="tmpl-repolist"></nav>
        <div class="tmpl-main" id="tmpl-main"></div>
      </div>
    </section>`;

  const findRepo = (path) => repos.find(r => r.repo === path) || repos[0];
  const anyFilter = () => stageQ.trim() !== '' || varQ.trim() !== '';

  function renderList() {
    const shown = repos.filter(r => repoHasMatch(r, stageQ, varQ));
    if (anyFilter() && shown.length && !shown.some(r => r.repo === active)) active = shown[0].repo;

    document.getElementById('tmpl-repolist').innerHTML = shown.map(r => {
      const on = r.repo === active;
      return html`
        <button class="tmpl-repoitem${on ? ' active' : ''}" data-repo="${r.repo}">
          <span class="tmpl-reponame">${r.repo}</span>
          <span class="tmpl-repometa">${(r.templates || []).length}</span>
        </button>`;
    }).join('') || `<p class="stub">該当なし</p>`;
    document.getElementById('tmpl-count').textContent =
      anyFilter() ? `${shown.length}/${repos.length} repo` : '';
  }

  function renderMain() {
    const r = findRepo(active);
    const sLc = stageQ.trim().toLowerCase();
    const vLc = varQ.trim().toLowerCase();

    const head = html`
      <div class="tmpl-repohead">
        <h3 class="tmpl-repotitle">${r.repo}</h3>
        <a href="${r.url}" rel="noopener noreferrer" target="_blank">GitLab</a>
      </div>`;

    const readme = r.readme
      ? `<details class="tmpl-readme">
           <summary>README${r.readme_path ? html` <code>${r.readme_path}</code>` : ''}</summary>
           <pre>${escapeHtml(r.readme)}</pre>
         </details>`
      : `<p class="meta">README なし</p>`;

    // フィルタ時は一致テンプレのみ表示。
    const tpls = (r.templates || []).filter(t => !anyFilter() || templateMatches(t, stageQ, varQ));

    const cards = tpls.map(t => {
      const stages = (t.stages || []).map(s => {
        const hit = sLc && s.toLowerCase().includes(sLc) ? ' hit' : '';
        return html`<span class="tmpl-stage ${raw(stageClass(s))}${raw(hit)}">${s}</span>`;
      }).join('');
      const vars = (t.vars || []).length
        ? (t.vars).map(v => {
            const hit = vLc && v.toLowerCase().includes(vLc) ? ' hit' : '';
            return html`<code class="tmpl-var${raw(hit)}">${v}</code>`;
          }).join('')
        : `<span class="meta">(参照変数なし)</span>`;
      const url = fileUrl(r, t);
      const link = url
        ? html`<a class="tmpl-filelink" href="${url}" rel="noopener noreferrer" target="_blank">GitLab ↗</a>`
        : '';
      return html`
        <div class="tmpl-card">
          <div class="tmpl-card-head">
            <code class="tmpl-file">${t.name}</code>
            ${raw(link)}
            <span class="tmpl-stages">${raw(stages)}</span>
          </div>
          <div class="tmpl-vars-label">参照環境変数 <span class="meta">(CI_* 除外)</span></div>
          <div class="tmpl-vars">${raw(vars)}</div>
        </div>`;
    }).join('');

    document.getElementById('tmpl-main').innerHTML =
      head + readme + (cards || `<p class="stub">条件に合うテンプレートなし</p>`);
  }

  function wire() {
    const sIn = document.getElementById('tmpl-stage');
    const vIn = document.getElementById('tmpl-var');
    const upd = () => { stageQ = sIn.value; varQ = vIn.value; renderList(); renderMain(); };
    sIn.addEventListener('change', upd);  // stage は select
    vIn.addEventListener('input', upd);
    document.getElementById('tmpl-repolist').addEventListener('click', (ev) => {
      const btn = ev.target.closest('.tmpl-repoitem');
      if (!btn) return;
      active = btn.dataset.repo;
      renderList();
      renderMain();
    });
  }

  return { html: shell, after: () => { renderList(); renderMain(); wire(); } };
}
