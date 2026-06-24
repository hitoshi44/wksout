// 小さな DOM ヘルパ。
//
// 主目的: テンプレートリテラルで HTML 組むときの XSS 対策と、
// よく使う「要素生成 + 子追加」を 1 行で書けるようにする。

export function escapeHtml(s) {
  if (s == null) return '';
  return String(s)
    .replaceAll('&', '&amp;')
    .replaceAll('<', '&lt;')
    .replaceAll('>', '&gt;')
    .replaceAll('"', '&quot;')
    .replaceAll("'", '&#39;');
}

// タグ付きテンプレートリテラル: html`<a href="${url}">${name}</a>` → 自動 escape。
// 値が { raw: ... } の形ならエスケープしない (生 HTML を埋め込みたいケース)。
export function html(strings, ...values) {
  let out = '';
  for (let i = 0; i < strings.length; i++) {
    out += strings[i];
    if (i < values.length) {
      const v = values[i];
      if (v && typeof v === 'object' && 'raw' in v) out += v.raw;
      else if (Array.isArray(v)) out += v.join('');
      else out += escapeHtml(v);
    }
  }
  return out;
}

export const raw = (s) => ({ raw: s });

export function el(tag, attrs = {}, children = []) {
  const e = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === 'class') e.className = v;
    else if (k.startsWith('on') && typeof v === 'function') {
      e.addEventListener(k.slice(2).toLowerCase(), v);
    } else {
      e.setAttribute(k, v);
    }
  }
  for (const c of [].concat(children)) {
    if (c == null) continue;
    if (typeof c === 'string') e.appendChild(document.createTextNode(c));
    else e.appendChild(c);
  }
  return e;
}
