export type ScrollIntent = 'top' | 'restore';

export interface ParsedRoute {
  path: string;
  query: string;
  value: string;
}

/**
 * Decode only the outer hash route encoding. Query bytes stay encoded so a
 * literal ampersand, question mark, percent sign, or Unicode character remains
 * part of the query value when URLSearchParams reads it later.
 */
export function parseHashRoute(hash: string): ParsedRoute {
  const encoded = hash.replace(/^#\/?/, '');
  if (!encoded || encoded === 'main') return { path: 'discover', query: '', value: 'discover' };
  try {
    const rawSeparator = encoded.indexOf('?');
    if (rawSeparator !== -1) {
      const path = decodeURIComponent(encoded.slice(0, rawSeparator));
      const query = encoded.slice(rawSeparator + 1);
      if (!path || path === 'main') return { path: 'discover', query: '', value: 'discover' };
      return { path, query, value: `${path}?${query}` };
    }
    const outer = decodeURIComponent(encoded);
    const separator = outer.indexOf('?');
    const path = separator === -1 ? outer : outer.slice(0, separator);
    const query = separator === -1 ? '' : outer.slice(separator + 1);
    if (!path || path === 'main') return { path: 'discover', query: '', value: 'discover' };
    return { path, query, value: query ? `${path}?${query}` : path };
  } catch {
    return { path: 'discover', query: '', value: 'discover' };
  }
}

export interface ScrollPosition {
  left: number;
  top: number;
}

interface ScrollStore {
  getItem(key: string): string | null;
  setItem(key: string, value: string): void;
}

const SCROLL_PREFIX = 'latentscape-scroll:';

export function scrollKey(route: string) {
  return `${SCROLL_PREFIX}${route}`;
}

export function rememberScroll(store: ScrollStore, route: string, position: ScrollPosition) {
  try {
    store.setItem(scrollKey(route), JSON.stringify(position));
  } catch {
    // Private browsing or a full storage quota should not break navigation.
  }
}

export function restoreScroll(store: Pick<ScrollStore, 'getItem'>, route: string): ScrollPosition | undefined {
  try {
    const value = store.getItem(scrollKey(route));
    if (!value) return undefined;
    const parsed = JSON.parse(value) as Partial<ScrollPosition>;
    if (typeof parsed.left !== 'number' || typeof parsed.top !== 'number') return undefined;
    return { left: parsed.left, top: parsed.top };
  } catch {
    return undefined;
  }
}

/** Keeps explicit navigation at the top while restoring browser-history routes. */
export class NavigationState {
  private intent: ScrollIntent = 'top';

  beginNewRoute() {
    this.intent = 'top';
  }

  beginHistoryRoute() {
    this.intent = 'restore';
  }

  consumeIntent(): ScrollIntent {
    const intent = this.intent;
    this.intent = 'top';
    return intent;
  }
}

/** Reject a slow prior route after the reader has navigated elsewhere. */
export class RenderGate {
  private current = 0;

  begin() {
    this.current += 1;
    return this.current;
  }

  isCurrent(id: number) {
    return id === this.current;
  }
}
