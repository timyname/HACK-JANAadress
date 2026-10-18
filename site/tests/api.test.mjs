import test from 'node:test';
import assert from 'node:assert/strict';
import { detectRuntime, parseViaRuntime } from '../src/api.mjs';
import { parseBatch } from '../src/parser.mjs';

test('runtime discovery only trusts Jana readiness', async () => {
  assert.equal((await detectRuntime(async () => ({ ok: true, json: async () => ({ status: 'ready' }) }))).mode, 'browser');
  assert.equal((await detectRuntime(async () => ({ ok: true, json: async () => ({ service: 'jana-address', status: 'ready' }) }))).mode, 'api');
});

test('static preview parses without sending addresses to a server', async () => {
  const input = [{ id: 'x', raw_address: 'тест' }];
  const response = await parseViaRuntime(input, { mode: 'browser' }, () => { throw new Error('unexpected request'); });
  assert.deepEqual(response.records.map(item => item.result), parseBatch(input));
});

test('API mode preserves input order and uses same-origin explanation route', async () => {
  const input = [{ id: 'same', raw_address: 'one' }, { id: 'same', raw_address: 'two' }];
  const records = parseBatch(input).map(result => ({ result, fields: {}, warnings: [] }));
  const response = await parseViaRuntime(input, { mode: 'api' }, async (url, options) => {
    assert.equal(url, '/internal/parse');
    assert.equal(options.credentials, 'same-origin');
    assert.deepEqual(JSON.parse(options.body), input);
    return { ok: true, json: async () => ({ records }) };
  });
  assert.deepEqual(response.records, records);
});

test('API failure and bad identity cannot silently become browser results', async () => {
  const input = [{ id: 'x', raw_address: 'original' }];
  await assert.rejects(parseViaRuntime(input, { mode: 'api' }, async () => ({ ok: false, status: 500 })), /500/);
  const result = parseBatch([{ id: 'x', raw_address: 'rewritten' }])[0];
  await assert.rejects(parseViaRuntime(input, { mode: 'api' }, async () => ({ ok: true, json: async () => ({ records: [{ result }] }) })), /контракт/);
});
