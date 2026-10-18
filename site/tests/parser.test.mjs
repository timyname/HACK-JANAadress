import test from 'node:test';
import assert from 'node:assert/strict';

async function loadParser() {
  try {
    return await import('../src/parser.mjs');
  } catch {
    return null;
  }
}

test('parser module exposes parseBatch', async () => {
  const parser = await loadParser();
  assert.ok(parser, 'site/src/parser.mjs must exist');
  assert.equal(typeof parser.parseBatch, 'function');
});

test('parseBatch preserves position, id and decoded raw address', async () => {
  const parser = await loadParser();
  assert.ok(parser);
  const input = [
    { id: 'demo-a', raw_address: 'г. Астана, проспект Мәңгілік Ел, дом 42/1, кв 18' },
    { id: 'demo-a', raw_address: 'Шымкент, улица Арман 14Б, подъезд 2, этаж 5' }
  ];

  const output = parser.parseBatch(input);

  assert.equal(output.length, input.length);
  assert.equal(output[0].id, input[0].id);
  assert.equal(output[1].id, input[1].id);
  assert.equal(output[0].raw_address, input[0].raw_address);
  assert.equal(output[1].raw_address, input[1].raw_address);
});

test('parseBatch emits the canonical public key order without internal metadata', async () => {
  const parser = await loadParser();
  assert.ok(parser);
  const [record] = parser.parseBatch([{ id: 'demo-keys', raw_address: 'Караганда, дом 9' }]);
  assert.deepEqual(Object.keys(record), [
    'id', 'raw_address', 'province', 'city', 'district', 'living_area',
    'street_type', 'street_name', 'house', 'apartment', 'entrance',
    'floor', 'landmark', 'confidence'
  ]);
});

test('parser keeps fractional and lettered house numbers as strings', async () => {
  const parser = await loadParser();
  assert.ok(parser);
  const [fractional] = parser.parseBatch([
    { id: 'demo-fraction', raw_address: 'Астана, ул. Достык, дом 42/1, квартира 18' }
  ]);
  const [lettered] = parser.parseBatch([
    { id: 'demo-letter', raw_address: 'Шымкент, улица Арман, дом 14Б' }
  ]);

  assert.equal(fractional.house, '42/1');
  assert.equal(fractional.apartment, '18');
  assert.equal(lettered.house, '14Б');
});

test('parser extracts apartment, entrance and floor markers', async () => {
  const parser = await loadParser();
  assert.ok(parser);
  const [record] = parser.parseBatch([
    { id: 'demo-details', raw_address: 'Алматы, улица Самал, 19 дом, 8 кв, 2 подъезд, 6 этаж' }
  ]);

  assert.equal(record.house, '19');
  assert.equal(record.apartment, '8');
  assert.equal(record.entrance, '2');
  assert.equal(record.floor, '6');
});

test('browser preview corrects the Mangilik typo and adds the district', async () => {
  const parser = await loadParser();
  assert.ok(parser);
  const [record] = parser.parseBatch([
    { id: 'fuzzy-preview', raw_address: 'Астана манглик ел 47 квартира 57' }
  ]);

  assert.equal(record.city, 'Астана');
  assert.equal(record.street_type, 'проспект');
  assert.equal(record.street_name, 'Мангилик Ел');
  assert.equal(record.house, '47');
  assert.equal(record.apartment, '57');
  assert.equal(record.district, 'Есиль');
});

test('browser preview adds a verified Shymkent district by street and house', async () => {
  const parser = await loadParser();
  assert.ok(parser);
  const [record] = parser.parseBatch([
    { id: 'shymkent-preview', raw_address: 'Шымкент, проспект Тауке хана, дом 133Б, квартира 8' }
  ]);

  assert.equal(record.city, 'Шымкент');
  assert.equal(record.street_name, 'Тауке хана');
  assert.equal(record.house, '133б');
  assert.equal(record.apartment, '8');
  assert.equal(record.district, 'Аль-Фарабийский');
});

test('province adjectives do not create substring city false positives', async () => {
  const parser = await loadParser();
  assert.ok(parser);
  const [record] = parser.parseBatch([
    { id: 'demo-province', raw_address: 'Костанайская область, село Заречное, дом 7' }
  ]);

  assert.equal(record.city, null);
});

test('parser supports district marker before or after the district name', async () => {
  const parser = await loadParser();
  assert.ok(parser);
  const [prefix] = parser.parseBatch([
    { id: 'demo-district-a', raw_address: 'район Сарыарка, неизвестный адресный фрагмент' }
  ]);
  const [suffix] = parser.parseBatch([
    { id: 'demo-district-b', raw_address: 'Сарыарка район, неизвестный адресный фрагмент' }
  ]);

  assert.equal(prefix.district, 'Сарыарка');
  assert.equal(suffix.district, 'Сарыарка');
});

test('unparseable rows are returned with null fields and bounded low confidence', async () => {
  const parser = await loadParser();
  assert.ok(parser);
  const [record] = parser.parseBatch([{ id: 'demo-null', raw_address: '—' }]);

  assert.equal(record.street_name, null);
  assert.equal(record.house, null);
  assert.ok(Number.isFinite(record.confidence));
  assert.ok(record.confidence >= 0 && record.confidence <= 0.25);
});

test('invalid top-level input is rejected without partial output', async () => {
  const parser = await loadParser();
  assert.ok(parser);
  assert.throws(() => parser.parseBatch({ id: 'not-an-array' }), /array/i);
});
