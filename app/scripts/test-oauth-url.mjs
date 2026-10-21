// node scripts/test-oauth-url.mjs（node 22.18 起直接剥类型跑 .ts）
// 核对连接器授权的两个网址：src/api/oauthUrl.ts 从授权页跳回来的地址里取 state / code / error / iss，从授权网址里取 state。不加新依赖。
import assert from 'node:assert/strict';
import { parseOAuthCallback, stateOf } from '../src/api/oauthUrl.ts';

let passed = 0;
function ok(name, fn) {
  fn();
  passed++;
  console.log(`ok  ${name}`);
}

console.log('parseOAuthCallback');
ok('取出 code 和 state', () => {
  assert.deepEqual(parseOAuthCallback('grava://oauth/callback?code=abc&state=xyz'),
    { state: 'xyz', code: 'abc', error: undefined, error_description: undefined, iss: undefined });
});
ok('iss 解码后一起交', () => {
  assert.equal(parseOAuthCallback('openmousse://oauth/callback?code=c&state=s&iss=https%3A%2F%2Fauth.example.com')?.iss, 'https://auth.example.com');
});
ok('报错的回调：error 和说明（+ 是空格）', () => {
  const r = parseOAuthCallback('openmousse://oauth/callback?error=access_denied&error_description=User+denied+it&state=s1');
  assert.equal(r?.error, 'access_denied');
  assert.equal(r?.error_description, 'User denied it');
  assert.equal(r?.code, undefined);
});
ok('结尾带斜杠、带 # 也认', () => {
  assert.equal(parseOAuthCallback('grava://oauth/callback/?code=1&state=2#x')?.state, '2');
});
ok('没带 state：不给备用的就不认', () => {
  assert.equal(parseOAuthCallback('grava://oauth/callback?error=invalid_client'), null);
});
ok('没带 state：应用内登录给了这次的 state 就用它', () => {
  const r = parseOAuthCallback('grava://oauth/callback?error=invalid_client', 'mine');
  assert.equal(r?.state, 'mine');
  assert.equal(r?.error, 'invalid_client');
});
ok('自己带了 state 的不被备用的盖掉', () => {
  assert.equal(parseOAuthCallback('grava://oauth/callback?code=1&state=theirs', 'mine')?.state, 'theirs');
});
ok('别的地址不认', () => {
  assert.equal(parseOAuthCallback('grava://today'), null);
  assert.equal(parseOAuthCallback('https://example.com/oauth/callback?code=1&state=2'), null);
  assert.equal(parseOAuthCallback('grava://oauth/callbackx?code=1&state=2'), null);
  assert.equal(parseOAuthCallback(''), null);
});
ok('坏掉的百分号编码不抛错，原样留着', () => {
  assert.equal(parseOAuthCallback('grava://oauth/callback?code=%E0%A4%A&state=s')?.code, '%E0%A4%A');
});

console.log('stateOf');
ok('授权网址里的 state（解码）', () => {
  assert.equal(stateOf('https://auth.example.com/authorize?response_type=code&client_id=c&state=abc%2Fdef&code_challenge=x'), 'abc/def');
});
ok('没有 state、没有查询串、# 后面的不算', () => {
  assert.equal(stateOf('https://auth.example.com/authorize?client_id=c'), null);
  assert.equal(stateOf('https://auth.example.com/authorize'), null);
  assert.equal(stateOf('https://auth.example.com/authorize?client_id=c#state=no'), null);
  assert.equal(stateOf(''), null);
});

console.log(`\n${passed} passed`);
