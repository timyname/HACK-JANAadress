import { canonicalFields, parseBatch } from './parser.mjs';

const SEARCH_ENDPOINT = 'https://nominatim.openstreetmap.org/search';
const REVERSE_ENDPOINT = 'https://nominatim.openstreetmap.org/reverse';
let katoRowsPromise;

function normalized(value) {
  return String(value ?? '').normalize('NFKC').toLocaleLowerCase('ru-RU')
    .replace(/ё/gu, 'е').replace(/[^0-9a-zа-яәғқңөұүһі]+/giu, ' ').trim();
}

async function loadKatoCities(fetcher = fetch) {
  katoRowsPromise ??= fetcher(new URL('../data/kato-cities.json', import.meta.url))
    .then((response) => {
      if (!response.ok) throw new Error(`KATO HTTP ${response.status}`);
      return response.json();
    })
    .catch(() => []);
  return katoRowsPromise;
}

function findKatoPlace(rawAddress, rows) {
  const words = normalized(rawAddress).split(' ').filter(Boolean);
  if (!words.length || !Array.isArray(rows)) return null;
  const aliases = new Map(rows.map((row) => [row.normalized, row]));
  const starts = [0];
  if (['казахстан', 'қазақстан', 'рк', 'г', 'город', 'қала'].includes(words[0])) starts.push(1);
  if (starts.includes(1) && ['г', 'город', 'қала'].includes(words[1])) starts.push(2);
  for (const start of [...new Set(starts)]) {
    for (let length = Math.min(9, words.length - start); length > 0; length -= 1) {
      const row = aliases.get(words.slice(start, start + length).join(' '));
      if (row) return row;
    }
  }
  return null;
}

function districtName(value) {
  if (!value) return null;
  return value.replace(/^район\s+(?:имени\s+)?/iu, '').replace(/\s+(?:район|ауданы)$/iu, '').trim() || null;
}

function streetName(value) {
  if (!value) return null;
  return value
    .replace(/^(?:улица|проспект|переулок|проезд|бульвар|шоссе|квартал|микрорайон|мкр\.?)\s+/iu, '')
    .replace(/\s+(?:көшесі|улица|проспект|даңғылы|тұйық)$/iu, '').trim() || null;
}

function streetType(value) {
  if (!value) return null;
  if (/(?:проспект|даңғылы)/iu.test(value)) return 'проспект';
  if (/(?:переулок|тұйық)/iu.test(value)) return 'переулок';
  if (/бульвар/iu.test(value)) return 'бульвар';
  if (/шоссе/iu.test(value)) return 'шоссе';
  if (/проезд/iu.test(value)) return 'проезд';
  return 'улица';
}

function sourceMeta(record, source) {
  const fields = {};
  for (const field of canonicalFields.slice(2, -1)) fields[field] = { source: record[field] == null ? 'unknown' : source };
  return fields;
}

function numberKey(value) {
  return normalized(value).replace(/\s*(?:корпус|корп|к|строение|стр|дробь)\s*/gu, '/');
}

function houseMatches(expected, actual) {
  return Boolean(expected && actual && numberKey(expected) === numberKey(actual));
}

function cleanExplicitQuery(rawAddress) {
  return rawAddress
    .replace(/(?:^|[\s,;])(?:квартира|кв\.?|flat)\s*№?\s*\d+[\w/-]*/giu, ' ')
    .replace(/(?:^|[\s,;])(?:подъезд|подьезд|п\.?|этаж|эт\.?)\s*№?\s*\d+[\w/-]*/giu, ' ')
    .replace(/[\s,;]+$/u, '').replace(/\s{2,}/gu, ' ').trim();
}

function buildCandidates(rawAddress, record) {
  const explicitHouse = /(?:^|[\s,;])(?:дом|д\.?)\s*№?\s*\d/iu.test(rawAddress);
  if (explicitHouse || record.apartment || record.entrance || record.floor) {
    return [{ query: cleanExplicitQuery(rawAddress), house: record.house, apartment: record.apartment, strict: Boolean(record.house) }];
  }
  const clean = rawAddress.replace(/[\s,;]+$/u, '').trim();
  const number = '\\d+(?:[/-]\\d+)*(?:[A-Za-zА-Яа-яӘәҒғҚқҢңӨөҰұҮүҺһІі])?';
  const candidates = [];
  const corpus = clean.match(new RegExp(`^(.*?\\b(${number}))\\s+(?:корпус|корп\\.?|к\\.?|строение|стр\\.?|дробь)\\s*(${number})\\s+(${number})$`, 'iu'));
  if (corpus) {
    const house = `${corpus[2]}/${corpus[3]}`;
    candidates.push({ query: `${corpus[1]}/${corpus[3]}`, house, apartment: corpus[4], strict: true });
  } else {
    const pair = clean.match(new RegExp(`^(.*?)\\s+(${number})\\s+(${number})$`, 'iu'));
    if (pair) candidates.push({ query: `${pair[1]} ${pair[2]}`, house: pair[2], apartment: pair[3], strict: true });
  }
  const last = clean.match(new RegExp(`^(.*?)\\s+(${number})$`, 'iu'));
  if (last) candidates.push({ query: clean, house: last[2], apartment: null, strict: true });
  if (!candidates.length) candidates.push({ query: clean, house: record.house, apartment: record.apartment, strict: false });
  return candidates;
}

