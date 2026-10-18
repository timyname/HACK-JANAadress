import { canonicalFields } from './parser.mjs';

const MAX_ROWS = 50000;
const REQUIRED_FIELDS = ['province', 'city', 'street_name', 'house'];
const REASONS = [
  'missing_province', 'missing_city', 'missing_street_name', 'missing_house',
  'ambiguity', 'conflict', 'low_confidence'
];
const NULLABLE_FIELDS = new Set(canonicalFields.slice(2, -1));

const isObject = (value) => value !== null && typeof value === 'object' && !Array.isArray(value);
const clone = (value) => structuredClone(value);
const ENRICHMENT_STATUSES = new Set([
  'matched', 'ambiguous', 'not_found', 'not_available', 'conflict'
]);

function freezeTree(value, seen = new WeakSet()) {
  if (value === null || typeof value !== 'object' || seen.has(value)) return value;
  seen.add(value);
  for (const child of Object.values(value)) freezeTree(child, seen);
  return Object.freeze(value);
}

function stableValue(value) {
  if (Array.isArray(value)) return value.map(stableValue);
  if (!isObject(value)) return value;
  return Object.fromEntries(Object.keys(value).sort().map((key) => [key, stableValue(value[key])]));
}

const stableJson = (value) => JSON.stringify(stableValue(value));

function fail(code) {
  throw new TypeError(code);
}

function isValidUnicode(value) {
  for (let index = 0; index < value.length; index += 1) {
    const unit = value.charCodeAt(index);
    if (unit >= 0xD800 && unit <= 0xDBFF) {
      const next = value.charCodeAt(index + 1);
      if (!(next >= 0xDC00 && next <= 0xDFFF)) return false;
      index += 1;
    } else if (unit >= 0xDC00 && unit <= 0xDFFF) {
      return false;
    }
  }
  return true;
}

function validateJson(value, code, seen = new WeakSet()) {
  if (value === null || typeof value === 'string' || typeof value === 'boolean') return;
  if (typeof value === 'number') {
    if (!Number.isFinite(value)) fail(code);
    return;
  }
  if (typeof value !== 'object' || seen.has(value)) fail(code);
  seen.add(value);
  if (Array.isArray(value)) {
    for (let index = 0; index < value.length; index += 1) {
      if (!Object.hasOwn(value, index)) fail(code);
      validateJson(value[index], code, seen);
    }
  } else {
    const prototype = Object.getPrototypeOf(value);
    if (prototype !== Object.prototype && prototype !== null) fail(code);
    for (const child of Object.values(value)) validateJson(child, code, seen);
  }
  seen.delete(value);
}

function validateInput(input, index) {
  if (!isObject(input) || typeof input.id !== 'string' || typeof input.raw_address !== 'string') {
    fail(`BATCH_INPUT_${index}`);
  }
  if (!isValidUnicode(input.id) || !isValidUnicode(input.raw_address)) {
    fail(`BATCH_INPUT_UNICODE_${index}`);
  }
  if (Array.from(input.id).length > 1024 || Array.from(input.raw_address).length > 8192) {
    fail(`BATCH_INPUT_LIMIT_${index}`);
  }
}

function projectResult(result, index) {
  if (!isObject(result)) fail(`BATCH_RESULT_${index}`);
  const keys = Object.keys(result);
  if (keys.length !== canonicalFields.length ||
      canonicalFields.some((field) => !Object.hasOwn(result, field))) {
    fail(`BATCH_RESULT_FIELDS_${index}`);
  }
  if (typeof result.id !== 'string' || typeof result.raw_address !== 'string') {
    fail(`BATCH_RESULT_TYPES_${index}`);
  }
  for (const field of NULLABLE_FIELDS) {
    if (result[field] !== null && typeof result[field] !== 'string') {
      fail(`BATCH_RESULT_TYPES_${index}`);
    }
  }
  if (typeof result.confidence !== 'number' || !Number.isFinite(result.confidence) ||
      result.confidence < 0 || result.confidence > 1) {
    fail(`BATCH_RESULT_CONFIDENCE_${index}`);
  }
  return Object.fromEntries(canonicalFields.map((field) => [field, result[field]]));
}

function defaultEnrichment() {
  return {
    status: 'not_available',
    residential_complex: { value: null, status: 'not_available', evidence: [] },
    available_entrances: []
  };
}

