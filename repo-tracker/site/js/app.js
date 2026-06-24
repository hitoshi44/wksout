import { route, setNotFound, start } from './router.js';
import * as reposView    from './views/repos.js';
import * as searchView   from './views/search.js';
import * as repoDetail   from './views/repo_detail.js';
import * as fileDetail   from './views/file_detail.js';
import * as ciIncludes   from './views/ci_includes.js';
import * as templatesView from './views/templates.js';

route('#/',               () => withErr(reposView.render()));
route('#/search',         (_p, q) => withErr(searchView.render(_p, q)));
route('#/ci-includes',    () => withErr(ciIncludes.render()));
route('#/templates',      () => withErr(templatesView.render()));
route('#/repo/:id',       (p) => withErr(repoDetail.render(p)));
route('#/file/:id/:path', (p) => withErr(fileDetail.render(p)));

setNotFound((path) => `<p>not found: ${escapeText(path)}</p>`);

window.addEventListener('hashchange', updateChrome);
updateChrome();
start(document.getElementById('app'));

// ナビの active クラスと document.title を hash に応じて切り替える。
// 詳細ページ (repo_detail / file_detail) は view 側で document.title を上書きする。
function updateChrome() {
  const hash = (location.hash || '#/').split('?')[0];
  const section =
    hash.startsWith('#/search')       ? 'search' :
    hash.startsWith('#/ci-includes')  ? 'ci-includes' :
    hash.startsWith('#/templates')    ? 'templates' :
    /* #/ , #/repo/.. , #/file/.. */    'repos';

  for (const a of document.querySelectorAll('header nav a')) {
    a.classList.toggle('active', a.dataset.section === section);
  }

  const titles = {
    'repos': 'リポジトリ一覧',
    'search': '横断検索',
    'ci-includes': 'CI 呼び出し',
    'templates': 'CI テンプレート',
  };
  document.title = `${titles[section]} — Repo Tracker`;
}

function escapeText(s) {
  return String(s).replaceAll('<', '&lt;').replaceAll('>', '&gt;');
}

async function withErr(p) {
  try {
    return await p;
  } catch (e) {
    console.error(e);
    return `<p>エラー: ${escapeText(e.message || String(e))}</p>`;
  }
}
