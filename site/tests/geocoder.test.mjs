import test from 'node:test';
import assert from 'node:assert/strict';
import { parseBatchWithReferences, parseOneAddress } from '../src/geocoder.mjs';

const KATO_ROWS = [
  { normalized: 'караганда', canonical: 'Караганда', province: 'Карагандинская область' },
  { normalized: 'астана', canonical: 'Астана', province: 'Астана' },
  { normalized: 'балхаш', canonical: 'Балхаш', province: 'Карагандинская область' },
  { normalized: 'сарань', canonical: 'Сарань', province: 'Карагандинская область' }
];
const kato = async () => KATO_ROWS;
const noVerifiedContexts = async () => [];
const noDistrictIndex = async () => null;

test('single address lookup returns the canonical 14 fields and keeps apartment', async () => {
  const fakeFetch = async () => ({ ok: true, json: async () => [{ address: {
    house_number: '50', road: 'Саттар Ерубаев көшесі', city: 'Қарағанды',
    city_district: 'район имени Казыбек би', state: 'Қарағанды облысы'
  } }] });
  const item = await parseOneAddress('Караганда, ерубаева 50 квартира80', fakeFetch, kato, noVerifiedContexts, noDistrictIndex);
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
  const item = await parseOneAddress('Балхаш Ленина 10 квартира 25', async () => { throw new Error('offline'); }, kato, noVerifiedContexts, noDistrictIndex);
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
  const item = await parseOneAddress('Караганда, ерубаева 50 80', fakeFetch, kato, noVerifiedContexts, noDistrictIndex);
  assert.equal(requested, 'Караганда, ерубаева 50');
  assert.equal(item.result.house, '50');
  assert.equal(item.result.apartment, '80');
});

test('fractional and корпус house numbers stay inside the house field', async () => {
  const fakeFetch = async (url) => ({ ok: true, json: async () => [{ address: {
    house_number: url.searchParams.get('q').includes('50/2') ? '50/2' : '50',
    road: 'улица Саттара Ерубаева', city: 'Караганда', state: 'Карагандинская область'
  } }] });
  const fractional = await parseOneAddress('Караганда Ерубаева 50/2 80', fakeFetch, kato, noVerifiedContexts, noDistrictIndex);
  const corpus = await parseOneAddress('Караганда Ерубаева 50 корпус 2 80', fakeFetch, kato, noVerifiedContexts, noDistrictIndex);
  assert.equal(fractional.result.house, '50/2');
  assert.equal(fractional.result.apartment, '80');
  assert.equal(corpus.result.house, '50/2');
  assert.equal(corpus.result.apartment, '80');
});

test('numeric microdistrict is confirmed before assigning house and apartment', async () => {
  const fakeFetch = async () => ({ ok: true, json: async () => [{ address: {
    house_number: '22', locality: '23 микрорайон', city: 'Караганда', state: 'Карагандинская область'
  } }] });
  const item = await parseOneAddress('Караганда 23 22 16', fakeFetch, kato, noVerifiedContexts, noDistrictIndex);
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
  const item = await parseOneAddress('Караганда 23 22', fakeFetch, kato, noVerifiedContexts, noDistrictIndex);
  assert.deepEqual(requested, ['Караганда 23', 'Караганда 23 22']);
  assert.equal(item.result.house, '22');
  assert.equal(item.result.apartment, null);
});

test('a settlement absent from the old demo list is resolved through KATO', async () => {
  const fakeFetch = async (url) => url.pathname.includes('/reverse')
    ? { ok: true, json: async () => ({ address: { district: 'Балхаш' } }) }
    : { ok: true, json: async () => [{ lat: '46.8', lon: '74.9', address: {
      house_number: '10', road: 'улица Ленина', town: 'Балхаш Г.А.', district: 'Балхаш', state: 'Карагандинская область'
    } }] };
  const item = await parseOneAddress('Балхаш Ленина 10 25', fakeFetch, kato, noVerifiedContexts, noDistrictIndex);
  assert.equal(item.result.city, 'Балхаш');
  assert.equal(item.result.province, 'Карагандинская область');
  assert.equal(item.result.house, '10');
  assert.equal(item.result.apartment, '25');
  assert.equal(item.result.district, null);
});

