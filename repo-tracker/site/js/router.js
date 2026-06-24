// 超薄いハッシュベースルータ。
//
// ルートは {pattern, handler} の配列。pattern は文字列 or 正規表現。
// 文字列パターンは ':name' でパラメータを抜く ('#/repo/:id' など)。
// handler(params, query) は描画する HTML 文字列 or DOM ノードを返すか、
// 自分で描画する場合は何も返さない (mount 先のクリアだけ済ませる)。

const routes = [];
let mountEl = null;
let notFoundHandler = null;

export function route(pattern, handler) {
  routes.push({ pattern, handler });
}

export function setNotFound(handler) {
  notFoundHandler = handler;
}

export function start(el) {
  mountEl = el;
  window.addEventListener('hashchange', dispatch);
  dispatch();
}

export function navigate(hash) {
  if (location.hash === hash) dispatch();
  else location.hash = hash;
}

function dispatch() {
  const hash = location.hash || '#/';
  const [pathPart, queryPart = ''] = hash.split('?');
  const query = parseQuery(queryPart);
  for (const { pattern, handler } of routes) {
    const params = match(pattern, pathPart);
    if (params) {
      render(handler(params, query));
      return;
    }
  }
  if (notFoundHandler) render(notFoundHandler(pathPart));
  else mountEl.textContent = 'not found: ' + pathPart;
}

function match(pattern, path) {
  const ps = pattern.split('/');
  const xs = path.split('/');
  if (ps.length !== xs.length) return null;
  const params = {};
  for (let i = 0; i < ps.length; i++) {
    if (ps[i].startsWith(':')) {
      params[ps[i].slice(1)] = decodeURIComponent(xs[i]);
    } else if (ps[i] !== xs[i]) {
      return null;
    }
  }
  return params;
}

export function parseQuery(s) {
  const out = {};
  if (!s) return out;
  for (const part of s.split('&')) {
    if (!part) continue;
    const [k, v = ''] = part.split('=');
    out[decodeURIComponent(k)] = decodeURIComponent(v);
  }
  return out;
}

export function buildQuery(obj) {
  const parts = [];
  for (const [k, v] of Object.entries(obj)) {
    if (v === undefined || v === null || v === '') continue;
    parts.push(encodeURIComponent(k) + '=' + encodeURIComponent(v));
  }
  return parts.join('&');
}

async function render(result) {
  const r = await Promise.resolve(result);
  if (r == null) return; // handler が自分で mount 済み
  if (typeof r === 'string') {
    mountEl.innerHTML = r;
    return;
  }
  // { html, after }: innerHTML 反映後に同期で after を呼ぶ。
  // queueMicrotask だと DOM 反映前に走ってフォームを掴み損ねるケースがあった。
  if (r && typeof r === 'object' && typeof r.html === 'string') {
    mountEl.innerHTML = r.html;
    if (typeof r.after === 'function') r.after();
    return;
  }
  // DOM ノード
  mountEl.innerHTML = '';
  mountEl.appendChild(r);
}