function validateEnrichment(enrichment, index) {
  if (!isObject(enrichment) || !ENRICHMENT_STATUSES.has(enrichment.status) ||
      !isObject(enrichment.residential_complex) ||
      !ENRICHMENT_STATUSES.has(enrichment.residential_complex.status) ||
      (enrichment.residential_complex.value !== null &&
       typeof enrichment.residential_complex.value !== 'string') ||
      !Array.isArray(enrichment.residential_complex.evidence) ||
      !Array.isArray(enrichment.available_entrances)) {
    fail(`BATCH_ENRICHMENT_${index}`);
  }
  validateJson(enrichment, `BATCH_ENRICHMENT_JSON_${index}`);
}

function validateDetail(detail, input, index) {
  if (!isObject(detail)) fail(`BATCH_DETAIL_${index}`);
  const allowed = new Set(['result', 'fields', 'warnings', 'enrichment']);
  if (Object.keys(detail).some((key) => !allowed.has(key))) fail(`BATCH_DETAIL_FIELDS_${index}`);
  const result = projectResult(detail.result, index);
  if (result.id !== input.id || result.raw_address !== input.raw_address) {
    fail(`BATCH_RESULT_IDENTITY_${index}`);
  }
  if (!isObject(detail.fields)) fail(`BATCH_FIELDS_${index}`);
  validateJson(detail.fields, `BATCH_FIELDS_JSON_${index}`);
  if (!Array.isArray(detail.warnings) || detail.warnings.some((item) => typeof item !== 'string')) {
    fail(`BATCH_WARNINGS_${index}`);
  }
  const enrichment = detail.enrichment ?? defaultEnrichment();
  validateEnrichment(enrichment, index);
  return freezeTree({
    state: 'processed',
    result: clone(result),
    fields: clone(detail.fields),
    warnings: clone(detail.warnings),
    enrichment: clone(enrichment),
    manual_edits: {}
  });
}

function nextUnprocessed(rows) {
  let index = 0;
  while (index < rows.length && rows[index].state === 'processed') index += 1;
  return index;
}

export function createBatchState(inputs) {
  if (!Array.isArray(inputs) || inputs.length > MAX_ROWS) fail('BATCH_INPUT');
  for (let index = 0; index < inputs.length; index += 1) {
    if (!Object.hasOwn(inputs, index)) fail(`BATCH_INPUT_${index}`);
    validateInput(inputs[index], index);
  }
  const detached = inputs.map((input) => freezeTree({ id: input.id, raw_address: input.raw_address }));
  return Object.freeze({
    inputs: Object.freeze(detached),
    rows: Object.freeze(detached.map(() => Object.freeze({ state: 'pending' }))),
    processing_fingerprint: null,
    catalogs: null
  });
}

export function acceptChunk(state, start, payload) {
  if (!Number.isInteger(start) || start < 0 || start !== nextUnprocessed(state.rows)) {
    fail('BATCH_START');
  }
  if (!isObject(payload) || !Array.isArray(payload.records) || payload.records.length === 0) {
    fail('BATCH_PAYLOAD');
  }
  for (let offset = 0; offset < payload.records.length; offset += 1) {
    if (!Object.hasOwn(payload.records, offset)) fail(`BATCH_DETAIL_${start + offset}`);
  }
  if (typeof payload.processing_fingerprint !== 'string' || !payload.processing_fingerprint.trim()) {
    fail('BATCH_FINGERPRINT');
  }
  if (!isObject(payload.catalogs)) fail('BATCH_CATALOGS');
  validateJson(payload.catalogs, 'BATCH_CATALOGS_JSON');
  if (start + payload.records.length > state.rows.length) fail('BATCH_RANGE');
  if (state.processing_fingerprint !== null &&
      state.processing_fingerprint !== payload.processing_fingerprint) {
    fail('BATCH_FINGERPRINT_MISMATCH');
  }
  if (state.catalogs !== null && stableJson(state.catalogs) !== stableJson(payload.catalogs)) {
    fail('BATCH_CATALOGS_MISMATCH');
  }

  const accepted = payload.records.map((detail, offset) => {
    const index = start + offset;
    if (state.rows[index].state === 'processed') fail('BATCH_OVERLAP');
    return validateDetail(detail, state.inputs[index], index);
  });
  const rows = state.rows.slice();
  accepted.forEach((row, offset) => { rows[start + offset] = row; });
  const catalogs = state.catalogs ?? freezeTree(clone(payload.catalogs));
  return Object.freeze({
    inputs: state.inputs,
    rows: Object.freeze(rows),
    processing_fingerprint: state.processing_fingerprint ?? payload.processing_fingerprint,
    catalogs
  });
}

