import { loadRepos } from '../store.js';
import { html, escapeHtml, raw } from '../render.js';

export async function render({ id }) {
  const data = await loadRepos();
  const repo = (data.repos || []).find(r => String(r.id) === String(id));
  if (!repo) return `<p>repo ${escapeHtml(id)} not found</p>`;

  document.title = `${repo.path} — Repo Tracker`;

  const files = repo.files || [];
  const rows = files.map(f => html`
    <tr>
      <td>${f.type}</td>
      <td class="path"><a href="#/file/${repo.id}/${encodeURIComponent(f.path)}">${f.path}</a></td>
    </tr>
  `).join('');

  return html`
    <section>
      <div class="crumbs"><a href="#/">← リポジトリ一覧</a></div>
      <h2 class="path">${repo.path}</h2>
      <p class="meta">
        <a href="${repo.url}" rel="noopener noreferrer" target="_blank">GitLab で開く</a> &middot;
        id ${repo.id} &middot; fetched_at ${repo.fetched_at}
      </p>
      <h3>追跡ファイル (${files.length})</h3>
      <table>
        <thead><tr><th>type</th><th>path</th></tr></thead>
        <tbody>${files.length ? raw(rows) : raw(`<tr><td colspan="2">(none)</td></tr>`)}</tbody>
      </table>
    </section>
  `;
}
