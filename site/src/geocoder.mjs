import { canonicalFields, parseBatch } from './parser.mjs';

const ENDPOINT = 'https://nominatim.openstreetmap.org/search';

const CITY_NAMES = new Map([
  ['қарағанды', 'Караганда'],
  ['караганда', 'Караганда'],
  ['астана', 'Астана'],
  ['алматы', 'Алматы'],
  ['шымкент', 'Шымкент']
]);

const PROVINCE_NAMES = new Map([
  ['қарағанды облысы', 'Карагандинская область'],
  ['карагандинская область', 'Карагандинская область'],
  ['ақмола облысы', 'Акмолинская область'],
  ['алматы облысы', 'Алматинская область'],
  ['түркістан облысы', 'Туркестанская область']
]);

const KATO_CITY_PROVINCES = new Map([
  ['Астана', 'Астана'], ['Алматы', 'Алматы'], ['Шымкент', 'Шымкент'],
  ['Караганда', 'Карагандинская область'], ['Костанай', 'Костанайская область'],
  ['Актобе', 'Актюбинская область'], ['Атырау', 'Атырауская область'],
  ['Актау', 'Мангистауская область'], ['Павлодар', 'Павлодарская область'],
  ['Семей', 'Абайская область'], ['Тараз', 'Жамбылская область'],
  ['Кокшетау', 'Акмолинская область'], ['Петропавловск', 'Северо-Казахстанская область'],
  ['Талдыкорган', 'Жетысуская область'], ['Туркестан', 'Туркестанская область'],
  ['Уральск', 'Западно-Казахстанская область'], ['Усть-Каменогорск', 'Восточно-Казахстанская область']
]);

function normalized(value) {
  return value?.toLocaleLowerCase('ru-RU').trim() ?? '';
}

function canonical(value, dictionary) {
  if (!value) return null;
  return dictionary.get(normalized(value)) ?? value;
}

function districtName(value) {
  if (!value) return null;
  return value
    .replace(/^район\s+(?:имени\s+)?/iu, '')
    .replace(/\s+(?:район|ауданы)$/iu, '')
    .trim() || null;
}

function streetName(value) {
  if (!value) return null;
  return value
    .replace(/\s+(?:көшесі|улица|проспект|даңғылы)$/iu, '')
    .trim() || null;
}

function streetType(value) {
  if (!value) return null;
  if (/(?:проспект|даңғылы)$/iu.test(value)) return 'проспект';
  if (/(?:переулок|тұйық)$/iu.test(value)) return 'переулок';
  return 'улица';
}

function sourceMeta(record, source) {
  const fields = {};
  for (const field of canonicalFields.slice(2, -1)) {
    fields[field] = { source: record[field] == null ? 'unknown' : source };
  }
  return fields;
}

export function mergeOpenStreetMap(record, place) {
  const address = place?.address ?? {};
  const updates = {
    province: canonical(address.state ?? address.region, PROVINCE_NAMES),
    city: canonical(address.city ?? address.town ?? address.village ?? address.municipality, CITY_NAMES),
    district: districtName(address.city_district ?? address.borough ?? address.district),
    living_area: address.neighbourhood ?? address.residential ?? null,
    street_type: streetType(address.road),
    street_name: streetName(address.road),
    house: address.house_number ?? null
  };

  const merged = { ...record };
  const fields = sourceMeta(record, 'text');
  for (const [field, value] of Object.entries(updates)) {
    if (merged[field] != null || value == null) continue;
    merged[field] = value;
    fields[field] = { source: 'osm' };
  }
  const core = ['province', 'city', 'street_name', 'house'];
  const completeness = core.filter((field) => merged[field] != null).length;
  merged.confidence = Math.max(merged.confidence, Number((0.44 + completeness * 0.12).toFixed(2)));
  return { result: merged, fields, warnings: [] };
}

export async function parseOneAddress(rawAddress, fetcher = fetch) {
  const [record] = parseBatch([{ id: 'manual-001', raw_address: rawAddress }]);
  const katoProvince = KATO_CITY_PROVINCES.get(record.city);
  if (record.province == null && katoProvince) record.province = katoProvince;
  const lookupQuery = [
    record.city,
    [record.street_type, record.street_name].filter(Boolean).join(' '),
    record.house
  ].filter(Boolean).join(', ') || rawAddress;
  const url = new URL(ENDPOINT);
  url.search = new URLSearchParams({
    format: 'jsonv2', addressdetails: '1', limit: '1', countrycodes: 'kz',
    'accept-language': 'ru,kk', q: lookupQuery
  }).toString();

  try {
    const response = await fetcher(url, { headers: { Accept: 'application/json' }, signal: AbortSignal.timeout(12000) });
    if (!response.ok) throw new Error(`OpenStreetMap HTTP ${response.status}`);
    const places = await response.json();
    if (!Array.isArray(places) || !places[0]) {
      const fields = sourceMeta(record, 'text');
      if (katoProvince) fields.province = { source: 'kato' };
      if (record.district && !/(?:район|аудан)/iu.test(rawAddress)) fields.district = { source: 'gazetteer' };
      return { result: record, fields, warnings: ['osm_not_found'] };
    }
    const item = mergeOpenStreetMap(record, places[0]);
    if (katoProvince) item.fields.province = { source: 'kato' };
    if (record.district && !/(?:район|аудан)/iu.test(rawAddress)) {
      item.fields.district = { source: 'gazetteer' };
    }
    return item;
  } catch {
    const fields = sourceMeta(record, 'text');
    if (katoProvince) fields.province = { source: 'kato' };
    if (record.district && !/(?:район|аудан)/iu.test(rawAddress)) fields.district = { source: 'gazetteer' };
    return { result: record, fields, warnings: ['osm_unavailable'] };
  }
}
