#!/usr/bin/env node
/** Build reader-sized, complete paper lists for every sanitized topic. */
import { mkdir, readFile, readdir, unlink, writeFile } from 'node:fs/promises';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const DATA = resolve(ROOT, 'site/public/data');
const TOPICS_PATH = resolve(DATA, 'topics.json');
const MANIFEST_PATH = resolve(DATA, 'manifest.json');
const OUTPUT = resolve(DATA, 'topic-papers');
const MAX_FILE_BYTES = 750_000;
const SAFE_TOPIC_SLUG = /^[a-z0-9][a-z0-9-]*$/;

const topics = JSON.parse(await readFile(TOPICS_PATH, 'utf8'));
const manifest = JSON.parse(await readFile(MANIFEST_PATH, 'utf8'));
if (!Array.isArray(topics) || !topics.length || !manifest?.shards?.papers) {
  throw new Error('Expected sanitized topics and paper-shard manifest');
}
const topicBySlug = new Map();
const membership = new Map();
for (const [index, topic] of topics.entries()) {
  if (!SAFE_TOPIC_SLUG.test(topic?.slug || '') || topicBySlug.has(topic.slug) || !Array.isArray(topic.paperSlugs)) {
    throw new Error(`Invalid topic metadata at index ${index}`);
  }
  topicBySlug.set(topic.slug, { ...topic, index });
  for (const paperSlug of topic.paperSlugs) {
    if (membership.has(paperSlug)) throw new Error(`Paper belongs to more than one topic: ${paperSlug}`);
    membership.set(paperSlug, index);
  }
}

// Only projected list rows are retained while shards are streamed one at a time.
const papersBySlug = new Map();
for (const relative of Object.values(manifest.shards.papers)) {
  if (typeof relative !== 'string' || !/^papers\/[0-9a-f]{2}\.json$/.test(relative)) {
    throw new Error(`Invalid paper shard path: ${relative}`);
  }
  const shard = JSON.parse(await readFile(resolve(DATA, relative), 'utf8'));
  if (!Array.isArray(shard)) throw new Error(`Paper shard is not an array: ${relative}`);
  for (const paper of shard) {
    if (!membership.has(paper?.slug)) continue;
    if (papersBySlug.has(paper.slug)) throw new Error(`Duplicate paper slug in shards: ${paper.slug}`);
    const sourceUrl = paper.originalUrl || paper.url;
    if (typeof sourceUrl !== 'string' || !sourceUrl || typeof paper.sourceLinkLabel !== 'string' || !paper.sourceLinkLabel) {
      throw new Error(`Paper lacks reader source metadata: ${paper.slug}`);
    }
    const row = {
      slug: paper.slug,
      title: paper.title,
      authors: Array.isArray(paper.authors) ? paper.authors : [],
      year: paper.year,
      venue: paper.venue,
      sourceUrl,
      sourceLinkLabel: paper.sourceLinkLabel,
    };
    if (typeof paper.summary?.tldr === 'string' && paper.summary.tldr) row.tldr = paper.summary.tldr;
    papersBySlug.set(paper.slug, row);
  }
}

await mkdir(OUTPUT, { recursive: true });
const expectedFiles = new Set(topics.map((topic) => `${topic.slug}.json`));
for (const entry of await readdir(OUTPUT, { withFileTypes: true })) {
  if (entry.isFile() && entry.name.endsWith('.json') && !expectedFiles.has(entry.name)) {
    await unlink(resolve(OUTPUT, entry.name));
  }
}

let totalBytes = 0;
let largest = { slug: '', bytes: 0 };
let defaultBytes = 0;
for (const topic of topics) {
  const papers = topic.paperSlugs.map((slug) => papersBySlug.get(slug));
  if (papers.some((paper) => !paper)) {
    const missing = topic.paperSlugs.filter((slug) => !papersBySlug.has(slug));
    throw new Error(`Topic paper membership is incomplete for ${topic.slug}: ${missing.slice(0, 3).join(', ')}`);
  }
  const relatedTopics = (topic.relatedTopics || []).map((slug) => topicBySlug.get(slug)).filter(Boolean).map((related) => ({
    slug: related.slug, title: related.title, paperCount: related.paperSlugs.length,
  }));
  const prerequisites = (topic.prerequisites || []).map((slug) => topicBySlug.get(slug)).filter(Boolean).map((prerequisite) => ({
    slug: prerequisite.slug, title: prerequisite.title,
  }));
  if (relatedTopics.length !== (topic.relatedTopics || []).length || prerequisites.length !== (topic.prerequisites || []).length) {
    throw new Error(`Topic links reference unknown topics: ${topic.slug}`);
  }
  const output = {
    version: 1,
    topic: {
      slug: topic.slug,
      title: topic.title,
      area: topic.area,
      overview: topic.overview,
      whyItMatters: topic.whyItMatters,
      evolution: topic.evolution,
      openQuestions: topic.openQuestions,
      difficulty: topic.difficulty,
      paperCount: topic.paperSlugs.length,
    },
    relatedTopics,
    prerequisites,
    papers,
  };
  const serialized = `${JSON.stringify(output)}\n`;
  const bytes = Buffer.byteLength(serialized);
  if (bytes > MAX_FILE_BYTES) throw new Error(`Topic list exceeds ${MAX_FILE_BYTES} bytes: ${topic.slug} (${bytes})`);
  await writeFile(resolve(OUTPUT, `${topic.slug}.json`), serialized);
  totalBytes += bytes;
  if (bytes > largest.bytes) largest = { slug: topic.slug, bytes };
  if (topic.slug.startsWith('topic-080-')) defaultBytes = bytes;
}
console.log(JSON.stringify({ topics: topics.length, totalBytes, largest, defaultTopic080Bytes: defaultBytes }));
