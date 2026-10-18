import test from 'node:test';
import assert from 'node:assert/strict';

async function loadState() {
  try {
    return await import('../src/ui-state.mjs');
  } catch {
    return null;
  }
}

test('upload parser accepts only a top-level array with string id and raw_address', async () => {
  const state = await loadState();
  assert.ok(state, 'site/src/ui-state.mjs must exist');

  const parsed = state.parseUpload('[{"id":"demo","raw_address":"synthetic"}]');
  assert.equal(parsed.length, 1);
  assert.throws(() => state.parseUpload('{"id":"demo"}'), /array/i);
  assert.throws(() => state.parseUpload('[{"id":"demo"}]'), /raw_address/i);
});

test('mobile upload guard rejects oversized files before reading their contents', async () => {
  const state = await loadState();
  assert.ok(state);
  assert.doesNotThrow(() => state.assertUploadSize(2 * 1024 * 1024));
  assert.throws(() => state.assertUploadSize((2 * 1024 * 1024) + 1), /2 MB/i);
});

test('upload parser caps a browser batch at the jury-sized preview limit', async () => {
  const state = await loadState();
  assert.ok(state);
  const oversized = Array.from({ length: 1001 }, (_, index) => ({
    id: `demo-${index}`,
    raw_address: 'synthetic'
  }));
  assert.throws(() => state.parseUpload(JSON.stringify(oversized)), /1000/i);
});

test('filter reconciliation selects a visible row instead of a hidden record', async () => {
  const state = await loadState();
  assert.ok(state);
  const records = [
    { confidence: 0.9 },
    { confidence: 0.4 },
    { confidence: 0.2 }
  ];
  assert.equal(state.reconcileSelectedIndex(records, 0, 'review'), 1);
  assert.equal(state.reconcileSelectedIndex(records, 2, 'review'), 2);
  assert.equal(state.reconcileSelectedIndex(records, 2, 'all'), 2);
});

test('record edits cannot mutate id or raw_address', async () => {
  const state = await loadState();
  assert.ok(state);
  const original = {
    id: 'demo', raw_address: 'synthetic', province: null, city: null,
    district: null, living_area: null, street_type: null, street_name: null,
    house: null, apartment: null, entrance: null, floor: null,
    landmark: null, confidence: 0.08
  };

  const edited = state.applyRecordEdit(original, {
    id: 'changed', raw_address: 'changed', city: 'Астана', house: '12'
  });

  assert.equal(edited.id, original.id);
  assert.equal(edited.raw_address, original.raw_address);
  assert.equal(edited.city, 'Астана');
  assert.equal(edited.house, '12');
});

test('manual edits remain a separate overlay over an immutable parser result', async () => {
  const state = await loadState();
  assert.ok(state);
  const parserResult = Object.freeze({
    id: 'demo', raw_address: 'synthetic', province: null, city: null,
    district: null, living_area: null, street_type: null, street_name: null,
    house: null, apartment: null, entrance: null, floor: null,
    landmark: null, confidence: 0.08
  });

  const overlay = state.createEditOverlay({
    id: 'changed', raw_address: 'changed', city: 'Астана', house: '12'
  });
  const current = state.applyRecordEdit(parserResult, overlay);

  assert.deepEqual(overlay, { city: 'Астана', house: '12' });
  assert.equal(parserResult.city, null);
  assert.equal(parserResult.house, null);
  assert.equal(current.city, 'Астана');
  assert.equal(current.raw_address, parserResult.raw_address);
});

test('download payload contains canonical records and no UI metadata', async () => {
  const state = await loadState();
  assert.ok(state);
  const record = {
    id: 'demo', raw_address: 'synthetic', province: null, city: 'Астана',
    district: null, living_area: null, street_type: 'улица', street_name: 'Арман',
    house: '12', apartment: null, entrance: null, floor: null,
    landmark: null, confidence: 0.62, selected: true, explanation: 'internal'
  };

  const payload = JSON.parse(state.toDownloadJson([record]));
  assert.deepEqual(Object.keys(payload[0]), [
    'id', 'raw_address', 'province', 'city', 'district', 'living_area',
    'street_type', 'street_name', 'house', 'apartment', 'entrance',
    'floor', 'landmark', 'confidence'
  ]);
  assert.equal(payload[0].selected, undefined);
  assert.equal(payload[0].explanation, undefined);
});
