export type ItemType = 'paper' | 'topic';
export interface SearchDoc { id: string; slug: string; type: ItemType; title: string; abstract?: string; description?: string; venue?: string; year?: number; topicSlugs?: string[]; url?: string }
export interface Summary { problem: string; idea: string; results: string; limitations: string; tldr: string; difficulty?: string; source?: string; model?: string }
export interface Paper { slug: string; title: string; authors: string[]; year: number; venue: string; abstract: string; url: string; codeUrl?: string; projectUrl?: string; citationCount?: number | null; references?: string[]; citedBy?: string[]; similarPapers?: string[]; topicSlugs?: string[]; summary?: Summary | null }
export interface Topic { slug: string; title: string; area?: string; overview?: string; whyItMatters?: string; evolution?: string; openQuestions?: string[]; prerequisites?: string[]; difficulty?: string; paperSlugs?: string[]; relatedTopics?: string[] }
export interface Manifest { snapshot?: string; totalPapers?: number; totalTopics?: number; coverage?: Record<string, Record<string, number>>; shards?: { papers?: Record<string, string>; vectors?: string } }
export interface CorpusReport { papers?: number; topics?: number; citationMatchRate?: number; sample?: boolean }

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
  private papers = new Map<string, Paper>();
  private loading = new Map<string, Promise<void>>();

  async initialize() {
    const [manifest, search, topics] = await Promise.all([json<Manifest>('manifest.json'), json<SearchDoc[]>('search.json'), json<Topic[]>('topics.json')]);
    this.manifest = manifest; this.search = search; this.topics = topics;
  }
  topic(slug: string) { return this.topics.find((item) => item.slug === slug); }
  async paper(slug: string) {
    if (this.papers.has(slug)) return this.papers.get(slug);
    const filename = this.manifest?.shards?.papers?.[await hashPrefix(slug)];
    if (!filename) return undefined;
    if (!this.loading.has(filename)) this.loading.set(filename, this.loadShard(filename));
    await this.loading.get(filename);
    return this.papers.get(slug);
  }
  async papersFor(slugs: string[] | undefined) { return Promise.all((slugs || []).slice(0, 18).map((slug) => this.paper(slug))); }
  async featuredPapers() {
    const picks = this.search.filter((doc) => doc.type === 'paper').slice(0, 6);
    return Promise.all(picks.map((doc) => this.paper(doc.slug)));
  }
  async report() { return json<CorpusReport>('corpus-report.json'); }
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
