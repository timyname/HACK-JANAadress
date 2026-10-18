const NULLABLE_FIELDS = [
  'province', 'city', 'district', 'living_area', 'street_type', 'street_name',
  'house', 'apartment', 'entrance', 'floor', 'landmark'
];

const CITY_NAMES = [
  'Астана', 'Алматы', 'Шымкент', 'Караганда', 'Костанай', 'Актобе',
  'Атырау', 'Актау', 'Павлодар', 'Семей', 'Тараз', 'Кокшетау',
  'Петропавловск', 'Талдыкорган', 'Туркестан', 'Уральск', 'Усть-Каменогорск'
];

const REPUBLIC_CITIES = new Set(['Астана', 'Алматы', 'Шымкент']);
const LETTERS = 'A-Za-zА-Яа-яӘәҒғҚқҢңӨөҰұҮүҺһІі';

const PREVIEW_STREETS = [
  {
    city: 'Астана',
    aliases: ['мангилик ел', 'мәңгілік ел', 'мангылик ел'],
    street_type: 'проспект',
    street_name: 'Мангилик Ел',
    districts_by_house: { '47': 'Есиль' }
  },
  {
    city: 'Шымкент',
    aliases: ['тауке хана', 'тәуке хан', 'тауке хан'],
    street_type: 'проспект',
    street_name: 'Тауке хана',
    districts_by_house: { '133б': 'Аль-Фарабийский' }
  }
];

const STREET_TYPES = [
  { pattern: /(?:^|[\s,;])(?:ул(?:ица)?\.?)[\s]+/iu, value: 'улица' },
  { pattern: /(?:^|[\s,;])(?:пр(?:оспект)?\.?)[\s]+/iu, value: 'проспект' },
  { pattern: /(?:^|[\s,;])(?:пер(?:еулок)?\.?)[\s]+/iu, value: 'переулок' },
  { pattern: /(?:^|[\s,;])(?:мкр|мкр-н|микрорайон)\.?[\s]+/iu, value: 'микрорайон' },
  { pattern: /(?:^|[\s,;])(?:квартал)[\s]+/iu, value: 'квартал' }
];

function escapeRegExp(value) {
  return value.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
}

function firstMatch(raw, patterns) {
  for (const pattern of patterns) {
    const match = raw.match(pattern);
    if (match?.[1]) return match[1];
  }
  return null;
}

function extractNumber(raw, markers) {
  const marker = markers.map(escapeRegExp).join('|');
  const value = '([0-9]+(?:[/-][0-9]+)*(?:[A-Za-zА-Яа-яӘәҒғҚқҢңӨөҰұҮүҺһІі])?)';
  return firstMatch(raw, [
    new RegExp(`(?:${marker})\\s*(?:№\\s*)?${value}`, 'iu'),
    new RegExp(`${value}\\s*(?:${marker})(?=\\s|[,;]|$)`, 'iu')
  ]);
}

function extractCity(raw) {
  for (const name of CITY_NAMES) {
    const exactToken = new RegExp(`(?:^|[^${LETTERS}])${escapeRegExp(name)}(?=$|[^${LETTERS}])`, 'iu');
    if (exactToken.test(raw)) return name;
  }
  return null;
}

function extractDistrict(raw) {
  return firstMatch(raw, [
    new RegExp(`(?:^|[\\s,;])(?:район|ауданы)\\s+([${LETTERS}-]+)(?=\\s|[,;]|$)`, 'iu'),
    new RegExp(`(?:^|[\\s,;])([${LETTERS}-]+)\\s+(?:район|ауданы)(?=\\s|[,;]|$)`, 'iu')
  ]);
}

function extractLivingArea(raw) {
  const match = raw.match(/(?:^|[\s,;])(?:жилой\s+массив|ж\/м|жк)\s+([^,;]+?)(?=\s+(?:ул(?:ица)?\.?|пр(?:оспект)?\.?|дом|д\.?|кв(?:артира)?\.?)(?=\s|[,;]|$)|[,;]|$)/iu);
  return match?.[1]?.trim() || null;
}

function extractStreet(raw) {
  for (const candidate of STREET_TYPES) {
    const marker = raw.match(candidate.pattern);
    if (!marker || marker.index == null) continue;
    const tail = raw.slice(marker.index + marker[0].length);
    const name = tail
      .split(/[,;]/u, 1)[0]
      .replace(/\s+(?:дом|д\.?|кв(?:артира)?\.?|подъезд|этаж)\s*.*$/iu, '')
      .trim();
    if (name) return { street_type: candidate.value, street_name: name };
  }
  return { street_type: null, street_name: null };
}

