import { defineConfig } from 'vite';
import { createHash } from 'node:crypto';
import { readFile, readdir, writeFile } from 'node:fs/promises';
import { resolve } from 'node:path';

export default defineConfig({
  // GitHub Pages project deployments serve this at /<repo>/.
  base: process.env.GITHUB_ACTIONS ? '/latent-scape/' : '/',
  build: { target: 'es2022', sourcemap: true },
  plugins: [{
    name: 'version-service-worker-from-output',
    async closeBundle() {
      const output = resolve('dist');
      const hash = createHash('sha256');
      const digestDirectory = async (path: string): Promise<void> => {
        for (const entry of await readdir(path, { withFileTypes: true })) {
          const file = resolve(path, entry.name);
          if (entry.isDirectory()) await digestDirectory(file);
          else { hash.update(file.slice(output.length)); hash.update(await readFile(file)); }
        }
      };
      // Data and hashed Vite entry chunks together identify exactly what a client
      // needs to cache. Updating either changes the service-worker script too.
      await digestDirectory(resolve(output, 'data'));
      await digestDirectory(resolve(output, 'assets'));
      const fingerprint = hash.digest('hex').slice(0, 16);
      const worker = await readFile(resolve(output, 'sw.js'), 'utf8');
      await writeFile(resolve(output, 'sw.js'), worker.replace('__BUILD_FINGERPRINT__', fingerprint));
    },
  }],
});
