import { canonicalFields, parseBatch } from './parser.mjs';

const SEARCH_ENDPOINT = 'https://nominatim.openstreetmap.org/search';
const REVERSE_ENDPOINT = 'https://nominatim.openstreetmap.org/reverse';
let katoRowsPromise;
let verifiedContextsPromise;
let districtManifestPromise;
const districtIndexPromises = new Map();
const katoAliasIndexes = new WeakMap();

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

async function loadVerifiedContexts(fetcher = fetch) {
  verifiedContextsPromise ??= fetcher(new URL('../data/verified-address-contexts.json', import.meta.url))
    .then((response) => {
      if (!response.ok) throw new Error(`Verified contexts HTTP ${response.status}`);
      return response.json();
    })
    .then((payload) => Array.isArray(payload?.contexts) ? payload.contexts : [])
    .catch(() => []);
  return verifiedContextsPromise;
}

async function loadDistrictIndex(city, fetcher = fetch) {
  if (!city) return null;
  districtManifestPromise ??= fetcher(new URL('../data/district-index/manifest.json', import.meta.url))
    .then((response) => {
      if (!response.ok) throw new Error(`District manifest HTTP ${response.status}`);
      return response.json();
    })
    .catch(() => ({ cities: {} }));
  const manifest = await districtManifestPromise;
  const entry = manifest?.cities?.[normalized(city)];
  if (!entry?.file) return null;
  if (!districtIndexPromises.has(entry.file)) {
    districtIndexPromises.set(entry.file, fetcher(new URL(`../data/district-index/${entry.file}`, import.meta.url))
      .then((response) => {
        if (!response.ok) throw new Error(`District index HTTP ${response.status}`);
        return response.json();
      })
      .catch(() => null));
  }
  return districtIndexPromises.get(entry.file);
}

function stripPlaceMarker(value) {
  return normalized(value)
    .replace(/^(?:г|город|қ|қала|с|село|а|ауыл|п|поселок|посёлок)\s+/u, '')
    .replace(/\s+(?:қ|қала|а|ауыл)$/u, '')
    .trim();
}

function katoAliasIndex(rows) {
  if (katoAliasIndexes.has(rows)) return katoAliasIndexes.get(rows);
  const aliases = new Map();
  for (const row of rows) {
    for (const alias of new Set([
      normalized(row.normalized),
      stripPlaceMarker(row.normalized),
      normalized(row.canonical),
      stripPlaceMarker(row.canonical)
    ])) {
      if (!alias) continue;
      if (!aliases.has(alias)) aliases.set(alias, []);
      aliases.get(alias).push(row);
    }
  }
  katoAliasIndexes.set(rows, aliases);
  return aliases;
}

function findKatoPlace(rawAddress, rows) {
  const words = normalized(rawAddress).split(' ').filter(Boolean);
  if (!words.length || !Array.isArray(rows)) return null;
  const aliases = katoAliasIndex(rows);

  // Full addresses are commonly comma-separated as country, province,
  // settlement, district, street, house. Resolve the settlement segment
  // against KATO and use the province already present in the same string to
  // distinguish repeated names such as Абай or Айтей.
  const segments = rawAddress.split(/[,;]/u).map((part) => part.trim()).filter(Boolean);
  const provinceIndex = segments.findIndex((part) => /(?:область|облысы)/iu.test(part));
  const structured = provinceIndex >= 0 ? segments.slice(provinceIndex + 1) : segments;
  for (const segment of structured) {
    if (/(?:район|ауданы|улица|көшесі|проспект|переулок|дом|үй|квартира)/iu.test(segment)) continue;
    const candidates = new Set();
    for (const key of [normalized(segment), stripPlaceMarker(segment)]) {
      for (const row of aliases.get(key) ?? []) candidates.add(row);
    }
    if (!candidates.size) continue;
    const provinceScoped = [...candidates].filter((row) =>
      row.province && containsNormalizedPhrase(rawAddress, row.province)
    );
    const scoped = provinceScoped.length ? provinceScoped : [...candidates];
    const identities = new Map();
    for (const row of scoped) {
      identities.set(`${normalized(row.canonical)}\u0000${normalized(row.province)}`, row);
    }
    if (identities.size === 1) return identities.values().next().value;
  }

  // Preserve the compact manual-input path ("Караганда Ерубаева 50") where
  // the locality is a prefix rather than its own comma-separated component.
  const starts = [0];
  if (['казахстан', 'қазақстан', 'рк', 'г', 'город', 'қала'].includes(words[0])) starts.push(1);
  if (starts.includes(1) && ['г', 'город', 'қала'].includes(words[1])) starts.push(2);
  for (const start of [...new Set(starts)]) {
    for (let length = Math.min(9, words.length - start); length > 0; length -= 1) {
      const matches = aliases.get(words.slice(start, start + length).join(' ')) ?? [];
      const identities = new Map(matches.map((row) => [
        `${normalized(row.canonical)}\u0000${normalized(row.province)}`, row
      ]));
      if (identities.size === 1) return identities.values().next().value;
    }
  }
  return null;
}

