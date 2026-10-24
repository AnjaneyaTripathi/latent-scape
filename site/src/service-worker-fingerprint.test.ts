import { mkdtemp, mkdir, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { afterEach, describe, expect, it } from 'vitest';
import { fingerprintStaticOutput } from '../vite.config';

const roots: string[] = [];

afterEach(async () => {
  await Promise.all(roots.splice(0).map((root) => rm(root, { recursive: true, force: true })));
});

describe('service-worker cache fingerprint', () => {
  it('changes when shipped model bytes change but ignores its own output', async () => {
    const root = await mkdtemp(join(tmpdir(), 'latentscape-fingerprint-'));
    roots.push(root);
    await Promise.all([
      mkdir(join(root, 'models'), { recursive: true }),
      mkdir(join(root, 'wasm'), { recursive: true }),
      mkdir(join(root, 'data'), { recursive: true }),
    ]);
    await Promise.all([
      writeFile(join(root, 'index.html'), '<main />'),
      writeFile(join(root, 'models', 'model.onnx'), 'model-a'),
      writeFile(join(root, 'wasm', 'runtime.wasm'), 'runtime-a'),
      writeFile(join(root, 'data', 'manifest.json'), '{}'),
      writeFile(join(root, 'sw.js'), 'latentscape-static-__BUILD_FINGERPRINT__'),
    ]);

    const baseline = await fingerprintStaticOutput(root);
    await writeFile(join(root, 'sw.js'), 'latentscape-static-different');
    expect(await fingerprintStaticOutput(root)).toBe(baseline);

    await writeFile(join(root, 'models', 'model.onnx'), 'model-b');
    expect(await fingerprintStaticOutput(root)).not.toBe(baseline);
  });
});
