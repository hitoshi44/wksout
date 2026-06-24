import { countByKind } from '../js/views/repos.js';
import { suite, test, assertEq } from './runner.js';

suite('countByKind');

test('counts each kind', () => {
  const files = [
    { type: 'gitlab-ci',    path: '.gitlab-ci.yml' },
    { type: 'package-json', path: 'package.json' },
    { type: 'package-json', path: 'frontend/package.json' },
    { type: 'pom-xml',      path: 'pom.xml' },
    { type: 'dockerfile',   path: 'Dockerfile' },
  ];
  assertEq(countByKind(files), { ci: 1, pkg: 2, pom: 1, docker: 1 });
});

test('handles empty', () => {
  assertEq(countByKind([]), { ci: 0, pkg: 0, pom: 0, docker: 0 });
});

test('ignores unknown', () => {
  assertEq(countByKind([{ type: 'mystery', path: 'x' }]),
    { ci: 0, pkg: 0, pom: 0, docker: 0 });
});
