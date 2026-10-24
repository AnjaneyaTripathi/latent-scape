import { defineConfig } from 'vite';
import { createHash } from 'node:crypto';
import { readFile, readdir, writeFile } from 'node:fs/promises';
import { resolve } from 'node:path';

/**
 * Version every byte a cached client can execute or read. The worker itself is
 * excluded because it is the file being rewritten.
 */
export async function fingerprintStaticOutput(output: string): Promise<string> {
  const hash = createHash('sha256');
  const excluded = new Set(['sw.js']);
  const digestDirectory = async (path: string): Promise<void> => {
    const entries = await readdir(path, { withFileTypes: true });
    for (const entry of entries.sort((left, right) => left.name.localeCompare(right.name))) {
      const file = resolve(path, entry.name);
      const relative = file.slice(output.length + 1).replace(/\\/g, '/');
      if (entry.isDirectory()) await digestDirectory(file);
      else if (!excluded.has(relative)) {
        hash.update(relative);
        hash.update(await readFile(file));
      }
    }
  };
  await digestDirectory(output);
  return hash.digest('hex').slice(0, 16);
}

export default defineConfig({
  // GitHub Pages project deployments serve this at /<repo>/.
  base: process.env.GITHUB_ACTIONS ? '/latent-scape/' : '/',
  // Source maps expose implementation comments and source paths to every site
  // reader. Release diagnostics live outside the static artifact instead.
  build: { target: 'es2022', sourcemap: false, assetsInlineLimit: 0 },
  plugins: [{
    name: 'version-service-worker-from-output',
    async closeBundle() {
      const output = resolve('dist');
      const fingerprint = await fingerprintStaticOutput(output);
      const worker = await readFile(resolve(output, 'sw.js'), 'utf8');
      await writeFile(resolve(output, 'sw.js'), worker.replace('__BUILD_FINGERPRINT__', fingerprint));
    },
  }],
});