test('official exact address context fills district when OSM has no city district', async () => {
  const fakeFetch = async () => ({ ok: true, json: async () => [{ address: {
    house_number: '50', road: 'улица Ерубаева', city: 'Караганда', state: 'Карагандинская область'
  } }] });
  const contexts = async () => [{
    source_id: 'qarobl/geonim/213381',
    province: 'Карагандинская область', city: 'Караганда', district: 'район им.Казыбек би',
    street_type: 'улица', street_name: 'Саттара Ерубаева', aliases: ['Ерубаева'],
    houses: [{ source_id: 'qarobl/addreg/0201300205273606', house: '50' }]
  }];

  const item = await parseOneAddress('Караганда ерубаева 50 80', fakeFetch, kato, contexts, noDistrictIndex);

  assert.equal(item.result.district, 'Казыбек би');
  assert.equal(item.result.street_type, 'улица');
  assert.equal(item.result.street_name, 'Саттара Ерубаева');
  assert.equal(item.result.house, '50');
  assert.equal(item.result.apartment, '80');
  assert.equal(item.fields.district.source, 'official_reference');
});

test('official context does not assign a district to an unlisted house', async () => {
  const fakeFetch = async () => ({ ok: true, json: async () => [{ address: {
    house_number: '52', road: 'улица Ерубаева', city: 'Караганда', state: 'Карагандинская область'
  } }] });
  const contexts = async () => [{
    city: 'Караганда', district: 'район им.Казыбек би', street_type: 'улица',
    street_name: 'Саттара Ерубаева', aliases: ['Ерубаева'], houses: [{ house: '50' }]
  }];

  const item = await parseOneAddress('Караганда ерубаева 52 80', fakeFetch, kato, contexts, noDistrictIndex);

  assert.equal(item.result.district, null);
});

test('city district index enriches any exact indexed street and house', async () => {
  const fakeFetch = async () => ({ ok: true, json: async () => [{ address: {
    house_number: '10', road: 'улица Ленина', town: 'Балхаш', state: 'Карагандинская область'
  } }] });
  const balhashKato = async () => KATO_ROWS;
  const districtIndex = async () => ({
    addresses: { 'ленина': { '10': 'Центральный район' } }
  });

  const item = await parseOneAddress(
    'Балхаш Ленина 10 25', fakeFetch, balhashKato, noVerifiedContexts, districtIndex
  );

  assert.equal(item.result.district, 'Центральный');
  assert.equal(item.fields.district.source, 'offline_gazetteer');
});

test('batch lookup uses KATO parent context and offline district references', async () => {
  const contextualKato = async () => [
    { normalized: 'абай', canonical: 'Абай', province: 'Карагандинская область' },
    { normalized: 'абай', canonical: 'Абай', province: 'Алматинская область' },
    { normalized: 'г абай', canonical: 'Абай', province: 'Алматинская область' }
  ];
  const districtIndex = async (city) => city === 'Абай' ? {
    addresses: { 'абая': { '11/26': 'Карасайский район' } }
  } : null;
  const input = [{
    id: 'real-0002',
    raw_address: 'Казахстан, Алматинская область, Абай, Карасайский район, улица Абая, дом 11/26'
  }];

  const payload = await parseBatchWithReferences(
    input, async () => { throw new Error('no network lookup expected'); },
    contextualKato, noVerifiedContexts, districtIndex
  );
  const item = payload.records[0];

  assert.equal(item.result.raw_address, input[0].raw_address);
  assert.equal(item.result.province, 'Алматинская область');
  assert.equal(item.result.city, 'Абай');
  assert.equal(item.result.district, 'Карасайский');
  assert.equal(item.result.street_name, 'Абая');
  assert.equal(item.result.house, '11/26');
  assert.ok(item.result.confidence >= 0.75);
  assert.equal(item.fields.city.source, 'kato');
});
