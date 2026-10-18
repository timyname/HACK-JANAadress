import test from 'node:test';
import assert from 'node:assert/strict';

import { canonicalFields } from '../src/parser.mjs';
import {
  acceptChunk, classifyRecord, createBatchState, extendedExport,
  markFailedChunk, pageIndexes, standardExport, summarizeBatch
} from '../src/batch-state.mjs';

const detail = (id, raw_address, patch = {}, extra = {}) => ({
  result: {
    ...Object.fromEntries(canonicalFields.map((key) => [key, null])),
    id, raw_address, province: 'Region', city: 'Town', street_name: 'Road',
    house: '7', confidence: 0.9, ...patch
  },
  fields: {}, warnings: [], ...extra
});

const payload = (records, fingerprint = 'fixture-v1', catalogs = {
  base: { sha256: 'fixture-only' }, supplemental: { status: 'not_available' }
}) => ({ records, processing_fingerprint: fingerprint, catalogs });

test('accounting keeps duplicate IDs, attention reasons and original state', () => {
  const inputs = [{ id: 'same', raw_address: 'one' }, { id: 'same', raw_address: 'two' }];
  const initial = createBatchState(inputs);
  const ready = acceptChunk(initial, 0, payload([
    detail('same', 'one'), detail('same', 'two', { house: null, confidence: 0.4 })
  ]));
  assert.deepEqual(summarizeBatch(ready), {
    received: 2, processed: 2, no_flags: 1, attention: 1, pending: 0,
    failed: 0,
    reasons: { missing_province: 0, missing_city: 0, missing_street_name: 0,
      missing_house: 1, ambiguity: 0, conflict: 0, low_confidence: 1 }
  });
  assert.equal(summarizeBatch(initial).processed, 0);
  assert.deepEqual(ready.inputs.map((row) => row.id), ['same', 'same']);
});

test('classification distinguishes requirements, ambiguity and coverage', () => {
  assert.deepEqual(classifyRecord(detail('x', 'x')), { attention: false, reasons: [] });
  assert.deepEqual(classifyRecord(detail('x', 'x', { province: ' ', city: null,
    street_name: '', house: null, confidence: 0.75 }, {
    warnings: ['unknown', 'ambiguous_street'],
    enrichment: { status: 'not_available', residential_complex: { status: 'not_found' } }
  })).reasons, ['missing_province', 'missing_city', 'missing_street_name',
    'missing_house', 'ambiguity']);
  assert.deepEqual(classifyRecord(detail('x', 'x', { confidence: 0.749 }, {
    fields: { city: { conflict: true } }, warnings: []
  })).reasons, ['conflict', 'low_confidence']);
});

test('chunk validation is atomic and pins fingerprint plus catalogs', () => {
  const input = [{ id: 'a', raw_address: 'one' }, { id: 'b', raw_address: 'two' }];
  const initial = createBatchState(input);
  const broken = payload([detail('a', 'one'), detail('wrong', 'two')]);
  assert.throws(() => acceptChunk(initial, 0, broken), /BATCH_RESULT_IDENTITY/);
  assert.equal(summarizeBatch(initial).processed, 0);
  const first = acceptChunk(initial, 0, payload([detail('a', 'one')]));
  assert.throws(() => acceptChunk(first, 1, payload([detail('b', 'two')], 'v2')),
    /BATCH_FINGERPRINT_MISMATCH/);
  assert.throws(() => acceptChunk(first, 1, payload([detail('b', 'two')], 'fixture-v1',
    { base: { sha256: 'changed' } })), /BATCH_CATALOGS_MISMATCH/);
  assert.throws(() => acceptChunk(first, 0, payload([detail('a', 'one')])), /BATCH_START/);
});

test('sparse arrays and invalid Unicode cannot create holes', () => {
  assert.throws(() => createBatchState(Array(1)), /BATCH_INPUT_0/);
  assert.throws(() => createBatchState([{ id: '\uD800', raw_address: 'x' }]),
    /BATCH_INPUT_UNICODE_0/);
  assert.doesNotThrow(() => createBatchState([{ id: '😀', raw_address: '𐐷' }]));
  const inputs = [{ id: '0', raw_address: '0' }, { id: '1', raw_address: '1' },
    { id: '2', raw_address: '2' }];
  const records = [detail('0', '0'), detail('1', '1'), detail('2', '2')];
  delete records[1];
  assert.throws(() => acceptChunk(createBatchState(inputs), 0, payload(records)), /BATCH_DETAIL_1/);
});

test('accepted metadata is JSON-safe and enrichment has the v1 minimum shape', () => {
  const input = [{ id: 'a', raw_address: 'one' }];
  const initial = createBatchState(input);
  assert.throws(() => acceptChunk(initial, 0, payload([
    detail('a', 'one', {}, { fields: { bad: 1n }, warnings: [] })
  ])), /BATCH_FIELDS_JSON_0/);
  assert.throws(() => acceptChunk(initial, 0, payload([
    detail('a', 'one', {}, { enrichment: {} })
  ])), /BATCH_ENRICHMENT_0/);
  const cyclic = {};
  cyclic.self = cyclic;
  assert.throws(() => acceptChunk(initial, 0, payload([detail('a', 'one')],
    'fixture-v1', cyclic)), /BATCH_CATALOGS_JSON/);
  assert.equal(summarizeBatch(initial).processed, 0);
});

