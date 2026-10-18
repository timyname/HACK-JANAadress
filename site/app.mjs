import { detectRuntime, parseViaRuntime } from './src/api.mjs';
import { applyRecordEdit, assertUploadSize, createEditOverlay, parseUpload, reconcileSelectedIndex, toDownloadJson } from './src/ui-state.mjs';

const demoInput = Object.freeze([
  { id: 'demo-001', raw_address: 'Астана манглик ел 47 квартира 57' },
  { id: 'demo-002', raw_address: 'Шымкент, улица Арман, 14Б дом, 2 подъезд, 5 этаж' },
  { id: 'demo-003', raw_address: 'Караганда, мкр Восток 3, дом 9, квартира 27' },
  { id: 'demo-004', raw_address: 'Алматы, улица Самал, дом 19, магазин Орбита' },
  { id: 'demo-005', raw_address: 'район Сарыарка, неизвестный адресный фрагмент' }
]);

const editableFields = [
  'province', 'city', 'district', 'living_area', 'street_type', 'street_name',
  'house', 'apartment', 'entrance', 'floor', 'landmark'
];

const state = {
  records: [],
  explanations: [],
  runtime: { mode: 'browser' },
  busy: false,
  edits: new Map(),
  selected: 0,
  filter: 'all'
};

const elements = {
  body: document.querySelector('#records-body'),
  empty: document.querySelector('#empty-state'),
  total: document.querySelector('#metric-total'),
  high: document.querySelector('#metric-high'),
  review: document.querySelector('#metric-review'),
  filterAll: document.querySelector('#filter-all'),
  filterReview: document.querySelector('#filter-review'),
  notice: document.querySelector('#notice'),
  fileInput: document.querySelector('#file-input'),
  demoButton: document.querySelector('#demo-button'),
  exportButton: document.querySelector('#export-button'),
  exportOriginalButton: document.querySelector('#export-original-button'),
  inspector: document.querySelector('#inspector'),
  closeInspector: document.querySelector('#close-inspector'),
  recordId: document.querySelector('#record-id'),
  rawAddress: document.querySelector('#raw-address'),
  confidence: document.querySelector('#record-confidence'),
  editor: document.querySelector('#editor-form'),
  saveButton: document.querySelector('#save-button')
};

const backgroundElements = [
  document.querySelector('.command-bar'),
  document.querySelector('.process-rail'),
  document.querySelector('.dashboard'),
  document.querySelector('.footer')
].filter(Boolean);

const mobileInspector = window.matchMedia('(max-width: 1060px)');
let lastInspectorTriggerIndex = null;

function scoreLabel(value) {
  return `${Math.round((value ?? 0) * 100)}%`;
}

function needsReview(record) {
  return record.confidence < 0.75;
}

function currentRecord(index) {
  const original = state.records[index];
  return original ? applyRecordEdit(original, state.edits.get(index) ?? {}) : null;
}

function currentRecords() {
  return state.records.map((_, index) => currentRecord(index));
}

function reconcileSelection() {
  state.selected = reconcileSelectedIndex(currentRecords(), state.selected, state.filter);
}

function visibleRecords() {
  return state.records
    .map((_, index) => ({ record: currentRecord(index), index }))
    .filter(({ record }) => state.filter === 'all' || needsReview(record));
}

function setNotice(message, error = false) {
  elements.notice.classList.toggle('is-error', error);
  const label = document.createElement('span');
  label.textContent = error ? 'CHECK INPUT' : (state.runtime.mode === 'api' ? 'SERVER MODE' : 'LOCAL MODE');
  elements.notice.replaceChildren(label, document.createTextNode(message));
}

function renderMetrics() {
  const high = currentRecords().filter((record) => !needsReview(record)).length;
  const review = state.records.length - high;
  elements.total.textContent = String(state.records.length);
  elements.high.textContent = String(high);
  elements.review.textContent = String(review);
  elements.filterAll.textContent = String(state.records.length);
  elements.filterReview.textContent = String(review);
  elements.exportButton.disabled = state.records.length === 0;
  elements.exportOriginalButton.disabled = state.records.length === 0;
}

