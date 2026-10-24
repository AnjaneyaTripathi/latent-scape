#!/usr/bin/env node
/**
 * Build a compact, deterministic semantic-neighborhood graph for every topic.
 *
 * The source relationships are the offline topic centroid-neighbor rankings.
 * This script never estimates edges or performs a browser-side layout.
 */
import { readFile, writeFile } from 'node:fs/promises';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const INPUT = resolve(ROOT, 'site/public/data/topics.json');
const OUTPUT = resolve(ROOT, 'site/public/data/topic-graph.json');
const MAX_OVERVIEW = 240;
const GRAPH_WIDTH = 700;
const MIN_GRAPH_HEIGHT = 480;
const DIRECT_START_Y = 48;
const COLUMN_GAP = 16;
const CARD_DIMENSIONS = {
  root: { width: 270, height: 380 },
  neighbor: { width: 270, height: 125 },
};

function excerpt(value = '') {
  const text = String(value).replace(/\s+/g, ' ').trim();
  if (text.length <= MAX_OVERVIEW) return text;
  const boundary = text.lastIndexOf(' ', MAX_OVERVIEW - 1);
  return `${text.slice(0, boundary > MAX_OVERVIEW - 35 ? boundary : MAX_OVERVIEW).trim()}…`;
}

function columnPositions(count, x, height, startY) {
  if (!count) return [];
  return Array.from({ length: count }, (_, index) => ({
    x,
    y: startY + index * (height + COLUMN_GAP),
  }));
}

const source = JSON.parse(await readFile(INPUT, 'utf8'));
if (!Array.isArray(source) || !source.length) throw new Error('topics.json must be a non-empty array');
const indexBySlug = new Map();
source.forEach((topic, index) => {
  if (!topic?.slug || !topic?.title || !Array.isArray(topic?.paperSlugs) || !Array.isArray(topic?.relatedTopics)) {
    throw new Error(`Topic ${index} lacks graph metadata`);
  }
  if (indexBySlug.has(topic.slug)) throw new Error(`Duplicate topic slug: ${topic.slug}`);
  indexBySlug.set(topic.slug, index);
});

const topics = source.map((topic) => ({
  slug: topic.slug,
  title: topic.title,
  area: topic.area || '',
  paperCount: topic.paperSlugs.length,
  overview: excerpt(topic.overview),
  // Indices retain the original outgoing, ranked semantic-neighbor relation.
  relatedTopics: topic.relatedTopics.filter((slug) => indexBySlug.has(slug)).map((slug) => indexBySlug.get(slug)),
}));

function outgoing(index) {
  return topics[index].relatedTopics;
}

const incoming = topics.map(() => []);
topics.forEach((topic, source) => topic.relatedTopics.forEach((target) => incoming[target].push(source)));

function neighborhood(rootTopic) {
  // Preserve the source's ranked outgoing order, then append incoming-only
  // semantic neighbors in a stable order. No relation is invented here.
  const direct = [];
  const directSet = new Set();
  for (const topic of outgoing(rootTopic)) {
    if (topic !== rootTopic && !directSet.has(topic)) {
      direct.push(topic);
      directSet.add(topic);
    }
  }
  const incomingOnly = [...new Set(incoming[rootTopic])]
    .filter((topic) => topic !== rootTopic && !directSet.has(topic))
    .sort((left, right) => topics[left].slug.localeCompare(topics[right].slug));
  direct.push(...incomingOnly);
  // x/y are card top-left positions. Keep the focus in the initial viewport
  // while the complete direct-neighbor column grows downward.
  const rootPosition = { x: 24, y: DIRECT_START_Y };
  const firstPositions = columnPositions(direct.length, 386, CARD_DIMENSIONS.neighbor.height, DIRECT_START_Y);
  const bottoms = [
    rootPosition.y + CARD_DIMENSIONS.root.height,
    ...firstPositions.map(({ y }) => y + CARD_DIMENSIONS.neighbor.height),
  ];
  const bounds = { width: GRAPH_WIDTH, height: Math.max(MIN_GRAPH_HEIGHT, Math.max(...bottoms) + 60) };
  const nodes = [
    { topic: rootTopic, degree: 0, ...rootPosition },
    ...direct.map((topic, index) => ({ topic, degree: 1, ...firstPositions[index] })),
  ];
  // Show exactly the semantic connections to the focus. Relations between
  // neighbors become visible when either neighbor is selected as the focus.
  const edges = direct.map((topic) => ({
    source: Math.min(rootTopic, topic),
    target: Math.max(rootTopic, topic),
    degree: 1,
  }));
  return { rootTopic, bounds, nodes, edges, directNeighborTotal: direct.length };
}

const defaultTopicSlug = indexBySlug.has('topic-080-text-s-2-q-vdit-accurate-quantized-video-diffusi')
  ? 'topic-080-text-s-2-q-vdit-accurate-quantized-video-diffusi'
  : [...topics].sort((left, right) => right.paperCount - left.paperCount || left.slug.localeCompare(right.slug))[0].slug;
const graph = {
  version: 3,
  layoutKind: 'focused-semantic-neighborhoods',
  defaultTopicSlug,
  cardDimensions: CARD_DIMENSIONS,
  topics,
  neighborhoods: topics.map((_, index) => neighborhood(index)),
};
const serialized = `${JSON.stringify(graph)}\n`;
if (Buffer.byteLength(serialized) >= 2_000_000) throw new Error(`Topic graph is too large: ${Buffer.byteLength(serialized)} bytes`);
await writeFile(OUTPUT, serialized);
console.log(JSON.stringify({ output: OUTPUT, bytes: Buffer.byteLength(serialized), topics: topics.length, neighborhoods: graph.neighborhoods.length }));
