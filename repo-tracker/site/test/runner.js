// 最小のテストランナー。ブラウザだけで動く。
//
// test.html が各 *.test.js を import すると、それぞれが test('name', fn) で
// 登録 → このランナーが順に実行し、結果を <ul id="results"> に出す。

const cases = [];
let currentSuite = '';

export function suite(name) { currentSuite = name; }
export function test(name, fn) { cases.push({ suite: currentSuite, name, fn }); }

export function assertEq(actual, expected, msg = '') {
  const a = JSON.stringify(actual);
  const e = JSON.stringify(expected);
  if (a !== e) throw new Error(`${msg} expected ${e}, got ${a}`);
}

export function assertTrue(v, msg = '') {
  if (!v) throw new Error(msg || `expected truthy, got ${v}`);
}

export function assertIncludes(arr, needle, key, msg = '') {
  const hit = arr.some(x => (key ? x[key] : x) === needle);
  if (!hit) throw new Error(msg || `expected array to include ${needle}`);
}

export async function run(mount) {
  let pass = 0, fail = 0;
  const ul = document.createElement('ul');
  for (const c of cases) {
    const li = document.createElement('li');
    try {
      await c.fn();
      li.textContent = `PASS  [${c.suite}] ${c.name}`;
      li.style.color = 'green';
      pass++;
    } catch (e) {
      li.textContent = `FAIL  [${c.suite}] ${c.name} — ${e.message}`;
      li.style.color = 'red';
      fail++;
      console.error(c.name, e);
    }
    ul.appendChild(li);
  }
  const summary = document.createElement('p');
  summary.textContent = `${pass} passed, ${fail} failed`;
  summary.style.fontWeight = 'bold';
  summary.style.color = fail ? 'red' : 'green';
  mount.appendChild(summary);
  mount.appendChild(ul);
}
