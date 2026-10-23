/// <reference lib="webworker" />

import { env, pipeline } from '@huggingface/transformers';

type VectorIndex = {
  dimensions: number;
  scale: number;
  ids: string[];
  /** Raw, row-major signed bytes. Legacy nested number arrays are also accepted. */
  vectors: string | number[] | number[][];
};

type ShardedVectorIndex = {
  dimensions: number;
  scale: number;
  shards: Array<{ path: string; ids: string[] }>;
};
type SemanticIndex = VectorIndex | ShardedVectorIndex;
type Topic = { slug: string; paperSlugs?: string[] };

type SearchRequest = { type: 'search'; requestId: number; query: string; topK: number; indexUrl: string; topicsUrl: string };
type SearchResponse =
  | { type: 'ready'; requestId: number; result: Array<{ id: string; score: number }> }
  | { type: 'error'; requestId: number; error: string };

declare const self: DedicatedWorkerGlobalScope;

// All model execution remains same-origin. Vite copies the wasm runtime through
// scripts/copy-transformers-assets.mjs before a production build.
const deploymentBase = new URL(import.meta.env.BASE_URL, self.location.origin).toString();
env.allowRemoteModels = false;
env.allowLocalModels = true;
env.localModelPath = new URL('models/', deploymentBase).toString();
if (env.backends.onnx.wasm) {
  env.backends.onnx.wasm.wasmPaths = new URL('wasm/', deploymentBase).toString();
}

let extractorPromise: Promise<any> | undefined;
let indexPromise: Promise<{ metadata: SemanticIndex; shards: Array<{ ids: string[]; values: Int8Array }> }> | undefined;
let indexKey = '';

async function extractor() {
  if (!extractorPromise) {
    extractorPromise = pipeline('feature-extraction', 'Xenova/all-MiniLM-L6-v2', {
      dtype: 'q8',
    });
  }
  return extractorPromise;
}

function flattenLegacyVectors(vectors: number[] | number[][]): Int8Array {
  const values = Array.isArray(vectors[0]) ? (vectors as number[][]).flat() : (vectors as number[]);
  return Int8Array.from(values);
}

async function loadIndex(url: string, topicsUrl: string) {
  const key = `${url}\u0000${topicsUrl}`;
  if (!indexPromise || indexKey !== key) {
    indexKey = key;
    indexPromise = (async () => {
      const response = await fetch(url);
      if (!response.ok) throw new Error(`Could not load semantic index (${response.status})`);
      const metadata = (await response.json()) as SemanticIndex;
      if (!metadata.dimensions || !metadata.scale) {
        throw new Error('Semantic index has missing dimensions, scale, or ids');
      }
      if ('shards' in metadata) {
        if (metadata.shards.length === 0) throw new Error('Semantic index has no shards');
        const shards = await Promise.all(metadata.shards.map(async (shard) => {
          const response = await fetch(new URL(shard.path, url));
          if (!response.ok) throw new Error(`Could not load semantic vector shard (${response.status})`);
          const values = new Int8Array(await response.arrayBuffer());
          if (values.byteLength !== shard.ids.length * metadata.dimensions) {
            throw new Error('Semantic shard byte length does not match its row metadata');
          }
          return { ids: shard.ids, values };
        }));
        return { metadata, shards };
      }
      if (metadata.ids.length === 0) throw new Error('Semantic index has no ids');
      const values = typeof metadata.vectors === 'string'
        ? new Int8Array(await (await fetch(new URL(metadata.vectors, url))).arrayBuffer())
        : flattenLegacyVectors(metadata.vectors);
      if (values.byteLength !== metadata.ids.length * metadata.dimensions) {
        throw new Error('Semantic index byte length does not match its row metadata');
      }
      return { metadata, shards: [{ ids: metadata.ids, values }] };
    })();
  }
  return indexPromise;
}

function insertTop(
  top: Array<{ id: string; score: number }>, candidate: { id: string; score: number }, limit: number,
) {
  if (top.length === limit && candidate.score <= top[top.length - 1].score) return;
  let at = top.length;
  while (at > 0 && candidate.score > top[at - 1].score) at--;
  top.splice(at, 0, candidate);
  if (top.length > limit) top.pop();
}

async function search(query: string, topK: number, url: string, topicsUrl: string) {
  const [featureExtractor, index, topics] = await Promise.all([
    extractor(), loadIndex(url, topicsUrl), fetch(topicsUrl).then(async (response) => {
      if (!response.ok) throw new Error(`Could not load topics for semantic search (${response.status})`);
      return response.json() as Promise<Topic[]>;
    }),
  ]);
  const output = await featureExtractor(query, {
    pooling: 'mean', normalize: true, truncation: true, max_length: 256,
  });
  const queryVector = output.data as Float32Array;
  const { metadata, shards } = index;
  if (queryVector.length !== metadata.dimensions) {
    throw new Error(`Model output dimensions ${queryVector.length} do not match index ${metadata.dimensions}`);
  }
  const paperCount = shards.reduce((total, shard) => total + shard.ids.length, 0);
  const limit = Math.max(1, Math.min(Math.floor(topK), paperCount));
  // Reserve a few semantic results for concepts. Otherwise paper results often
  // dominate even when the user asks a broad, title-free conceptual question.
  const paperLimit = Math.max(1, Math.ceil(limit * 0.75));
  const topicLimit = Math.max(1, limit - paperLimit);
  const results: Array<{ id: string; score: number }> = [];
  const inverseScale = 1 / metadata.scale;
  const paperScores = new Map<string, number>();
  for (const shard of shards) {
    for (let row = 0; row < shard.ids.length; row++) {
      const offset = row * metadata.dimensions;
      let score = 0;
      for (let dimension = 0; dimension < metadata.dimensions; dimension++) {
        score += queryVector[dimension] * shard.values[offset + dimension] * inverseScale;
      }
      paperScores.set(shard.ids[row], score);
      insertTop(results, { id: shard.ids[row], score }, paperLimit);
    }
  }
  // A topic's semantic score is the mean score of its member papers. This is the
  // query dot product with its (unnormalized) member-vector centroid, and lets
  // concept queries surface topics even when their words never occur in a title.
  const topicResults: Array<{ id: string; score: number }> = [];
  for (const topic of topics) {
    const scores = (topic.paperSlugs ?? []).map((slug) => paperScores.get(slug)).filter((score): score is number => score !== undefined);
    if (scores.length === 0) continue;
    const score = scores.reduce((total, value) => total + value, 0) / scores.length;
    insertTop(topicResults, { id: `topic:${topic.slug}`, score }, topicLimit);
  }
  return [...results, ...topicResults].sort((left, right) => right.score - left.score);
}

self.onmessage = async (event: MessageEvent<SearchRequest>) => {
  const request = event.data;
  if (request.type !== 'search') return;
  try {
    const result = await search(request.query, request.topK, request.indexUrl, request.topicsUrl);
    self.postMessage({ type: 'ready', requestId: request.requestId, result } satisfies SearchResponse);
  } catch (error) {
    self.postMessage({
      type: 'error', requestId: request.requestId,
      error: error instanceof Error ? error.message : String(error),
    } satisfies SearchResponse);
  }
};