function districtName(value) {
  if (!value) return null;
  return value.replace(/^район\s+(?:(?:имени)\s+|им\.?\s*)?/iu, '').replace(/\s+(?:район|ауданы)$/iu, '').trim() || null;
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

function referenceConfidence(record) {
  const weights = {
    province: 0.08, city: 0.16, district: 0.04, living_area: 0.05,
    street_type: 0.08, street_name: 0.22, house: 0.19,
    apartment: 0.06, entrance: 0.03, floor: 0.03, landmark: 0.03
  };
  let score = 0.08;
  for (const [field, weight] of Object.entries(weights)) {
    if (record[field] != null) score += weight;
  }
  return Number(Math.min(0.96, score).toFixed(4));
}

function numberKey(value) {
  return normalized(value).replace(/\s*(?:корпус|корп|к|строение|стр|дробь)\s*/gu, '/');
}

function houseMatches(expected, actual) {
  return Boolean(expected && actual && numberKey(expected) === numberKey(actual));
}

function containsNormalizedPhrase(text, phrase) {
  return ` ${normalized(text)} `.includes(` ${normalized(phrase)} `);
}

function findVerifiedContext(rawAddress, record, candidates, contexts) {
  if (!record.city || !Array.isArray(contexts)) return null;
  for (const context of contexts) {
    if (normalized(context.city) !== normalized(record.city)) continue;
    const aliases = [context.street_name, ...(context.aliases ?? [])].filter(Boolean);
    if (!aliases.some((alias) => containsNormalizedPhrase(rawAddress, alias))) continue;
    for (const candidate of candidates) {
      const house = (context.houses ?? []).find((item) => houseMatches(candidate.house, item.house));
      if (house) return { context, house, candidate };
    }
  }
  return null;
}

function applyVerifiedContext(item, match) {
  if (!match) return item;
  const { context, candidate } = match;
  const updates = {
    province: context.province ?? null,
    city: context.city,
    district: districtName(context.district),
    street_type: context.street_type,
    street_name: context.street_name,
    house: candidate.house,
    apartment: candidate.apartment
  };
  for (const [field, value] of Object.entries(updates)) {
    if (value == null) continue;
    item.result[field] = value;
    item.fields[field] = { source: 'official_reference' };
  }
  item.result.confidence = Math.max(item.result.confidence, 0.94);
  return item;
}

function applyDistrictIndex(item, index) {
  if (!index || item.result.district || !item.result.street_name || !item.result.house) return item;
  const houses = index.addresses?.[normalized(item.result.street_name)];
  const district = houses?.[numberKey(item.result.house)];
  if (!district) return item;
  item.result.district = districtName(district);
  item.fields.district = { source: 'offline_gazetteer' };
  item.result.confidence = Math.max(item.result.confidence, 0.94);
  return item;
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
  const cityValue = osmCity(address);
  const districtValue = districtName(address.city_district ?? address.borough ?? address.district);
  const updates = {
    province: address.state ?? address.region ?? null,
    city: cityValue,
    district: [cityValue, record.city].some((value) => normalized(districtValue) === normalized(value)) ? null : districtValue,
    living_area: address.neighbourhood ?? address.residential ?? null,
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
    if (value && normalized(value) !== normalized(item.result.city)) {
      item.result.district = value;
      item.fields.district = { source: 'osm' };
    }
  } catch {
    // District is optional; a confirmed street and house remain usable.
  }
  return item;
}

export async function parseOneAddress(
  rawAddress,
  fetcher = fetch,
  katoLoader = loadKatoCities,
  verifiedLoader = loadVerifiedContexts,
  districtLoader = loadDistrictIndex
) {
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
  const candidates = buildCandidates(rawAddress, record);
  const verified = findVerifiedContext(
    rawAddress,
    record,
    candidates,
    await verifiedLoader(fetcher)
  );
  const districtIndex = await districtLoader(record.city, fetcher);
  const baseFields = sourceMeta(record, 'text');
  if (katoPlace) {
    baseFields.city = { source: 'kato' };
    if (record.province) baseFields.province = { source: 'kato' };
  }
  const baseItem = applyVerifiedContext(
    { result: record, fields: baseFields, warnings: [] },
    verified
  );
  try {
    for (const candidate of candidates) {
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
      return applyVerifiedContext(
        applyDistrictIndex(await addDistrictFromReverse(item, place, fetcher), districtIndex),
        verified
      );
    }
    baseItem.warnings.push('osm_not_found');
    return baseItem;
  } catch {
    baseItem.warnings.push('osm_unavailable');
    return baseItem;
  }
}

export async function parseBatchWithReferences(
  input,
  fetcher = fetch,
  katoLoader = loadKatoCities,
  verifiedLoader = loadVerifiedContexts,
  districtLoader = loadDistrictIndex
) {
  const started = performance.now();
  const [katoRows, contexts] = await Promise.all([
    katoLoader(fetcher),
    verifiedLoader(fetcher)
  ]);
  const records = parseBatch(input);
  const enriched = await Promise.all(records.map(async (record) => {
    const katoPlace = findKatoPlace(record.raw_address, katoRows);
    if (katoPlace) {
      record.city = katoPlace.canonical;
      record.province = katoPlace.province || record.province;
    }
    const fields = sourceMeta(record, 'text');
    if (katoPlace) {
      fields.city = { source: 'kato' };
      if (record.province) fields.province = { source: 'kato' };
    }
    record.confidence = referenceConfidence(record);
    const candidates = buildCandidates(record.raw_address, record);
    const verified = findVerifiedContext(record.raw_address, record, candidates, contexts);
    const districtIndex = await districtLoader(record.city, fetcher);
    const item = applyVerifiedContext({ result: record, fields, warnings: [] }, verified);
    applyDistrictIndex(item, districtIndex);
    item.result.confidence = Math.max(item.result.confidence, referenceConfidence(item.result));
    return item;
  }));
  return {
    records: enriched,
    meta: { count: enriched.length, seconds: Number(((performance.now() - started) / 1000).toFixed(4)) }
  };
}
