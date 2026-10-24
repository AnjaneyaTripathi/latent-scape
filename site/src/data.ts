export type ItemType = 'paper' | 'topic';
export interface SearchDoc { id: string; slug: string; type: ItemType; title: string; abstract?: string; description?: string; venue?: string; year?: number; topicSlugs?: string[]; url?: string; originalUrl?: string; sourceLinkLabel?: string }
export interface Summary {
  tldr: string;
  /** A reader-facing account of the work, authored as 3–5 paragraphs when present. */
  paragraphs?: string[];
  /** The original curated batch uses these fields and is rendered as four paragraphs. */
  problem?: string;
  idea?: string;
  results?: string;
  limitations?: string;
  difficulty?: string;
}
export interface Paper { slug: string; title: string; authors: string[]; year: number; venue: string; abstract: string; originalAbstract?: string; abstractStatus?: string; abstractQuality?: string; url: string; originalUrl?: string; sourceLinkLabel?: string; codeUrl?: string; projectUrl?: string; citationCount?: number | null; references?: string[]; citedBy?: string[]; similarPapers?: string[]; topicSlugs?: string[]; summary?: Summary | null }
export interface Topic { slug: string; title: string; area?: string; broadGroupId?: string; overview?: string; whyItMatters?: string; evolution?: string; openQuestions?: string[]; prerequisites?: string[]; difficulty?: string; paperSlugs?: string[]; relatedTopics?: string[] }
export interface Manifest { snapshot?: string; totalPapers?: number; totalTopics?: number; featuredSlugs?: string[]; shards?: { papers?: Record<string, string>; vectors?: string } }

const base = import.meta.env.BASE_URL;
const json = async <T>(path: string): Promise<T> => {
  const response = await fetch(`${base}data/${path}`);
  if (!response.ok) throw new Error(`Could not load ${path} (${response.status})`);
  return response.json() as Promise<T>;
};

export class AtlasData {
  manifest?: Manifest;
  search: SearchDoc[] = [];
  topics: Topic[] = [];
  private searchLoad?: Promise<SearchDoc[]>;
  private topicsLoad?: Promise<Topic[]>;
  private papers = new Map<string, Paper>();
  private loading = new Map<string, Promise<void>>();

  async initialize() {
    // The full text search index is substantially larger than the shell needed
    // to reach a featured paper. Fetch it only when a reader actually searches.
    this.manifest = await json<Manifest>('manifest.json');
  }
  async ensureSearch() {
    if (this.search.length) return this.search;
    if (!this.searchLoad) {
      this.searchLoad = json<SearchDoc[]>('search.json').then((documents) => {
        this.search = documents;
        return documents;
      }).catch((error: unknown) => {
        this.searchLoad = undefined;
        throw error;
      });
    }
    return this.searchLoad;
  }
  async ensureTopics() {
    if (this.topics.length) return this.topics;
    if (!this.topicsLoad) {
      this.topicsLoad = json<Topic[]>('topics.json').then((topics) => {
        this.topics = topics;
        return topics;
      }).catch((error: unknown) => {
        this.topicsLoad = undefined;
        throw error;
      });
    }
    return this.topicsLoad;
  }
  topic(slug: string) { return this.topics.find((item) => item.slug === slug); }
  async paper(slug: string) {
    if (this.papers.has(slug)) return this.papers.get(slug);
    const filename = this.manifest?.shards?.papers?.[await hashPrefix(slug)];
    if (!filename) return undefined;
    const loading = this.loading.get(filename) || this.loadShard(filename);
    this.loading.set(filename, loading);
    try {
      await loading;
    } catch (error) {
      // A transient offline/cache failure must not leave this shard permanently
      // stuck as a rejected promise when the reader retries the route.
      if (this.loading.get(filename) === loading) this.loading.delete(filename);
      throw error;
    }
    return this.papers.get(slug);
  }
  async papersFor(slugs: string[] | undefined) { return Promise.all((slugs || []).slice(0, 18).map((slug) => this.paper(slug))); }
  async featuredPapers() {
    const wanted = this.manifest?.featuredSlugs || [];
    if (wanted.length) return Promise.all(wanted.map((slug) => this.paper(slug)));
    // Older artifacts lacked explicit featured IDs. Keep them browseable without
    // making current artifacts pay the full search-index cost at startup.
    const documents = await this.ensureSearch();
    return Promise.all(documents.filter((doc) => doc.type === 'paper').slice(0, 6).map((doc) => this.paper(doc.slug)));
  }
  private async loadShard(filename: string) {
    const shard = await json<Paper[]>(filename);
    shard.forEach((paper) => this.papers.set(paper.slug, paper));
  }
}

async function hashPrefix(value: string) {
  const bytes = new TextEncoder().encode(value);
  const digest = await crypto.subtle.digest('SHA-256', bytes);
  return Array.from(new Uint8Array(digest)).slice(0, 1).map((item) => item.toString(16).padStart(2, '0')).join('');
}

export const atlas = new AtlasData();