function renderQueue() {
  const rows = visibleRecords();
  elements.body.replaceChildren();
  elements.empty.hidden = rows.length > 0;

  for (const { record, index } of rows) {
    const row = document.createElement('tr');
    row.tabIndex = 0;
    row.dataset.recordIndex = String(index);
    row.classList.toggle('is-selected', index === state.selected);
    row.setAttribute('aria-label', `Открыть запись ${record.id}`);
    const low = needsReview(record);
    const signalCell = document.createElement('td');
    const signal = document.createElement('span');
    signal.className = `signal-meter${low ? ' is-low' : ''}`;
    signal.style.setProperty('--signal-width', `${Math.round(record.confidence * 100)}%`);
    const signalValue = document.createElement('strong');
    signalValue.textContent = scoreLabel(record.confidence);
    signal.append(signalValue, document.createElement('i'));
    signalCell.append(signal);

    const values = [
      record.raw_address,
      record.city ?? 'Не определён',
      [record.street_type, record.street_name].filter(Boolean).join(' ') || '—',
      record.house ?? '—',
      record.apartment ?? '—'
    ];
    const cells = values.map((value, cellIndex) => {
      const cell = document.createElement('td');
      if (cellIndex === 0) cell.className = 'raw-cell';
      if (cellIndex === 1) cell.className = 'muted-cell';
      cell.textContent = value;
      return cell;
    });
    const actionCell = document.createElement('td');
    const action = document.createElement('button');
    action.className = 'open-record';
    action.type = 'button';
    action.setAttribute('aria-label', `Открыть ${record.id}`);
    action.textContent = '↗';
    actionCell.append(action);
    row.append(signalCell, ...cells, actionCell);

    const open = () => selectRecord(index, true);
    row.addEventListener('click', open);
    row.addEventListener('keydown', (event) => {
      if (event.key === 'Enter' || event.key === ' ') {
        event.preventDefault();
        open();
      }
    });
    elements.body.append(row);
  }
}

function renderInspector() {
  const record = currentRecord(state.selected);
  elements.saveButton.disabled = !record;
  for (const field of editableFields) {
    elements.editor.elements[field].disabled = !record;
    elements.editor.elements[field].value = record?.[field] ?? '';
    const input = elements.editor.elements[field];
    let badge = input.parentElement.querySelector('.field-source');
    if (!badge) {
      badge = document.createElement('small');
      badge.className = 'field-source';
      input.parentElement.append(badge);
    }
    const detail = state.explanations[state.selected]?.fields?.[field];
    const manual = Object.hasOwn(state.edits.get(state.selected) ?? {}, field);
    const source = manual ? 'manual' : (detail?.source ?? (record?.[field] == null ? 'unknown' : 'heuristic'));
    badge.dataset.source = source;
    badge.textContent = ({ text: 'Из текста', gazetteer: 'По справочнику', manual: 'Правка оператора', heuristic: 'Эвристика', unknown: 'Не определено' })[source] ?? 'Не определено';
    badge.title = detail?.span ? `Позиции в исходной строке: ${detail.span[0]}–${detail.span[1]}` : '';
  }
  if (!record) {
    elements.recordId.textContent = 'Нет записи';
    elements.rawAddress.textContent = 'Выберите запись в очереди';
    elements.confidence.textContent = '—';
    elements.confidence.classList.remove('is-low');
    return;
  }
  elements.recordId.textContent = record.id;
  elements.rawAddress.textContent = record.raw_address;
  elements.confidence.textContent = scoreLabel(record.confidence);
  elements.confidence.classList.toggle('is-low', needsReview(record));
}

function render() {
  renderMetrics();
  renderQueue();
  renderInspector();
}

