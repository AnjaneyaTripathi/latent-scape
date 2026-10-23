import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

class MockWorker {
  static instances: MockWorker[] = [];
  onmessage: ((event: MessageEvent) => void) | null = null;
  onerror: ((event: ErrorEvent) => void) | null = null;
  postMessage = vi.fn();
  terminate = vi.fn();

  constructor() {
    MockWorker.instances.push(this);
  }
}

describe('SemanticSearch worker recovery', () => {
  let SemanticSearch: (typeof import('./semantic'))['SemanticSearch'];

  beforeEach(async () => {
    vi.stubGlobal('window', { location: { href: 'https://example.test/' } });
    ({ SemanticSearch } = await import('./semantic'));
  });

  afterEach(() => {
    MockWorker.instances = [];
    vi.unstubAllGlobals();
  });

  it('recreates a worker after a transient worker failure', async () => {
    vi.stubGlobal('Worker', MockWorker);
    const search = new SemanticSearch('https://example.test/vectors.json', 'https://example.test/topics.json');

    const failed = search.search('first query');
    const first = MockWorker.instances[0];
    expect(first.postMessage).toHaveBeenCalledTimes(1);
    first.onerror?.({ message: 'temporary worker failure' } as ErrorEvent);
    await expect(failed).rejects.toThrow('temporary worker failure');
    expect(first.terminate).toHaveBeenCalledTimes(1);

    const recovered = search.search('second query');
    const second = MockWorker.instances[1];
    expect(second).toBeDefined();
    expect(second).not.toBe(first);
    second.onmessage?.({ data: { type: 'ready', requestId: 2, result: [{ id: 'paper-a', score: 0.8 }] } } as MessageEvent);
    await expect(recovered).resolves.toEqual([{ id: 'paper-a', score: 0.8 }]);
  });
});
