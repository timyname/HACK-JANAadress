import { cp, mkdir, rm } from 'node:fs/promises';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const root = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const source = resolve(root, 'site');
const output = resolve(root, 'dist');

await rm(output, { recursive: true, force: true });
await mkdir(resolve(output, 'src'), { recursive: true });

await Promise.all([
  cp(resolve(source, 'index.html'), resolve(output, 'index.html')),
  cp(resolve(source, 'styles.css'), resolve(output, 'styles.css')),
  cp(resolve(source, 'app.mjs'), resolve(output, 'app.mjs')),
  cp(resolve(source, 'src', 'parser.mjs'), resolve(output, 'src', 'parser.mjs')),
  cp(resolve(source, 'src', 'geocoder.mjs'), resolve(output, 'src', 'geocoder.mjs')),
  cp(resolve(source, 'src', 'api.mjs'), resolve(output, 'src', 'api.mjs')),
  cp(resolve(source, 'src', 'ui-state.mjs'), resolve(output, 'src', 'ui-state.mjs'))
]);

console.log('Built static Site into dist/');
