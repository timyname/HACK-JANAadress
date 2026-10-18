import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';

const root = new URL('../', import.meta.url);

test('mobile inspector has an explicit close control and inert-state handling', async () => {
  const [html, app] = await Promise.all([
    readFile(new URL('index.html', root), 'utf8'),
    readFile(new URL('app.mjs', root), 'utf8')
  ]);

  assert.match(html, /id="close-inspector"/);
  assert.match(html, /aria-label="Закрыть инспектор"/);
  assert.match(app, /\.inert\s*=/);
  assert.match(app, /aria-hidden/);
});

test('mobile inspector restores focus to the re-rendered trigger row', async () => {
  const app = await readFile(new URL('app.mjs', root), 'utf8');
  assert.match(app, /dataset\.recordIndex/);
  assert.match(app, /querySelector\(`tr\[data-record-index=/);
});

test('mobile inspector activates dialog semantics and isolates the background', async () => {
  const app = await readFile(new URL('app.mjs', root), 'utf8');
  assert.match(app, /setAttribute\('role', 'dialog'\)/);
  assert.match(app, /setAttribute\('aria-modal', 'true'\)/);
  assert.match(app, /setBackgroundInert/);
  assert.match(app, /event\.key === 'Tab'/);
});

test('browser rendering never injects uploaded values through innerHTML', async () => {
  const app = await readFile(new URL('app.mjs', root), 'utf8');
  assert.doesNotMatch(app, /\.innerHTML\s*=/);
});

test('preview copy cannot be mistaken for the final submission runtime', async () => {
  const html = await readFile(new URL('index.html', root), 'utf8');
  assert.match(html, /UI \/ architecture demo · not final submission runtime/i);
  assert.doesNotMatch(html, /system ready|jury shape|05 \/ 05/i);
});

test('hidden file input exposes a visible focus treatment on its label', async () => {
  const [html, css] = await Promise.all([
    readFile(new URL('index.html', root), 'utf8'),
    readFile(new URL('styles.css', root), 'utf8')
  ]);
  assert.match(html, /class="file-action"/);
  assert.match(css, /\.visually-hidden:focus-visible\s*\+\s*\.button--accent/);
});
