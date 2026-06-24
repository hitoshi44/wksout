import {
  templateMatches, repoHasMatch, fileUrl, collectVars,
} from '../js/views/templates.js';
import { suite, test, assertEq, assertTrue } from './runner.js';

suite('templates view');

const repos = [
  {
    repo: 'group/ci-docker',
    name: 'ci-docker',
    url: 'https://gitlab.com/group/ci-docker',
    ref: 'main',
    templates: [
      { name: 'build.yml',   path: 'templates/build.yml',   stages: ['build'],  jobs: ['docker_build'], vars: ['DOCKER_BUILD_ARGS'] },
      { name: 'publish.yml', path: 'templates/publish.yml', stages: ['deploy'], jobs: ['npm_publish'],   vars: ['NPM_TOKEN'] },
    ],
  },
  {
    repo: 'group/ci-k8s',
    name: 'ci-k8s',
    url: 'https://gitlab.com/group/ci-k8s',
    ref: 'main',
    templates: [
      { name: 'deploy.yml', path: 'templates/deploy.yml', stages: ['deploy'], jobs: ['deploy'], vars: ['KUBE_CONTEXT', 'APPROVAL_TOKEN'] },
    ],
  },
];

const docker = repos[0];

test('templateMatches: empty queries match', () => {
  assertTrue(templateMatches(docker.templates[0], '', ''));
});

test('templateMatches: stage AND var together', () => {
  // deploy ステージ かつ TOKEN を含む変数 → publish.yml (deploy + NPM_TOKEN) が一致
  assertEq(templateMatches(docker.templates[1], 'deploy', 'token'), true);
  // build ステージ だが TOKEN 変数なし → 不一致
  assertEq(templateMatches(docker.templates[0], 'build', 'token'), false);
  // deploy ステージ だが publish の変数に kube は無い
  assertEq(templateMatches(docker.templates[1], 'deploy', 'kube'), false);
});

test('repoHasMatch: any template satisfies both', () => {
  assertEq(repoHasMatch(docker, 'deploy', 'token'), true);
  assertEq(repoHasMatch(docker, 'build', 'token'), false);
});

test('fileUrl builds GitLab blob URL, HEAD fallback, null without url', () => {
  assertEq(fileUrl(docker, docker.templates[0]),
    'https://gitlab.com/group/ci-docker/-/blob/main/templates/build.yml');
  assertEq(fileUrl({ url: 'https://x/y' }, { path: 'a.yml' }), 'https://x/y/-/blob/HEAD/a.yml');
  assertEq(fileUrl({}, { path: 'a.yml' }), null);
});

test('collectVars dedups and sorts', () => {
  assertEq(collectVars(docker), ['DOCKER_BUILD_ARGS', 'NPM_TOKEN']);
});
