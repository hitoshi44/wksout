import { grep, highlightPositions, highlightPositionsMulti, parseTerms } from '../js/grep.js';
import { suite, test, assertEq, assertTrue } from './runner.js';

suite('grep');

const ci = {
  kind: 'ci',
  entries: [
    { repo_id: 1, path: '.gitlab-ci.yml', raw: "image: node:18\nstages:\n  - build\n" },
    { repo_id: 2, path: '.gitlab-ci.yml', raw: "image: alpine\nscript:\n  - echo node\n" },
  ],
};

test('returns empty when query is empty', () => {
  assertEq(grep([ci], ''), []);
});

test('case insensitive by default', () => {
  const hits = grep([ci], 'NODE');
  assertEq(hits.length, 2);
});

test('case sensitive when requested', () => {
  const hits = grep([ci], 'NODE', { caseSensitive: true });
  assertEq(hits.length, 0);
});

test('records line_no, line, kind', () => {
  const hits = grep([ci], 'node:18');
  assertEq(hits.length, 1);
  assertEq(hits[0].repo_id, 1);
  assertEq(hits[0].line_no, 1);
  assertEq(hits[0].kind, 'ci');
  assertTrue(hits[0].line.includes('node:18'));
});

test('multiple matches across files & sources', () => {
  const pkg = {
    kind: 'pkg',
    entries: [
      { repo_id: 3, path: 'package.json', raw: '"node": ">=18"' },
    ],
  };
  const hits = grep([ci, pkg], 'node');
  // ci entry1: line1, ci entry2: line3, pkg: line1 → 3 hits
  assertEq(hits.length, 3);
});

test('maxPerFile limits hits per entry', () => {
  const many = {
    kind: 'ci',
    entries: [{ repo_id: 1, path: 'x', raw: 'a\na\na\na\na\n' }],
  };
  const hits = grep([many], 'a', { maxPerFile: 3 });
  assertEq(hits.length, 3);
});

suite('parseTerms (AND/OR)');

test('single term', () => {
  assertEq(parseTerms('react'), [['react']]);
});

test('space = AND', () => {
  assertEq(parseTerms('react webpack'), [['react', 'webpack']]);
});

test('uppercase AND keyword', () => {
  assertEq(parseTerms('react AND webpack'), [['react', 'webpack']]);
});

test('pipe = OR', () => {
  assertEq(parseTerms('react | vue'), [['react'], ['vue']]);
  assertEq(parseTerms('react|vue'), [['react'], ['vue']]);
});

test('uppercase OR keyword', () => {
  assertEq(parseTerms('react OR vue'), [['react'], ['vue']]);
});

test('AND binds tighter than OR (DNF)', () => {
  assertEq(parseTerms('a b | c d'), [['a', 'b'], ['c', 'd']]);
});

test('lowercase and/or are plain terms', () => {
  assertEq(parseTerms('foo and bar'), [['foo', 'and', 'bar']]);
});

test('empty / operator-only → []', () => {
  assertEq(parseTerms(''), []);
  assertEq(parseTerms('  |  '), []);
});

test('backslash escapes space and pipe', () => {
  assertEq(parseTerms('foo\\ bar'), [['foo bar']]);
  assertEq(parseTerms('a\\|b'), [['a|b']]);
});

test('escaped AND/OR stays a literal term', () => {
  assertEq(parseTerms('\\AND'), [['AND']]);
});

test('double-quote is a plain searchable character', () => {
  // " は構文ではないので、そのまま語の一部になる
  assertEq(parseTerms('"react"'), [['"react"']]);
  assertEq(parseTerms('"foo bar"'), [['"foo', 'bar"']]);
});

suite('grep AND/OR');

const two = {
  kind: 'pkg',
  entries: [
    // repo 10: has react + webpack on different lines (file-level AND should match)
    { repo_id: 10, path: 'package.json', raw: '"react": "^18"\n"webpack": "^5"\n' },
    // repo 11: only react
    { repo_id: 11, path: 'package.json', raw: '"react": "^17"\n"eslint": "^8"\n' },
    // repo 12: only vue
    { repo_id: 12, path: 'package.json', raw: '"vue": "^3"\n' },
  ],
};

test('AND matches file when both terms appear on different lines', () => {
  const hits = grep([two], 'react webpack');
  const repos = new Set(hits.map(h => h.repo_id));
  assertTrue(repos.has(10), 'repo10 has both');
  assertTrue(!repos.has(11), 'repo11 lacks webpack');
  // both relevant lines from repo10 surface
  assertEq(hits.filter(h => h.repo_id === 10).length, 2);
});

test('AND term that misses → repo excluded entirely', () => {
  const hits = grep([two], 'react vue');
  assertEq(hits.length, 0);
});

test('OR unions matches across repos', () => {
  const hits = grep([two], 'react | vue');
  const repos = new Set(hits.map(h => h.repo_id));
  assertEq([...repos].sort(), [10, 11, 12]);
});

test('hit carries the terms present on that line', () => {
  const hits = grep([two], 'react webpack');
  const reactLine = hits.find(h => h.line.includes('react'));
  assertEq(reactLine.terms, ['react']);
});

suite('highlightPositionsMulti');

test('merges positions across multiple terms', () => {
  // "react webpack" → highlight both
  assertEq(highlightPositionsMulti('use react and webpack', ['react', 'webpack']),
    [[4, 9], [14, 21]]);
});

test('overlapping ranges are merged', () => {
  assertEq(highlightPositionsMulti('abcd', ['abc', 'bcd']), [[0, 4]]);
});

suite('highlightPositions');

test('finds all positions', () => {
  assertEq(highlightPositions('abc abc abc', 'abc'), [[0,3], [4,7], [8,11]]);
});

test('case insensitive', () => {
  assertEq(highlightPositions('ABC abc', 'abc'), [[0,3], [4,7]]);
});

test('empty query returns []', () => {
  assertEq(highlightPositions('abc', ''), []);
});
