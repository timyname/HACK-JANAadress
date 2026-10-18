import test from 'node:test';
import assert from 'node:assert/strict';
import { parseOneAddress } from '../src/geocoder.mjs';

const KATO_ROWS = [
  { normalized: 'караганда', canonical: 'Караганда', province: 'Карагандинская область' },
  { normalized: 'астана', canonical: 'Астана', province: 'Астана' },
  { normalized: 'балхаш', canonical: 'Балхаш', province: 'Карагандинская область' },
  { normalized: 'сарань', canonical: 'Сарань', province: 'Карагандинская область' }
];
const kato = async () => KATO_ROWS;

test('single address lookup returns the canonical 14 fields and keeps apartment', async () => {
  const fakeFetch = async () => ({ ok: true, json: async () => [{ address: {
    house_number: '50', road: 'Саттар Ерубаев көшесі', city: 'Қарағанды',
    city_district: 'район имени Казыбек би', state: 'Қарағанды облысы'
  } }] });
  const item = await parseOneAddress('Караганда, ерубаева 50 квартира80', fakeFetch, kato);
  assert.deepEqual(Object.keys(item.result), [
    'id', 'raw_address', 'province', 'city', 'district', 'living_area',
    'street_type', 'street_name', 'house', 'apartment', 'entrance',
    'floor', 'landmark', 'confidence'
  ]);
  assert.equal(item.result.province, 'Карагандинская область');
  assert.equal(item.fields.province.source, 'kato');
  assert.equal(item.result.city, 'Караганда');
  assert.equal(item.result.district, 'Казыбек би');
  assert.equal(item.result.street_name, 'Саттар Ерубаев');
  assert.equal(item.result.house, '50');
  assert.equal(item.result.apartment, '80');
});

test('KATO still identifies place and province when OSM is unavailable', async () => {
  const item = await parseOneAddress('Балхаш Ленина 10 квартира 25', async () => { throw new Error('offline'); }, kato);
  assert.equal(item.result.city, 'Балхаш');
  assert.equal(item.result.province, 'Карагандинская область');
  assert.deepEqual(item.warnings, ['osm_unavailable']);
});

test('unmarked trailing numbers become house and apartment after confirmation', async () => {
  let requested = '';
  const fakeFetch = async (url) => {
    requested = url.searchParams.get('q');
    return { ok: true, json: async () => [{ address: {
      house_number: '50', road: 'улица Саттара Ерубаева', city: 'Караганда', state: 'Карагандинская область'
    } }] };
  };
  const item = await parseOneAddress('Караганда, ерубаева 50 80', fakeFetch, kato);
  assert.equal(requested, 'Караганда, ерубаева 50');
  assert.equal(item.result.house, '50');
  assert.equal(item.result.apartment, '80');
});

test('fractional and корпус house numbers stay inside the house field', async () => {
  const fakeFetch = async (url) => ({ ok: true, json: async () => [{ address: {
    house_number: url.searchParams.get('q').includes('50/2') ? '50/2' : '50',
    road: 'улица Саттара Ерубаева', city: 'Караганда', state: 'Карагандинская область'
  } }] });
  const fractional = await parseOneAddress('Караганда Ерубаева 50/2 80', fakeFetch, kato);
  const corpus = await parseOneAddress('Караганда Ерубаева 50 корпус 2 80', fakeFetch, kato);
  assert.equal(fractional.result.house, '50/2');
  assert.equal(fractional.result.apartment, '80');
  assert.equal(corpus.result.house, '50/2');
  assert.equal(corpus.result.apartment, '80');
});

test('numeric microdistrict is confirmed before assigning house and apartment', async () => {
  const fakeFetch = async () => ({ ok: true, json: async () => [{ address: {
    house_number: '22', locality: '23 микрорайон', city: 'Караганда', state: 'Карагандинская область'
  } }] });
  const item = await parseOneAddress('Караганда 23 22 16', fakeFetch, kato);
  assert.equal(item.result.street_type, 'микрорайон');
  assert.equal(item.result.street_name, '23');
  assert.equal(item.result.house, '22');
  assert.equal(item.result.apartment, '16');
});

test('two unmarked numbers are not forced into house plus apartment when lookup rejects it', async () => {
  const requested = [];
  const fakeFetch = async (url) => {
    const query = url.searchParams.get('q');
    requested.push(query);
    if (query === 'Караганда 23') return { ok: true, json: async () => [] };
    return { ok: true, json: async () => [{ address: {
      house_number: '22', locality: '23 микрорайон', city: 'Караганда', state: 'Карагандинская область'
    } }] };
  };
  const item = await parseOneAddress('Караганда 23 22', fakeFetch, kato);
  assert.deepEqual(requested, ['Караганда 23', 'Караганда 23 22']);
  assert.equal(item.result.house, '22');
  assert.equal(item.result.apartment, null);
});

test('a settlement absent from the old demo list is resolved through KATO', async () => {
  const fakeFetch = async () => ({ ok: true, json: async () => [{ address: {
    house_number: '10', road: 'улица Ленина', town: 'Балхаш', state: 'Карагандинская область'
  } }] });
  const item = await parseOneAddress('Балхаш Ленина 10 25', fakeFetch, kato);
  assert.equal(item.result.city, 'Балхаш');
  assert.equal(item.result.province, 'Карагандинская область');
  assert.equal(item.result.house, '10');
  assert.equal(item.result.apartment, '25');
});
