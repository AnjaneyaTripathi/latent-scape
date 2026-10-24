#!/usr/bin/env node
/**
 * Build a compact, fixed-layout semantic-neighborhood graph for every topic.
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
const MAX_SECOND_DEGREE = 6;
const BOUNDS = { width: 1040, height: 900 };
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

function columnPositions(count, x, height) {
  if (!count) return [];
  const gap = 16;
  const total = count * height + (count - 1) * gap;
  return Array.from({ length: count }, (_, index) => ({
    x,
    y: Math.round((BOUNDS.height - total) / 2 + index * (height + gap)),
  }));
}

function canonicalEdge(left, right) {
  return left < right ? `${left}:${right}` : `${right}:${left}`;
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

function adjacency(index) {
  return [...new Set([...outgoing(index), ...incoming[index]])];
}

function hasSemanticEdge(left, right) {
  return outgoing(left).includes(right) || outgoing(right).includes(left);
}

function neighborhood(rootTopic) {
  // Degree one is intentionally only the root's outgoing top-five ranking.
  const direct = outgoing(rootTopic).slice(0, 5);
  const directSet = new Set(direct);
  // Excluding all known root adjacency means an incoming-only root relation
  // can never be labelled degree two while still drawing an edge to the root.
  const rootAdjacent = new Set(adjacency(rootTopic));
  const candidates = new Map();
  for (let directPosition = 0; directPosition < direct.length; directPosition += 1) {
    const neighbor = direct[directPosition];
    for (const candidate of adjacency(neighbor)) {
      if (candidate === rootTopic || directSet.has(candidate) || rootAdjacent.has(candidate)) continue;
      const outgoingRank = outgoing(neighbor).indexOf(candidate);
      // An outgoing ranking is the available semantic rank. Incoming-only
      // adjacency remains a known edge but ranks after explicitly ranked ones.
      const sourceRank = outgoingRank === -1 ? 10_000 + candidate : outgoingRank;
      const score = candidates.get(candidate) || { connections: 0, sourceRank, slug: topics[candidate].slug };
      score.connections += 1;
      score.sourceRank = Math.min(score.sourceRank, sourceRank);
      candidates.set(candidate, score);
    }
  }
  const allSecondDegree = [...candidates.keys()].sort((left, right) => {
    const a = candidates.get(left); const b = candidates.get(right);
    return b.connections - a.connections || a.sourceRank - b.sourceRank || a.slug.localeCompare(b.slug);
  });
  const second = allSecondDegree.slice(0, MAX_SECOND_DEGREE);
  const visible = [rootTopic, ...direct, ...second];
  const degreeByTopic = new Map([[rootTopic, 0], ...direct.map((topic) => [topic, 1]), ...second.map((topic) => [topic, 2])]);
  // x/y are card top-left positions, not node centers.
  const rootPosition = { x: 24, y: Math.round((BOUNDS.height - CARD_DIMENSIONS.root.height) / 2) };
  const firstPositions = columnPositions(direct.length, 386, CARD_DIMENSIONS.neighbor.height);
  const secondPositions = columnPositions(second.length, 746, CARD_DIMENSIONS.neighbor.height);
  const nodes = [
    { topic: rootTopic, degree: 0, ...rootPosition },
    ...direct.map((topic, index) => ({ topic, degree: 1, ...firstPositions[index] })),
    ...second.map((topic, index) => ({ topic, degree: 2, ...secondPositions[index] })),
  ];
  const edges = [];
  const seen = new Set();
  for (let left = 0; left < visible.length; left += 1) {
    for (let right = left + 1; right < visible.length; right += 1) {
      const source = visible[left]; const target = visible[right];
      if (!hasSemanticEdge(source, target)) continue;
      const key = canonicalEdge(source, target);
      if (seen.has(key)) continue;
      seen.add(key);
      // A primary edge means root → an outgoing ranked direct neighbor only.
      const [edgeSource, edgeTarget] = source < target ? [source, target] : [target, source];
      const other = source === rootTopic ? target : source;
      edges.push({
        source: edgeSource,
        target: edgeTarget,
        degree: (source === rootTopic || target === rootTopic) && degreeByTopic.get(other) === 1 ? 1 : 2,
      });
    }
  }
  return {
    rootTopic,
    nodes,
    edges,
    directNeighborTotal: rootAdjacent.size,
    secondDegreeTotal: allSecondDegree.length,
  };
}

const defaultTopicSlug = indexBySlug.has('topic-080-text-s-2-q-vdit-accurate-quantized-video-diffusi')
  ? 'topic-080-text-s-2-q-vdit-accurate-quantized-video-diffusi'
  : [...topics].sort((left, right) => right.paperCount - left.paperCount || left.slug.localeCompare(right.slug))[0].slug;
const graph = {
  version: 1,
  layoutKind: 'focused-semantic-neighborhoods',
  defaultTopicSlug,
  bounds: BOUNDS,
  cardDimensions: CARD_DIMENSIONS,
  topics,
  neighborhoods: topics.map((_, index) => neighborhood(index)),
};
const serialized = `${JSON.stringify(graph)}\n`;
if (Buffer.byteLength(serialized) >= 600_000) throw new Error(`Topic graph is too large: ${Buffer.byteLength(serialized)} bytes`);
await writeFile(OUTPUT, serialized);
console.log(JSON.stringify({ output: OUTPUT, bytes: Buffer.byteLength(serialized), topics: topics.length, neighborhoods: graph.neighborhoods.length }));
