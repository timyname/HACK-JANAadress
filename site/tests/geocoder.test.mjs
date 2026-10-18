import test from 'node:test';
import assert from 'node:assert/strict';
import { parseOneAddress } from '../src/geocoder.mjs';

test('single address lookup returns the canonical 14 fields and keeps apartment', async () => {
  const fakeFetch = async () => ({
    ok: true,
    json: async () => [{ address: {
      house_number: '50',
      road: 'Саттар Ерубаев көшесі',
      city: 'Қарағанды',
      state: 'Қарағанды облысы'
    } }]
  });

  const item = await parseOneAddress('Караганда, ерубаева 50 квартира80', fakeFetch);
  assert.deepEqual(Object.keys(item.result), [
    'id', 'raw_address', 'province', 'city', 'district', 'living_area',
    'street_type', 'street_name', 'house', 'apartment', 'entrance',
    'floor', 'landmark', 'confidence'
  ]);
  assert.equal(item.result.province, 'Карагандинская область');
  assert.equal(item.fields.province.source, 'kato');
  assert.equal(item.result.city, 'Караганда');
  assert.equal(item.result.district, 'Казыбек би');
  assert.equal(item.result.street_name, 'Саттара Ерубаева');
  assert.equal(item.result.house, '50');
  assert.equal(item.result.apartment, '80');
});

test('single address lookup falls back to local parsing when OSM is unavailable', async () => {
  const item = await parseOneAddress('Астана манглик ел 47 квартира 57', async () => { throw new Error('offline'); });
  assert.equal(item.result.district, 'Есиль');
  assert.deepEqual(item.warnings, ['osm_unavailable']);
});

test('unmarked trailing numbers become house and apartment only after address lookup', async () => {
  let requested = '';
  const fakeFetch = async (url) => {
    requested = url.searchParams.get('q');
    return { ok: true, json: async () => [{ address: {
      house_number: '50', road: 'улица Саттара Ерубаева', city: 'Караганда', state: 'Карагандинская область'
    } }] };
  };
  const item = await parseOneAddress('Караганда, ерубаева 50 80', fakeFetch);
  assert.equal(requested, 'Караганда, ерубаева, 50');
  assert.equal(item.result.street_name, 'Саттара Ерубаева');
  assert.equal(item.result.house, '50');
  assert.equal(item.result.apartment, '80');
  assert.equal(item.result.district, 'Казыбек би');
});

test('fractional and корпус house numbers stay inside the house field', async () => {
  const fakeFetch = async (url) => ({ ok: true, json: async () => [{ address: {
    house_number: url.searchParams.get('q').endsWith('50/2') ? '50/2' : '50',
    road: 'улица Саттара Ерубаева', city: 'Караганда', state: 'Карагандинская область'
  } }] });
  const fractional = await parseOneAddress('Караганда Ерубаева 50/2 80', fakeFetch);
  const corpus = await parseOneAddress('Караганда Ерубаева 50 корпус 2 80', fakeFetch);
  assert.equal(fractional.result.house, '50/2');
  assert.equal(fractional.result.apartment, '80');
  assert.equal(corpus.result.house, '50/2');
  assert.equal(corpus.result.apartment, '80');
});

test('numeric microdistrict is confirmed before assigning house and apartment', async () => {
  const fakeFetch = async () => ({ ok: true, json: async () => [{ address: {
    house_number: '22', locality: '23 микрорайон', city: 'Караганда', state: 'Карагандинская область'
  } }] });
  const item = await parseOneAddress('Караганда 23 22 16', fakeFetch);
  assert.equal(item.result.street_type, 'микрорайон');
  assert.equal(item.result.street_name, '23');
  assert.equal(item.result.house, '22');
  assert.equal(item.result.apartment, '16');
});
