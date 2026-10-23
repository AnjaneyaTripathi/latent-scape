import { afterEach, describe, expect, it, vi } from 'vitest';
import { AtlasData } from './data';

const response = (value: unknown, status = 200) => new Response(JSON.stringify(value), { status });

describe('AtlasData lazy collections', () => {
  afterEach(() => vi.unstubAllGlobals());

  it('loads only the manifest during startup, then shares one search request', async () => {
    const fetchMock = vi.fn(async (input: string | URL | Request) => {
      const path = String(input);
      if (path.endsWith('manifest.json')) return response({ featuredSlugs: [] });
      if (path.endsWith('search.json')) return response([{ id: 'paper-a', slug: 'paper-a', type: 'paper', title: 'A paper' }]);
      throw new Error(`unexpected request: ${path}`);
    });
    vi.stubGlobal('fetch', fetchMock);
    const atlas = new AtlasData();

    await atlas.initialize();
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(String(fetchMock.mock.calls[0][0])).toMatch(/data\/manifest\.json$/);

    const [first, second] = await Promise.all([atlas.ensureSearch(), atlas.ensureSearch()]);
    expect(first).toEqual(second);
    expect(fetchMock.mock.calls.filter(([input]) => String(input).endsWith('search.json'))).toHaveLength(1);
  });

  it('clears a failed lazy request so a later navigation can retry it', async () => {
    let attempts = 0;
    vi.stubGlobal('fetch', vi.fn(async (input: string | URL | Request) => {
      const path = String(input);
      if (path.endsWith('manifest.json')) return response({});
      attempts += 1;
      return attempts === 1 ? response({ error: 'temporary' }, 503) : response([]);
    }));
    const atlas = new AtlasData();
    await atlas.initialize();

    await expect(atlas.ensureSearch()).rejects.toThrow('Could not load search.json (503)');
    await expect(atlas.ensureSearch()).resolves.toEqual([]);
    expect(attempts).toBe(2);
  });

  it('allows a paper shard to be retried after a failed shared load', async () => {
    const slug = 'retry-paper';
    const digest = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(slug));
    const prefix = new Uint8Array(digest)[0].toString(16).padStart(2, '0');
    let attempts = 0;
    vi.stubGlobal('fetch', vi.fn(async () => {
      attempts += 1;
      return attempts === 1 ? response({ error: 'temporary' }, 503) : response([{ slug, title: 'Recovered' }]);
    }));
    const atlas = new AtlasData();
    atlas.manifest = { shards: { papers: { [prefix]: 'papers/retry.json' } } };

    await expect(atlas.paper(slug)).rejects.toThrow('Could not load papers/retry.json (503)');
    await expect(atlas.paper(slug)).resolves.toMatchObject({ slug, title: 'Recovered' });
    expect(attempts).toBe(2);
  });
});