function areaFromAddress(address) {
  const surface = address.locality ?? address.quarter ?? null;
  if (!surface) return {};
  const name = surface.replace(/^(?:микрорайон|мкр\.?)\s*/iu, '').replace(/\s*(?:микрорайон|мкр\.?)$/iu, '').trim();
  return name ? { street_type: 'микрорайон', street_name: name } : {};
}

function osmCity(address) {
  return address.city ?? address.town ?? address.village ?? address.municipality ?? address.hamlet ?? null;
}

export function mergeOpenStreetMap(record, place) {
  const address = place?.address ?? {};
  const area = areaFromAddress(address);
  const updates = {
    province: address.state ?? address.region ?? null,
    city: osmCity(address),
    district: districtName(address.city_district ?? address.borough ?? address.district),
    living_area: address.neighbourhood ?? address.residential ?? address.suburb ?? null,
    street_type: streetType(address.road) ?? area.street_type ?? null,
    street_name: streetName(address.road) ?? area.street_name ?? null,
    house: address.house_number ?? null
  };
  const merged = { ...record };
  const fields = sourceMeta(record, 'text');
  for (const [field, value] of Object.entries(updates)) {
    if (value == null || (['province', 'city'].includes(field) && merged[field] != null)) continue;
    merged[field] = value;
    fields[field] = { source: 'osm' };
  }
  const completeness = ['province', 'city', 'street_name', 'house'].filter((field) => merged[field] != null).length;
  merged.confidence = Math.max(merged.confidence, Number((0.44 + completeness * 0.12).toFixed(2)));
  return { result: merged, fields, warnings: [] };
}

async function requestJson(fetcher, endpoint, parameters) {
  const url = new URL(endpoint);
  url.search = new URLSearchParams(parameters).toString();
  const response = await fetcher(url, { headers: { Accept: 'application/json' }, signal: AbortSignal.timeout(12000) });
  if (!response.ok) throw new Error(`OpenStreetMap HTTP ${response.status}`);
  return response.json();
}

async function addDistrictFromReverse(item, place, fetcher) {
  if (item.result.district || place?.lat == null || place?.lon == null) return item;
  try {
    const reverse = await requestJson(fetcher, REVERSE_ENDPOINT, {
      format: 'jsonv2', addressdetails: '1', zoom: '14', 'accept-language': 'ru,kk', lat: place.lat, lon: place.lon
    });
    const value = districtName(reverse?.address?.city_district ?? reverse?.address?.borough ?? reverse?.address?.district);
    if (value) {
      item.result.district = value;
      item.fields.district = { source: 'osm' };
    }
  } catch {
    // District is optional; a confirmed street and house remain usable.
  }
  return item;
}

export async function parseOneAddress(rawAddress, fetcher = fetch, katoLoader = loadKatoCities) {
  const [record] = parseBatch([{ id: 'manual-001', raw_address: rawAddress }]);
  const katoPlace = findKatoPlace(rawAddress, await katoLoader(fetcher));
  if (katoPlace) {
    record.city = katoPlace.canonical;
    record.province = katoPlace.province || record.province;
  }
  if (!/(?:^|[\s,;])(?:район|ауданы)(?=\s|[,;]|$)/iu.test(rawAddress)) record.district = null;
  if (!/(?:^|[\s,;])(?:ул(?:ица)?\.?|пр(?:оспект)?\.?|пер(?:еулок)?\.?|мкр|мкр-н|микрорайон|квартал)(?=\s)/iu.test(rawAddress)) {
    record.street_type = null;
    record.street_name = null;
  }
  const baseFields = sourceMeta(record, 'text');
  if (katoPlace) {
    baseFields.city = { source: 'kato' };
    if (record.province) baseFields.province = { source: 'kato' };
  }
  try {
    for (const candidate of buildCandidates(rawAddress, record)) {
      const places = await requestJson(fetcher, SEARCH_ENDPOINT, {
        format: 'jsonv2', addressdetails: '1', limit: '1', countrycodes: 'kz', 'accept-language': 'ru,kk', q: candidate.query
      });
      const place = Array.isArray(places) ? places[0] : null;
      if (!place || (candidate.strict && !houseMatches(candidate.house, place.address?.house_number))) continue;
      const item = mergeOpenStreetMap(record, place);
      if (katoPlace) {
        item.fields.city = { source: 'kato' };
        if (record.province) item.fields.province = { source: 'kato' };
      }
      if (candidate.house) {
        item.result.house = place.address?.house_number ?? candidate.house;
        item.fields.house = { source: place.address?.house_number ? 'osm' : 'structure' };
      }
      if (candidate.apartment) {
        item.result.apartment = candidate.apartment;
        item.fields.apartment = { source: 'structure' };
      }
      return addDistrictFromReverse(item, place, fetcher);
    }
    return { result: record, fields: baseFields, warnings: ['osm_not_found'] };
  } catch {
    return { result: record, fields: baseFields, warnings: ['osm_unavailable'] };
  }
}
