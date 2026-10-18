import { canonicalFields } from './parser.mjs';

const EDITABLE_FIELDS = new Set([
  'province', 'city', 'district', 'living_area', 'street_type', 'street_name',
  'house', 'apartment', 'entrance', 'floor', 'landmark', 'confidence'
]);

export const MAX_UPLOAD_BYTES = 2 * 1024 * 1024;
export const MAX_UPLOAD_RECORDS = 1000;

export function assertUploadSize(bytes, maxBytes = MAX_UPLOAD_BYTES) {
  if (!Number.isFinite(bytes) || bytes < 0) throw new TypeError('Не удалось определить размер файла');
  if (bytes > maxBytes) throw new RangeError('Файл превышает безопасный лимит 2 MB');
}

export function parseUpload(text) {
  let parsed;
  try {
    parsed = JSON.parse(text);
  } catch {
    throw new TypeError('Файл не является валидным JSON');
  }
  if (!Array.isArray(parsed)) {
    throw new TypeError('Вход должен быть top-level JSON array');
  }
  if (parsed.length > MAX_UPLOAD_RECORDS) {
    throw new RangeError(`Browser preview поддерживает не более ${MAX_UPLOAD_RECORDS} записей за один пакет`);
  }
  parsed.forEach((record, index) => {
    if (!record || typeof record !== 'object') {
      throw new TypeError(`Запись ${index + 1} должна быть объектом`);
    }
    if (typeof record.id !== 'string') {
      throw new TypeError(`Запись ${index + 1}: id должен быть строкой`);
    }
    if (typeof record.raw_address !== 'string') {
      throw new TypeError(`Запись ${index + 1}: raw_address должен быть строкой`);
    }
  });
  return parsed;
}

export function reconcileSelectedIndex(records, selected, filter) {
  const visible = records
    .map((record, index) => ({ record, index }))
    .filter(({ record }) => filter === 'all' || record.confidence < 0.75)
    .map(({ index }) => index);
  return visible.includes(selected) ? selected : (visible[0] ?? -1);
}

export function applyRecordEdit(record, patch) {
  return { ...record, ...createEditOverlay(patch) };
}

export function createEditOverlay(patch) {
  const overlay = {};
  for (const [key, value] of Object.entries(patch)) {
    if (!EDITABLE_FIELDS.has(key)) continue;
    if (key === 'confidence') {
      const numeric = Number(value);
      if (Number.isFinite(numeric)) overlay[key] = Math.min(1, Math.max(0, numeric));
    } else {
      overlay[key] = value === '' ? null : value;
    }
  }
  return overlay;
}

export function toDownloadJson(records) {
  const canonical = records.map((record) => Object.fromEntries(
    canonicalFields.map((field) => [field, record[field] ?? null])
  ));
  return `${JSON.stringify(canonical, null, 2)}\n`;
}
