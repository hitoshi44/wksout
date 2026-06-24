import { parseQuery, buildQuery } from '../js/router.js';
import { suite, test, assertEq } from './runner.js';

suite('router queries');

test('parseQuery handles basic kv', () => {
  assertEq(parseQuery('a=1&b=hello'), { a: '1', b: 'hello' });
});

test('parseQuery handles encoded values', () => {
  assertEq(parseQuery('q=hello%20world&kinds=ci%2Cpkg'),
    { q: 'hello world', kinds: 'ci,pkg' });
});

test('parseQuery on empty', () => {
  assertEq(parseQuery(''), {});
});

test('buildQuery skips empty / null / undefined', () => {
  assertEq(buildQuery({ a: '1', b: '', c: null, d: undefined, e: '0' }),
    'a=1&e=0');
});

test('buildQuery encodes', () => {
  assertEq(buildQuery({ q: 'a b', kinds: 'ci,pkg' }),
    'q=a%20b&kinds=ci%2Cpkg');
});

test('round trip', () => {
  const obj = { q: 'foo bar', mode: 'grep', kinds: 'ci,pkg' };
  assertEq(parseQuery(buildQuery(obj)), obj);
});
