import MiniSearch from 'minisearch';
import type { SearchDoc } from './data';

export interface SearchResult { item: SearchDoc; score: number }
let engine: MiniSearch<SearchDoc> | undefined;
export let lastSemanticNotice = '';

export function configureSearch(documents: SearchDoc[]) {
  engine = new MiniSearch<SearchDoc>({ fields: ['title', 'abstract', 'description', 'venue'], storeFields: ['slug', 'type', 'title', 'abstract', 'description', 'venue', 'year', 'topicSlugs', 'url', 'originalUrl', 'sourceLinkLabel'], searchOptions: { prefix: true, fuzzy: 0.2, boost: { title: 4 } } });
  engine.addAll(documents);
}

export function keywordSearch(query: string) {
  return engine?.search(query).map((result, index) => ({ item: { id: result.id, slug: result.slug, type: result.type, title: result.title, abstract: result.abstract, description: result.description, venue: result.venue, year: result.year, topicSlugs: result.topicSlugs, url: result.url, originalUrl: result.originalUrl, sourceLinkLabel: result.sourceLinkLabel } as SearchDoc, score: 1 / (60 + index + 1) })) || [];
}

export async function search(query: string, documents: SearchDoc[]) {
  const keyword = keywordSearch(query).map((result) => result.item);
  let semantic: Array<{ slug: string; score: number }> = [];
  lastSemanticNotice = '';
  try { semantic = await semanticMatches(query); } catch { lastSemanticNotice = 'Semantic search is unavailable for this query; these are keyword matches.'; }
  // Reciprocal-rank fusion makes the lexical and cosine rankings comparable without
  // pretending their raw scores share a scale.
  const results = new Map<string, SearchResult>();
  keyword.forEach((item, index) => results.set(item.slug, { item, score: 1 / (60 + index + 1) }));
  semantic.forEach(({ slug }, index) => {
    const normalSlug = slug.startsWith('topic:') ? slug.slice(6) : slug;
    const item = documents.find((document) => document.slug === normalSlug);
    if (!item) return;
    const existing = results.get(normalSlug);
    results.set(normalSlug, { item, score: (existing?.score || 0) + 1 / (60 + index + 1) });
  });
  return [...results.values()].sort((a, b) => b.score - a.score).slice(0, 40);
}

type SemanticBridge = (query: string) => Promise<Array<{ slug: string; score: number }>>;
declare global { interface Window { latentscapeSemanticSearch?: SemanticBridge } }
async function semanticMatches(query: string) { return window.latentscapeSemanticSearch ? window.latentscapeSemanticSearch(query) : []; }
