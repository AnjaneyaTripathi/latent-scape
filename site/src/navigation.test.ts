import { describe, expect, it } from 'vitest';
import { NavigationState, RenderGate, parseHashRoute, rememberScroll, restoreScroll, scrollKey } from './navigation';

class MemoryStore {
  private values = new Map<string, string>();
  getItem(key: string) { return this.values.get(key) ?? null; }
  setItem(key: string, value: string) { this.values.set(key, value); }
}

describe('route navigation', () => {
  it('opens an explicitly selected paper at the top after saving the prior route position', () => {
    const store = new MemoryStore();
    const navigation = new NavigationState();
    rememberScroll(store, 'search?q=diffusion', { left: 0, top: 1840 });
    navigation.beginNewRoute();

    expect(restoreScroll(store, 'search?q=diffusion')).toEqual({ left: 0, top: 1840 });
    expect(navigation.consumeIntent()).toBe('top');
  });

  it('restores the saved position when browser history returns to a route', () => {
    const store = new MemoryStore();
    const navigation = new NavigationState();
    rememberScroll(store, 'paper/a', { left: 0, top: 920 });
    navigation.beginHistoryRoute();

    expect(scrollKey('paper/a')).toBe('latentscape-scroll:paper/a');
    expect(navigation.consumeIntent()).toBe('restore');
    expect(restoreScroll(store, 'paper/a')).toEqual({ left: 0, top: 920 });
  });

  it('prevents a slow earlier route from replacing a newer route', () => {
    const gate = new RenderGate();
    const slowPaper = gate.begin();
    const newerTopic = gate.begin();

    expect(gate.isCurrent(slowPaper)).toBe(false);
    expect(gate.isCurrent(newerTopic)).toBe(true);
  });

  it('preserves encoded query characters while decoding the route path', () => {
    const encoded = '#/search%3Fq%3DL1%2520%2526%2520L2%2520regularization%2520%253F%2520%2525%2520%25CE%25BA';
    const parsed = parseHashRoute(encoded);

    expect(parsed.path).toBe('search');
    expect(parsed.query).toBe('q=L1%20%26%20L2%20regularization%20%3F%20%25%20%CE%BA');
    expect(new URLSearchParams(parsed.query).get('q')).toBe('L1 & L2 regularization ? % κ');
  });

  it('falls back safely for a malformed hash escape', () => {
    expect(parseHashRoute('#/paper/%E0%A4%A')).toEqual({ path: 'discover', query: '', value: 'discover' });
  });
});
