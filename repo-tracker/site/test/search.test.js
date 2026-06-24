import { gitlabFileUrl } from '../js/views/search.js';
import { suite, test, assertEq } from './runner.js';

suite('search gitlabFileUrl');

test('builds GitLab blob URL at HEAD', () => {
  assertEq(gitlabFileUrl('https://gitlab.com/g/r', '.gitlab-ci.yml'),
    'https://gitlab.com/g/r/-/blob/HEAD/.gitlab-ci.yml');
  assertEq(gitlabFileUrl('https://gitlab.com/g/r', 'frontend/package.json'),
    'https://gitlab.com/g/r/-/blob/HEAD/frontend/package.json');
});

test('returns null when url or path missing', () => {
  assertEq(gitlabFileUrl('', 'a'), null);
  assertEq(gitlabFileUrl('https://x/y', ''), null);
});
