#!/usr/bin/env node
/** Project complete published TLDRs into search without rebuilding the corpus. */
import { readFile, writeFile } from 'node:fs/promises';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), '..');
// An optional data directory permits isolated fixture checks without touching the corpus.
const DATA = resolve(process.argv[2] || resolve(ROOT, 'site/public/data'));
const MAX_FILE_BYTES = 100_000_000;
const searchPath = resolve(DATA, 'search.json');
const original = await readFile(searchPath, 'utf8');
const documents = JSON.parse(original);
const manifest = JSON.parse(await readFile(resolve(DATA, 'manifest.json'), 'utf8'));
if (!Array.isArray(documents) || !manifest?.shards?.papers) {
  throw new Error('Expected search documents and a paper-shard manifest');
}

const summaries = new Map();
for (const relative of Object.values(manifest.shards.papers)) {
  if (typeof relative !== 'string' || !/^papers\/[0-9a-f]{2}\.json$/.test(relative)) {
    throw new Error(`Invalid paper shard path: ${relative}`);
  }
  const shard = JSON.parse(await readFile(resolve(DATA, relative), 'utf8'));
  if (!Array.isArray(shard)) throw new Error(`Paper shard is not an array: ${relative}`);
  for (const paper of shard) {
    if (typeof paper?.slug !== 'string' || !paper.slug || summaries.has(paper.slug)) {
      throw new Error(`Invalid or duplicate paper slug: ${paper?.slug}`);
    }
    const tldr = paper.summary?.tldr;
    summaries.set(paper.slug, typeof tldr === 'string' && tldr ? tldr : undefined);
  }
}

let paperCount = 0;
let summaryCount = 0;
let updated = 0;
let removed = 0;
for (const [index, document] of documents.entries()) {
  if (document.type !== 'paper') continue;
  paperCount += 1;
  if (!summaries.has(document.slug)) throw new Error(`Indexed paper missing from shards: ${document.slug}`);
  const tldr = summaries.get(document.slug);
  if (tldr !== undefined) {
    summaryCount += 1;
    if (document.tldr !== tldr) {
      if (Object.hasOwn(document, 'tldr')) {
        document.tldr = tldr;
      } else {
        // Place the new field beside title to minimize changes to pretty JSON.
        const entries = Object.entries(document);
        const titleIndex = entries.findIndex(([key]) => key === 'title');
        entries.splice(titleIndex < 0 ? entries.length : titleIndex, 0, ['tldr', tldr]);
        documents[index] = Object.fromEntries(entries);
      }
      updated += 1;
    }
  } else if (Object.hasOwn(document, 'tldr')) {
    delete document.tldr;
    removed += 1;
  }
}

// Preserve existing formatting on no-op runs. Keep the readable format until the
// asset limit requires compact JSON; no content is shortened to satisfy the limit.
let serialized = updated || removed ? `${JSON.stringify(documents, null, original.includes('\n  {') ? 2 : undefined)}\n` : original;
if (Buffer.byteLength(serialized) >= MAX_FILE_BYTES) serialized = `${JSON.stringify(documents)}\n`;
const beforeBytes = Buffer.byteLength(original);
const afterBytes = Buffer.byteLength(serialized);
if (afterBytes >= MAX_FILE_BYTES) throw new Error(`Search index exceeds ${MAX_FILE_BYTES} bytes: ${afterBytes}`);
if (serialized !== original) await writeFile(searchPath, serialized);
console.log(JSON.stringify({ papers: paperCount, summaries: summaryCount, updated, removed, beforeBytes, afterBytes }));