function syncInspectorA11y() {
  const openAsDialog = mobileInspector.matches && elements.inspector.classList.contains('is-open');
  const hidden = mobileInspector.matches && !openAsDialog;
  elements.inspector.inert = hidden;
  if (hidden) {
    elements.inspector.setAttribute('aria-hidden', 'true');
    elements.inspector.setAttribute('inert', '');
  } else {
    elements.inspector.removeAttribute('aria-hidden');
    elements.inspector.removeAttribute('inert');
  }
  if (openAsDialog) {
    elements.inspector.setAttribute('role', 'dialog');
    elements.inspector.setAttribute('aria-modal', 'true');
  } else {
    elements.inspector.removeAttribute('role');
    elements.inspector.removeAttribute('aria-modal');
  }
  setBackgroundInert(openAsDialog);
}

function setBackgroundInert(active) {
  for (const element of backgroundElements) {
    element.inert = active;
    if (active) element.setAttribute('inert', '');
    else element.removeAttribute('inert');
  }
}

function closeInspector({ restoreFocus = true } = {}) {
  elements.inspector.classList.remove('is-open');
  syncInspectorA11y();
  if (restoreFocus && lastInspectorTriggerIndex !== null) {
    elements.body.querySelector(`tr[data-record-index="${lastInspectorTriggerIndex}"]`)?.focus();
  }
}

function selectRecord(index, openInspector = false) {
  state.selected = index;
  renderQueue();
  renderInspector();
  if (openInspector && mobileInspector.matches) {
    lastInspectorTriggerIndex = index;
    elements.inspector.classList.add('is-open');
    syncInspectorA11y();
    elements.closeInspector.focus();
  }
}

async function loadBatch(input, message) {
  if (state.busy) return;
  setBusy(true);
  setNotice(`Обработка ${input.length} записей…`);
  let payload;
  try {
    payload = await parseViaRuntime(input, state.runtime);
  } catch (error) {
    setNotice(error instanceof Error ? error.message : 'Сервис недоступен', true);
    return;
  } finally {
    setBusy(false);
  }
  state.records = payload.records.map(item => Object.freeze(item.result));
  state.explanations = payload.records;
  state.edits.clear();
  state.selected = 0;
  state.filter = 'all';
  document.querySelectorAll('.filter-tab').forEach((tab) => {
    tab.classList.toggle('is-active', tab.dataset.filter === 'all');
  });
  const elapsed = Number.isFinite(payload.meta?.seconds) ? ` ${payload.meta.seconds.toFixed(3)} с.` : '';
  setNotice(message + elapsed);
  render();
}

function setBusy(busy) {
  state.busy = busy;
  elements.demoButton.disabled = busy;
  elements.fileInput.disabled = busy;
  document.querySelector('.workbench').setAttribute('aria-busy', String(busy));
}

elements.demoButton.addEventListener('click', () => {
  loadBatch(demoInput, 'Загружен демонстрационный пакет синтетических адресов.');
});

elements.fileInput.addEventListener('change', async () => {
  const [file] = elements.fileInput.files;
  if (!file) return;
  try {
    assertUploadSize(file.size);
    const text = await file.text();
    const input = parseUpload(text);
    await loadBatch(input, `Обработано записей: ${input.length}. ${state.runtime.mode === 'api' ? 'Сервис не сохраняет загруженный пакет.' : 'Файл остался в памяти браузера.'}`);
  } catch (error) {
    setNotice(error instanceof Error ? error.message : 'Не удалось прочитать JSON', true);
  } finally {
    elements.fileInput.value = '';
  }
});

document.querySelectorAll('.filter-tab').forEach((tab) => {
  tab.addEventListener('click', () => {
    state.filter = tab.dataset.filter;
    reconcileSelection();
    document.querySelectorAll('.filter-tab').forEach((item) => item.classList.toggle('is-active', item === tab));
    renderQueue();
    renderInspector();
  });
});

