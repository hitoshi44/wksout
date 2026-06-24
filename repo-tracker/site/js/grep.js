// 純関数: raw バンドル群を文字列クエリで grep する。
//
// 戻り値はマッチ単位の配列。1 ファイル内に複数行ヒットすれば複数エントリ。
// CI / pkg / pom など複数ソースを受け取れるよう、入力は { kind, entries } の配列。
//
// クエリは AND/OR をサポートする (インライン構文):
//   - スペース区切り or 大文字 AND = かつ (すべて含む)
//   - | or 大文字 OR              = または (いずれか含む)
//   - AND が OR より強く結合する → "a b | c d" = "(a かつ b) または (c かつ d)"
// AND の判定は「ファイル単位」: 1 ファイル内のどこかに全語があればそのファイルがヒット。
// (演算子は大文字 AND / OR と | のみ。小文字 and/or は普通の検索語として扱う。)
//
// 演算子を含む文字列をそのまま検索したいときは、バックスラッシュでエスケープする:
//   foo\ bar → スペース込みの 1 語 / a\|b → リテラル | / \\ → バックスラッシュ自身
// エスケープされた語は AND/OR キーワード判定の対象外 (= 常にリテラル)。
// (" は特別扱いせず、普通の検索文字として扱う。)

// クエリをトークン列に分解する。
// 返り値は { t:'term'|'and'|'or', v? } の配列。
function tokenize(query) {
  const out = [];
  const n = query.length;
  let i = 0;
  while (i < n) {
    const c = query[i];
    if (c === ' ' || c === '\t' || c === '\n' || c === '\r') { i++; continue; }
    if (c === '|') { out.push({ t: 'or' }); i++; continue; }
    // 素の語: 空白 / | まで。\x はエスケープして 1 文字取り込む。
    let s = '';
    let literal = false;
    while (i < n) {
      const ch = query[i];
      if (ch === '\\' && i + 1 < n) { s += query[i + 1]; i += 2; literal = true; continue; }
      if (ch === ' ' || ch === '\t' || ch === '\n' || ch === '\r' || ch === '|') break;
      s += ch; i++;
    }
    if (!literal && s === 'AND') out.push({ t: 'and' });
    else if (!literal && s === 'OR') out.push({ t: 'or' });
    else if (s) out.push({ t: 'term', v: s });
  }
  return out;
}

// クエリを DNF (論理和標準形) にパースする。
// 返り値は OR グループの配列で、各グループは AND 語の配列。
// 例: "a b | c"      → [["a","b"], ["c"]]
//     "react"        → [["react"]]
//     "foo\\ bar"    → [["foo bar"]]
//     ""             → []
export function parseTerms(query) {
  if (!query) return [];
  const groups = [];
  let cur = [];
  for (const tk of tokenize(query)) {
    if (tk.t === 'or') {
      if (cur.length) { groups.push(cur); cur = []; }
    } else if (tk.t === 'term') {
      cur.push(tk.v);
    }
    // 'and' は区切りだけ (隣接 = AND なので何もしない)
  }
  if (cur.length) groups.push(cur);
  return groups;
}

export function grep(sources, query, opts = {}) {
  const { caseSensitive = false, maxPerFile = 50 } = opts;
  const groups = parseTerms(query);
  if (groups.length === 0) return [];

  const norm = s => (caseSensitive ? s : s.toLowerCase());
  // 各語を正規化済みの { raw, n } に。raw はハイライト表示用、n は照合用。
  const ngroups = groups.map(g => g.map(t => ({ raw: t, n: norm(t) })));

  const results = [];

  for (const src of sources) {
    for (const entry of src.entries) {
      const hay = norm(entry.raw);

      // ファイル単位 AND: いずれかの OR グループが「全語そろっている」なら採用。
      // 採用されたグループの語だけを relevant に集める (ハイライト & 行抽出の対象)。
      const relevant = new Map(); // n -> raw (重複排除)
      for (const g of ngroups) {
        if (g.every(t => hay.indexOf(t.n) !== -1)) {
          for (const t of g) relevant.set(t.n, t.raw);
        }
      }
      if (relevant.size === 0) continue;

      const relTerms = [...relevant.entries()].map(([n, raw]) => ({ n, raw }));
      const lines = entry.raw.split('\n');
      let hits = 0;
      for (let i = 0; i < lines.length && hits < maxPerFile; i++) {
        const ln = norm(lines[i]);
        const present = relTerms.filter(t => ln.indexOf(t.n) !== -1).map(t => t.raw);
        if (present.length === 0) continue;
        results.push({
          kind: src.kind,
          repo_id: entry.repo_id,
          path: entry.path,
          line_no: i + 1,
          line: lines[i],
          terms: present, // この行に実際に出現した relevant 語
        });
        hits++;
      }
    }
  }
  return results;
}

// 1 行内に複数ヒットがあっても 1 件だけ扱う簡易版なので、
// 「ヒット箇所をハイライト」する側で同じ needle を再走査して mark を付ける。
// この関数はその位置探索を返す (単一語版)。
export function highlightPositions(line, query, caseSensitive = false) {
  if (!query) return [];
  const hay = caseSensitive ? line : line.toLowerCase();
  const needle = caseSensitive ? query : query.toLowerCase();
  const out = [];
  let from = 0;
  while (true) {
    const i = hay.indexOf(needle, from);
    if (i === -1) break;
    out.push([i, i + query.length]);
    from = i + Math.max(1, query.length);
  }
  return out;
}

// 複数語版: 各語のヒット位置を集めて開始位置でソート、重なりはマージして返す。
// AND/OR 検索で 1 行に複数の語が当たるケースに対応する。
export function highlightPositionsMulti(line, terms, caseSensitive = false) {
  const all = [];
  for (const t of terms || []) {
    for (const pos of highlightPositions(line, t, caseSensitive)) all.push(pos);
  }
  all.sort((a, b) => a[0] - b[0] || a[1] - b[1]);
  const merged = [];
  for (const [s, e] of all) {
    const last = merged[merged.length - 1];
    if (last && s <= last[1]) last[1] = Math.max(last[1], e);
    else merged.push([s, e]);
  }
  return merged;
}
