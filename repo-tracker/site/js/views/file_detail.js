import { loadFile, loadRepos } from '../store.js';
import { html, escapeHtml, raw } from '../render.js';

export async function render({ id, path }) {
  let file;
  try {
    file = await loadFile(id, path);
  } catch (e) {
    return `<p>load error: ${escapeHtml(e.message)}</p>`;
  }

  const repos = ((await loadRepos()).repos) || [];
  const repo = repos.find(r => String(r.id) === String(id));
  const repoLabel = repo ? repo.path : `repo ${id}`;

  document.title = `${path} — Repo Tracker`;

  return html`
    <section class="file-detail">
      <div class="crumbs">
        <a href="#/repo/${id}">← ${repoLabel}</a>
      </div>
      <h2 class="path">${path}</h2>
      <p class="meta">type: ${file.type} &middot; size: ${file.size} B &middot; blob_sha: <code>${file.blob_sha}</code></p>
      ${raw(renderCode(file.raw))}
    </section>
  `;
}

// 行番号付き code block。
// 構造: <pre class="code"><ol><li><span class="line">...</span></li>...</ol></pre>
// 行は CSS counter + ::before で番号を出す方が DOM が軽いので、行ごとに <span> を出す。
export function renderCode(rawStr) {
  const text = rawStr == null ? '' : String(rawStr);
  const lines = text.split('\n');
  // trailing newline で空行が増えるのを抑制
  if (lines.length > 0 && lines[lines.length - 1] === '') lines.pop();

  const inner = lines.map(l => `<span class="ln">${escapeHtml(l) || ' '}</span>`).join('\n');
  return `<pre class="code"><code>${inner}</code></pre>`;
}
