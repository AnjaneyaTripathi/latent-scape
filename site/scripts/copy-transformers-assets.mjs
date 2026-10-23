/** Copy ONNX Runtime WASM files into the static site for fully offline browser search. */
import { cp, mkdir, readdir, rm } from 'node:fs/promises';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const siteRoot = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const source = resolve(siteRoot, 'node_modules/onnxruntime-web/dist');
const destination = resolve(siteRoot, 'public/wasm');
const files = (await readdir(source)).filter((name) => /^ort-wasm.*\.(?:wasm|mjs)$/.test(name));
if (files.length === 0) throw new Error(`No ONNX Runtime WASM assets found in ${source}`);
await rm(destination, { recursive: true, force: true });
await mkdir(destination, { recursive: true });
await Promise.all(files.map((name) => cp(resolve(source, name), resolve(destination, name))));
console.log(`Copied ${files.length} local ONNX Runtime assets to public/wasm`);
