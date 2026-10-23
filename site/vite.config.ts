import { defineConfig } from 'vite';
import { createHash } from 'node:crypto';
import { readFile, readdir, writeFile } from 'node:fs/promises';
import { resolve } from 'node:path';

/**
 * Version every byte a cached client can execute or read. The worker itself is
 * excluded because it is the file being rewritten, and report.json is emitted
 * after Vite by the artifact-report step.
 */
export async function fingerprintStaticOutput(output: string): Promise<string> {
  const hash = createHash('sha256');
  const excluded = new Set(['sw.js', 'data/report.json']);
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
  build: { target: 'es2022', sourcemap: true },
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
