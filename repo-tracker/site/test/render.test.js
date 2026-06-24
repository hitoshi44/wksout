import { html, escapeHtml, raw } from '../js/render.js';
import { suite, test, assertEq } from './runner.js';

suite('escapeHtml');

test('escapes <, >, &, quotes', () => {
  assertEq(escapeHtml('<a href="x">&"\'</a>'),
    '&lt;a href=&quot;x&quot;&gt;&amp;&quot;&#39;&lt;/a&gt;');
});

test('null/undefined → empty', () => {
  assertEq(escapeHtml(null), '');
  assertEq(escapeHtml(undefined), '');
});

suite('html`` tag');

test('escapes interpolated values by default', () => {
  const name = '<script>';
  assertEq(html`hi ${name}`, 'hi &lt;script&gt;');
});

test('raw() bypasses escape', () => {
  assertEq(html`x ${raw('<b>y</b>')}`, 'x <b>y</b>');
});

test('arrays are joined', () => {
  const xs = ['a', 'b', 'c'];
  assertEq(html`${xs}`, 'abc');
});
