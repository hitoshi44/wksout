import { loadRepos } from '../store.js';
import { html, escapeHtml } from '../render.js';

export async function render() {
  const data = await loadRepos();
  const repos = data.repos || [];
  const rows = repos.map(r => {
    const counts = countByKind(r.files || []);
    return html`
      <tr>
        <td class="path"><a href="#/repo/${r.id}">${r.path}</a></td>
        <td class="count">${counts.ci     || ''}</td>
        <td class="count">${counts.pkg    || ''}</td>
        <td class="count">${counts.pom    || ''}</td>
        <td class="count">${counts.docker || ''}</td>
        <td><a href="${r.url}" rel="noopener noreferrer" target="_blank">GitLab</a></td>
      </tr>`;
  }).join('');

  return `
    <section>
      <p class="list-meta">fetched_at: ${escapeHtml(data.fetched_at || '')} / ${repos.length} repo(s)</p>
      <table>
        <thead>
          <tr><th>repo</th><th class="count">ci</th><th class="count">pkg</th><th class="count">pom</th><th class="count">docker</th><th></th></tr>
        </thead>
        <tbody>${rows || `<tr><td colspan="6">(no repos)</td></tr>`}</tbody>
      </table>
    </section>
  `;
}

function countByKind(files) {
  const out = { ci: 0, pkg: 0, pom: 0, docker: 0 };
  for (const f of files) {
    if (f.type === 'gitlab-ci')         out.ci++;
    else if (f.type === 'package-json') out.pkg++;
    else if (f.type === 'pom-xml')      out.pom++;
    else if (f.type === 'dockerfile')   out.docker++;
  }
  return out;
}

// テスト用に export しておく
export { countByKind };
