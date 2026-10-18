import { canonicalFields, parseBatch } from './parser.mjs';

export async function detectRuntime(fetcher = fetch) {
  try {
    const response = await fetcher('/health/ready', { signal: AbortSignal.timeout(3000), credentials: 'same-origin' });
    if (response.ok) {
      const health = await response.json();
      if (health.service === 'jana-address' && health.status === 'ready') return { mode: 'api', health };
    }
  } catch { /* A static Site has no server runtime; its mode is shown explicitly. */ }
  return { mode: 'browser' };
}

export async function parseViaRuntime(input, runtime, fetcher = fetch) {
  if (runtime.mode === 'browser') {
    return { records: parseBatch(input).map(result => ({ result, fields: {}, warnings: [] })) };
  }
  const response = await fetcher('/internal/parse', {
    method: 'POST', credentials: 'same-origin', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(input), signal: AbortSignal.timeout(310000)
  });
  if (!response.ok) throw new Error(`Сервис вернул HTTP ${response.status}. Пакет не заменён.`);
  const payload = await response.json();
  if (!Array.isArray(payload.records) || payload.records.length !== input.length) {
    throw new Error('Сервис нарушил контракт количества записей.');
  }
  for (let index = 0; index < input.length; index += 1) {
    const row = payload.records[index]?.result;
    if (!row || row.id !== input[index].id || row.raw_address !== input[index].raw_address
      || Object.keys(row).length !== canonicalFields.length || canonicalFields.some(key => !(key in row))
      || !Number.isFinite(row.confidence) || row.confidence < 0 || row.confidence > 1
      || canonicalFields.slice(2, -1).some(key => row[key] !== null && typeof row[key] !== 'string')) {
      throw new Error(`Сервис нарушил контракт записи ${index + 1}.`);
    }
  }
  return payload;
}
