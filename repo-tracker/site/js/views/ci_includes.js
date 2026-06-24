import { loadAndBuildRows } from '../ci_includes.js';
import { html } from '../render.js';

export async function render() {
  const rows = await loadAndBuildRows();
  if (rows.length === 0) {
    return html`
      <section>
        <p>該当 include なし</p>
      </section>
    `;
  }

  const tbody = rows.map(r => html`
    <tr data-repo-path="${r.repo_path}" data-ref="${r.ref}">
      <td><a href="#/repo/${r.repo_id}">${r.repo_path}</a></td>
      <td>${r.ref}</td>
    </tr>
  `).join('');

  const total = rows.length;
  const html_ = `
    <section>
      <form id="ci-filter" class="filter-form" onsubmit="return false;">
        <input type="text" name="repo" placeholder="repository フィルタ" autofocus>
        <input type="text" name="ref" placeholder="参照先 フィルタ">
        <span class="filter-count"><span id="ci-filter-count">${total}</span> / ${total}</span>
      </form>
      <table id="ci-table">
        <thead><tr><th>repository</th><th>参照先</th></tr></thead>
        <tbody>${tbody}</tbody>
      </table>
    </section>
  `;
  return { html: html_, after: () => bindFilter(total) };
}

// 純関数: 1 行が表示対象かを判定 (テスト対象)
// repoQuery は repo_path のみ、refQuery は ref のみに対する部分一致 (大小無視)
export function matches(row, repoQuery, refQuery) {
  if (repoQuery && !(row.repo_path || '').toLowerCase().includes(repoQuery.toLowerCase())) return false;
  if (refQuery && !(row.ref || '').toLowerCase().includes(refQuery.toLowerCase())) return false;
  return true;
}

function bindFilter(total) {
  const form = document.getElementById('ci-filter');
  const tbody = document.querySelector('#ci-table tbody');
  if (!form || !tbody) return;
  const trs = Array.from(tbody.querySelectorAll('tr'));
  const countEl = document.getElementById('ci-filter-count');

  function apply() {
    const repoQ = form.querySelector('input[name=repo]').value;
    const refQ = form.querySelector('input[name=ref]').value;
    let visible = 0;
    for (const tr of trs) {
      const row = {
        repo_path: tr.dataset.repoPath,
        ref: tr.dataset.ref,
      };
      const show = matches(row, repoQ, refQ);
      tr.style.display = show ? '' : 'none';
      if (show) visible++;
    }
    countEl.textContent = String(visible);
  }

  form.addEventListener('input', apply);
  apply();
}