elements.editor.addEventListener('submit', (event) => {
  event.preventDefault();
  const record = state.records[state.selected];
  if (!record) return;
  const formData = new FormData(elements.editor);
  const patch = Object.fromEntries(editableFields.map((field) => [field, formData.get(field)?.toString() ?? '']));
  const overlay = createEditOverlay(patch);
  state.edits.set(state.selected, Object.fromEntries(Object.entries(overlay).filter(([key, value]) => value !== record[key])));
  reconcileSelection();
  setNotice(`Корректировка ${record.id} сохранена локально. Исходная строка не изменена.`);
  render();
});

function downloadRecords(records, filename) {
  if (!state.records.length) return;
  const blob = new Blob([toDownloadJson(records)], { type: 'application/json;charset=utf-8' });
  const url = URL.createObjectURL(blob);
  const link = document.createElement('a');
  link.href = url;
  link.download = filename;
  link.click();
  URL.revokeObjectURL(url);
  setNotice(`Экспортировано записей: ${state.records.length}. ${filename}`);
}

elements.exportButton.addEventListener('click', () => downloadRecords(currentRecords(), 'jana-address-edited.json'));
elements.exportOriginalButton.addEventListener('click', () => downloadRecords(state.records, 'jana-address-output.json'));

document.addEventListener('keydown', (event) => {
  if (event.key === 'Escape') closeInspector();
  if (event.key === 'Tab' && mobileInspector.matches && elements.inspector.classList.contains('is-open')) {
    const focusable = [...elements.inspector.querySelectorAll('button:not([disabled]), input:not([disabled])')]
      .filter((element) => element.getClientRects().length > 0);
    const first = focusable[0];
    const last = focusable.at(-1);
    if (event.shiftKey && (document.activeElement === first || !elements.inspector.contains(document.activeElement))) {
      event.preventDefault();
      last?.focus();
    } else if (!event.shiftKey && document.activeElement === last) {
      event.preventDefault();
      first?.focus();
    }
  }
  if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 's' && !elements.saveButton.disabled) {
    event.preventDefault();
    elements.editor.requestSubmit();
  }
});

elements.closeInspector.addEventListener('click', () => closeInspector());
mobileInspector.addEventListener('change', () => {
  if (!mobileInspector.matches) elements.inspector.classList.remove('is-open');
  syncInspectorA11y();
});

syncInspectorA11y();
setBusy(true);
state.runtime = await detectRuntime();
setBusy(false);
if (state.runtime.mode === 'api') {
  document.querySelector('#runtime-chip').textContent = 'сервер подключён';
  document.querySelector('#catalog-chip').textContent = 'КАТО / OpenStreetMap';
  document.querySelector('#ready-chip').textContent = 'готов к обработке';
  document.querySelector('#runtime-mode').textContent = 'API';
  document.querySelector('#runtime-caption').textContent = 'локальный справочник';
  document.querySelector('#data-boundary').textContent = 'Обработка на этом сервере';
  document.querySelector('#runtime-description').textContent = 'Адреса отправляются сервису этого сайта. Пакет не сохраняется. Внешние геокодеры не вызываются.';
  document.querySelector('#runtime-footer').textContent = 'Jana Address · offline parser + gazetteer';
  const attribution = document.querySelector('#data-attribution');
  const link = document.createElement('a');
  link.href = 'https://www.openstreetmap.org/copyright';
  link.textContent = '© OpenStreetMap contributors · ODbL';
  attribution.replaceChildren(link);
} else {
  document.querySelector('#ready-chip').textContent = 'демо готово';
  elements.exportOriginalButton.textContent = 'Без правок';
}
await loadBatch(demoInput, state.runtime.mode === 'api'
  ? 'Сервис подключён. Синтетический демо-пакет обработан сервером.'
  : 'Демонстрационный режим браузера. Серверный справочник здесь недоступен.');