export function markFailedChunk(state, start, count) {
  if (!Number.isInteger(start) || start < 0 || start !== nextUnprocessed(state.rows) ||
      !Number.isInteger(count) || count <= 0 || start + count > state.rows.length) {
    fail('BATCH_FAILED_RANGE');
  }
  const rows = state.rows.slice();
  for (let index = start; index < start + count; index += 1) {
    if (rows[index].state === 'processed') fail('BATCH_FAILED_OVERLAP');
    rows[index] = Object.freeze({ state: 'failed' });
  }
  return Object.freeze({ ...state, rows: Object.freeze(rows) });
}

function scanStatuses(value, reasons, seen = new WeakSet()) {
  if (value === null || typeof value !== 'object' || seen.has(value)) return;
  seen.add(value);
  for (const [key, child] of Object.entries(value)) {
    if ((key === 'status' || key === 'source_status') && typeof child === 'string') {
      if (child.includes('ambiguous') || child.includes('ambiguity')) reasons.add('ambiguity');
      if (child.includes('conflict')) reasons.add('conflict');
    }
    if (key === 'ambiguous' && child === true) reasons.add('ambiguity');
    if (key === 'conflict' && child === true) reasons.add('conflict');
    scanStatuses(child, reasons, seen);
  }
}

export function classifyRecord(detail) {
  const reasons = new Set();
  for (const field of REQUIRED_FIELDS) {
    if (typeof detail.result[field] !== 'string' || !detail.result[field].trim()) {
      reasons.add(`missing_${field}`);
    }
  }
  for (const warning of detail.warnings ?? []) {
    if (/ambiguous|ambiguity/iu.test(warning)) reasons.add('ambiguity');
    if (/conflict/iu.test(warning)) reasons.add('conflict');
  }
  scanStatuses(detail.fields, reasons);
  scanStatuses(detail.enrichment, reasons);
  if (detail.result.confidence < 0.75) reasons.add('low_confidence');
  const ordered = REASONS.filter((reason) => reasons.has(reason));
  return { attention: ordered.length > 0, reasons: ordered };
}

export function summarizeBatch(state) {
  const summary = {
    received: state.rows.length, processed: 0, no_flags: 0, attention: 0,
    pending: 0, failed: 0,
    reasons: Object.fromEntries(REASONS.map((reason) => [reason, 0]))
  };
  for (const row of state.rows) {
    if (row.state !== 'processed') {
      summary.pending += 1;
      if (row.state === 'failed') summary.failed += 1;
      continue;
    }
    summary.processed += 1;
    const classification = classifyRecord(row);
    if (classification.attention) summary.attention += 1;
    else summary.no_flags += 1;
    for (const reason of classification.reasons) summary.reasons[reason] += 1;
  }
  return summary;
}

export function pageIndexes(state, options = {}) {
  const { filter = 'all', page = 0, pageSize = 100 } = options;
  if (!['all', 'attention'].includes(filter) || !Number.isInteger(pageSize) ||
      pageSize < 1 || pageSize > 100 || !Number.isInteger(page) || page < 0) {
    fail('BATCH_PAGE');
  }
  const matches = [];
  state.rows.forEach((row, index) => {
    if (row.state === 'processed' && (filter === 'all' || classifyRecord(row).attention)) {
      matches.push(index);
    }
  });
  const pageCount = Math.ceil(matches.length / pageSize);
  const selectedPage = pageCount === 0 ? 0 : Math.min(page, pageCount - 1);
  return {
    indexes: matches.slice(selectedPage * pageSize, (selectedPage + 1) * pageSize),
    total: matches.length,
    page: selectedPage,
    pageCount
  };
}

const copyResult = (result) => Object.fromEntries(canonicalFields.map((field) => [field, result[field]]));

export function standardExport(state) {
  if (state.rows.some((row) => row.state !== 'processed')) fail('BATCH_INCOMPLETE');
  return state.rows.map((row) => copyResult(row.result));
}

export function extendedExport(state) {
  const summary = summarizeBatch(state);
  return {
    schema_version: 'jana-enriched/v1',
    catalogs: clone(state.catalogs),
    processing_fingerprint: state.processing_fingerprint,
    complete: summary.processed === summary.received,
    input_count: summary.received,
    processed_count: summary.processed,
    records: state.rows.map((row, row_index) => row.state === 'processed'
      ? { row_index, ...clone(row) }
      : {
          row_index, state: row.state, input: clone(state.inputs[row_index]), result: null,
          enrichment: null, fields: {}, warnings: [], manual_edits: {}
        })
  };
}