test('result schema, types and identity reject the whole chunk', () => {
  const inputs = [{ id: 'a', raw_address: 'one' }, { id: 'b', raw_address: 'two' }];
  const initial = createBatchState(inputs);
  const cases = [
    detail('a', 'one', { extra: 'x' }),
    detail('a', 'one', { confidence: Number.NaN }),
    detail('a', 'one', { city: 2 }),
    detail('a', 'wrong')
  ];
  for (const broken of cases) {
    assert.throws(() => acceptChunk(initial, 0, payload([broken])), /BATCH_RESULT/);
    assert.equal(summarizeBatch(initial).processed, 0);
  }
});

test('failed next chunk is visible and retryable', () => {
  const inputs = Array.from({ length: 3 }, (_, index) => ({ id: `${index}`, raw_address: `${index}` }));
  let state = acceptChunk(createBatchState(inputs), 0, payload([detail('0', '0')]));
  state = markFailedChunk(state, 1, 2);
  assert.deepEqual(summarizeBatch(state), {
    received: 3, processed: 1, no_flags: 1, attention: 0, pending: 2, failed: 2,
    reasons: { missing_province: 0, missing_city: 0, missing_street_name: 0,
      missing_house: 0, ambiguity: 0, conflict: 0, low_confidence: 0 }
  });
  state = acceptChunk(state, 1, payload([detail('1', '1'), detail('2', '2')]));
  assert.equal(summarizeBatch(state).pending, 0);
});

test('exports preserve exact14 and partial extended positions defensively', () => {
  const inputs = [{ id: 'a', raw_address: 'one' }, { id: 'a', raw_address: 'two' }];
  const source = payload([detail('a', 'one')]);
  let state = acceptChunk(createBatchState(inputs), 0, source);
  source.records[0].result.house = 'mutated';
  assert.throws(() => standardExport(state), /BATCH_INCOMPLETE/);
  const partial = extendedExport(state);
  assert.doesNotThrow(() => JSON.stringify(partial));
  assert.equal(partial.schema_version, 'jana-enriched/v1');
  assert.equal(partial.complete, false);
  assert.equal(partial.records[1].row_index, 1);
  assert.deepEqual(partial.records[1].input, inputs[1]);
  assert.equal(partial.records[1].result, null);
  state = acceptChunk(state, 1, payload([detail('a', 'two')]));
  const standard = standardExport(state);
  assert.deepEqual(Object.keys(standard[0]), canonicalFields);
  assert.equal(standard[0].house, '7');
  standard[0].house = 'changed';
  assert.equal(standardExport(state)[0].house, '7');
  partial.records[0].result.house = 'extended-change';
  assert.equal(extendedExport(state).records[0].result.house, '7');
});

test('pagination uses row indexes and clamps pages', () => {
  const inputs = Array.from({ length: 205 }, (_, index) => ({ id: 'same', raw_address: `${index}` }));
  const records = inputs.map((row, index) => detail(row.id, row.raw_address,
    index >= 200 ? { house: null } : {}));
  const state = acceptChunk(createBatchState(inputs), 0, payload(records));
  assert.deepEqual(pageIndexes(state, { filter: 'attention', page: 99, pageSize: 2 }),
    { indexes: [204], total: 5, page: 2, pageCount: 3 });
  assert.deepEqual(pageIndexes(createBatchState([]), {}),
    { indexes: [], total: 0, page: 0, pageCount: 0 });
});

test('50000 processed: 48000 no flags, 2000 attention; both exports retain all', () => {
  const inputs = Array.from({ length: 50000 }, (_, index) =>
    ({ id: 'duplicate', raw_address: `synthetic-${index}` }));
  let state = createBatchState(inputs);
  for (let start = 0; start < inputs.length; start += 250) {
    const records = inputs.slice(start, start + 250).map((row, offset) =>
      detail(row.id, row.raw_address, start + offset >= 48000 ? { house: null } : {}));
    state = acceptChunk(state, start, payload(records));
  }
  const summary = summarizeBatch(state);
  assert.deepEqual([summary.received, summary.processed, summary.no_flags, summary.attention],
    [50000, 50000, 48000, 2000]);
  assert.deepEqual(pageIndexes(state, { filter: 'attention', page: 0, pageSize: 100 }).indexes,
    Array.from({ length: 100 }, (_, index) => 48000 + index));
  const page = pageIndexes(state, { filter: 'attention', page: 0, pageSize: 100 });
  assert.equal(page.total, 2000);
  const standard = standardExport(state);
  const extended = extendedExport(state);
  assert.equal(standard.length, 50000);
  assert.equal(extended.records.length, 50000);
  assert.equal(extended.complete, true);
  assert.deepEqual(Object.keys(standard[49999]), canonicalFields);
  assert.equal(standard[49999].raw_address, 'synthetic-49999');
});
