import test from 'node:test';
import assert from 'node:assert/strict';
import {ownerAuthorizationFailure} from '../lib/authorization.ts';

const request=(identity)=>new Request('https://example.test/api/workspace',{
  headers:identity?{'oai-authenticated-user-id':identity}:{},
});
const configured={WORKSPACE_AUTH_MODE:'trusted-sites',WORKSPACE_OWNER_ID:'synthetic-owner'};

test('unconfigured instances reject access even with a supplied identity header',()=>{
  for(const config of [{},{WORKSPACE_OWNER_ID:'synthetic-owner'},
    {WORKSPACE_AUTH_MODE:'trusted-sites'}, {...configured,WORKSPACE_OWNER_ID:'   '},
    {...configured,WORKSPACE_AUTH_MODE:'public'}]) {
    assert.equal(ownerAuthorizationFailure(request('synthetic-owner'),config).status,503);
  }
});
test('trusted-gateway instances require the exact configured owner',()=>{
  for(const identity of [undefined,'another-synthetic-owner']) {
    assert.equal(ownerAuthorizationFailure(request(identity),configured).status,401);
  }
  assert.equal(ownerAuthorizationFailure(request('synthetic-owner'),configured),null);
});
test('cross-site requests remain blocked for the configured owner',()=>{
  const cross=request('synthetic-owner');cross.headers.set('sec-fetch-site','cross-site');
  const denied=ownerAuthorizationFailure(cross,configured);
  assert.equal(denied.status,403);assert.equal(denied.headers.get('cache-control'),'private, no-store');
});
