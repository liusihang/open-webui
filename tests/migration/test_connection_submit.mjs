// Execute the actual modal submit handler, including validation and onSubmit.
// No browser, app startup, credentials or external service is involved.
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { test } from 'node:test';
import vm from 'node:vm';

const source = readFileSync(new URL('../../src/lib/components/AddConnectionModal.svelte', import.meta.url), 'utf8');
const submit = source.split('const submitHandler = async () => {')[1].split('\n\tconst init =')[0];
assert.ok(submit, 'real submit handler must be found');

async function save(connection = null, changes = {}) {
  let result;
  const config = connection?.config ?? {};
  const context = {
    connection, loading: false, ollama: false, direct: false, azure: false,
    url: connection?.url ?? 'https://example.invalid/v1', key: connection?.key ?? 'synthetic',
    enable: config.enable ?? true, tags: config.tags ?? [], prefixId: config.prefix_id ?? '',
    modelIds: config.model_ids ?? [], connectionType: config.connection_type ?? 'external',
    auth_type: config.auth_type ?? 'bearer', forwardCookies: config.forward_cookies ?? false,
    headers: config.headers ? JSON.stringify(config.headers) : '', passthroughParams: '',
    provider: config.provider ?? '', apiVersion: config.api_version ?? '', apiType: config.api_type ?? '',
    showAdvanced: false, show: true, toast: { error: (e) => assert.fail(e) },
    $i18n: { t: (value) => value }, parsePassthroughParams: (value) => value.split(',').map(v => v.trim()).filter(Boolean),
    onSubmit: async (value) => { result = value; }, ...changes
  };
  await vm.runInNewContext(`(async () => {${submit.slice(0, submit.lastIndexOf('};'))}})()`, context);
  // Normalize cross-realm prototypes but retain undefined values/types.
  return structuredClone(result);
}

const prior = {
  url: 'https://example.invalid/v1', key: 'synthetic', permission: { user_ids: ['owner'] },
  config: { enable: true, api_type: 'responses', model_ids: ['gpt-6-astra'],
    native_compaction_models: ['gpt-6-astra', 'gpt-6.1-sol', 'gpt-6-luna'], native_compaction_protocol: 'codex_v2',
    custom: { flag: false, count: 0, nullable: null, list: [] } }
};

test('unrelated edit keeps native protocol, exact allowlist, permissions and non-UI values', async () => {
  const snapshot = structuredClone(prior);
  const result = await save(prior, { tags: ['edited'] });
  assert.deepEqual(result.config.native_compaction_models, prior.config.native_compaction_models);
  assert.equal(result.config.native_compaction_protocol, 'codex_v2');
  assert.deepEqual(result.config.custom, prior.config.custom);
  assert.deepEqual(result.permission, prior.permission);
  assert.equal(result.url, prior.url);
  assert.equal(result.key, prior.key);
  assert.deepEqual(result.config.tags, ['edited']);
  assert.deepEqual(prior, snapshot, 'saving does not mutate original connection');
});

test('explicitly clearing UI values does not restore old optional fields', async () => {
  const old = structuredClone(prior);
  Object.assign(old.config, { provider: 'azure', azure: true, api_version: 'old', headers: { Example: 'old' }, passthrough_params: ['old'] });
  const result = await save(old, { provider: '', azure: false, apiVersion: '', apiType: '', headers: '', passthroughParams: '', modelIds: [], prefixId: '' });
  for (const key of ['provider', 'azure', 'api_version', 'api_type', 'headers']) assert.equal(result.config[key], undefined, key);
  assert.deepEqual(result.config.passthrough_params, []);
  assert.deepEqual(result.config.model_ids, []);
  assert.equal(result.config.native_compaction_protocol, 'codex_v2');
});

test('new connection uses defaults and never invents native capability or permissions', async () => {
  const result = await save();
  assert.equal(result.config.enable, true);
  assert.equal(result.config.auth_type, 'bearer');
  for (const key of ['native_compaction_models', 'native_compaction_protocol']) assert.ok(!(key in result.config));
  assert.ok(!('permission' in result));
});

test('disable keeps capability fields and explicit empty/nonstandard stored values unchanged', async () => {
  for (const value of [[], null, false, 'stored-value']) {
    const old = structuredClone(prior);
    old.config.native_compaction_models = value;
    old.config.native_compaction_protocol = null;
    const result = await save(old, { enable: false });
    assert.equal(result.config.enable, false);
    assert.deepEqual(result.config.native_compaction_models, value);
    assert.equal(result.config.native_compaction_protocol, null);
  }
});