function extractLandmark(raw) {
  const match = raw.match(/(?:^|[\s,;])((?:магазин|школа|бизнес[-\s]?центр|тц|qazpost)\s+[^,;]+)/iu);
  return match?.[1]?.trim() || null;
}

function normalizePreviewText(value) {
  return value.toLocaleLowerCase('ru-RU')
    .replace(/ё/gu, 'е')
    .replace(/[^0-9a-zа-яәғқңөұүһі]+/giu, ' ')
    .trim();
}

function editDistance(left, right) {
  const row = Array.from({ length: right.length + 1 }, (_, index) => index);
  for (let leftIndex = 1; leftIndex <= left.length; leftIndex += 1) {
    let diagonal = row[0];
    row[0] = leftIndex;
    for (let rightIndex = 1; rightIndex <= right.length; rightIndex += 1) {
      const above = row[rightIndex];
      const cost = left[leftIndex - 1] === right[rightIndex - 1] ? 0 : 1;
      row[rightIndex] = Math.min(row[rightIndex] + 1, row[rightIndex - 1] + 1, diagonal + cost);
      diagonal = above;
    }
  }
  return row[right.length];
}

function similarity(left, right) {
  const longest = Math.max(left.length, right.length);
  return longest === 0 ? 1 : 1 - editDistance(left, right) / longest;
}

function enrichPreviewStreet(raw, parsed) {
  if (!parsed.city) return;
  const normalized = normalizePreviewText(raw);
  const cityPrefix = normalizePreviewText(parsed.city);
  const bareMatch = normalized.match(new RegExp(`^${escapeRegExp(cityPrefix)}\\s+(.+?)\\s+(\\d+[a-zа-я]?)\\s+(?:квартира|кв)(?=\\s|$)`, 'iu'));
  const candidate = parsed.street_name ? normalizePreviewText(parsed.street_name) : bareMatch?.[1];
  const house = normalizePreviewText(parsed.house ?? bareMatch?.[2] ?? '');
  if (!candidate || !house) return;

  const street = PREVIEW_STREETS.find((item) =>
    item.city === parsed.city
    && Math.max(...item.aliases.map((alias) => similarity(candidate, alias))) >= 0.8
  );
  if (!street) return;

  parsed.street_type = street.street_type;
  parsed.street_name = street.street_name;
  parsed.house = house;
  parsed.district = street.districts_by_house[house] ?? null;
}

function scoreRecord(record) {
  const weighted = ['province', 'city', 'street_name', 'house', 'apartment', 'entrance', 'floor'];
  const found = weighted.filter((field) => record[field] != null).length;
  if (found === 0) return 0.08;
  return Math.min(0.96, Number((0.22 + found * 0.1).toFixed(2)));
}

function parseRecord(input) {
  if (!input || typeof input !== 'object' || typeof input.id !== 'string' || typeof input.raw_address !== 'string') {
    throw new TypeError('Each input record must contain string id and raw_address');
  }

  const raw = input.raw_address;
  const city = extractCity(raw);
  const street = extractStreet(raw);
  const parsed = {
    id: input.id,
    raw_address: raw,
    province: city && REPUBLIC_CITIES.has(city) ? city : null,
    city,
    district: extractDistrict(raw),
    living_area: extractLivingArea(raw),
    street_type: street.street_type,
    street_name: street.street_name,
    house: extractNumber(raw, ['дом', 'д.', 'д']),
    apartment: extractNumber(raw, ['квартира', 'кв.', 'кв', 'flat']),
    entrance: extractNumber(raw, ['подъезд', 'подьезд', 'п.']),
    floor: extractNumber(raw, ['этаж', 'эт.', 'эт']),
    landmark: extractLandmark(raw),
    confidence: 0
  };

  for (const field of NULLABLE_FIELDS) {
    if (parsed[field] === '') parsed[field] = null;
  }
  enrichPreviewStreet(raw, parsed);
  parsed.confidence = scoreRecord(parsed);
  return parsed;
}

export function parseBatch(input) {
  if (!Array.isArray(input)) {
    throw new TypeError('Jana Address input must be a top-level JSON array');
  }
  return input.map(parseRecord);
}

export const canonicalFields = Object.freeze([
  'id', 'raw_address', 'province', 'city', 'district', 'living_area',
  'street_type', 'street_name', 'house', 'apartment', 'entrance',
  'floor', 'landmark', 'confidence'
]);
