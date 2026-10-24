import './style.css';
import './overrides.css';
import './topic-graph.css';
import './topic-papers.css';
import './search-cards.css';
import { atlas, type Paper } from './data';
import { renderRichText, plainText } from './rich-text';
import { mountSearchCards } from './search-cards';
import { renderAbout } from './about';
import { configureSearch, keywordSearch, lastSemanticNotice, search } from './search';
import { semanticSearch } from './semantic';
import { NavigationState, RenderGate, parseHashRoute, rememberScroll, restoreScroll } from './navigation';

const app = document.querySelector<HTMLDivElement>('#app')!;
const esc = (value = '') => value.replace(/[&<>'"]/g, (char) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', "'": '&#39;', '"': '&quot;' })[char]!);
const route = () => parseHashRoute(location.hash).value;
const href = (path: string) => `#/${encodeURIComponent(path).replace(/%2F/g, '/')}`;
const link = (kind: 'paper' | 'topic', slug: string, label: string) => `<a href="${href(`${kind}/${slug}`)}">${renderRichText(label)}</a>`;
const topicLabel = (slug: string) => atlas.topic(slug)?.title || slug.replaceAll('-', ' ');
let disposeActiveMap: (() => void) | undefined;
let disposeActiveSearch: (() => void) | undefined;
const navigation = new NavigationState();
const renderGate = new RenderGate();
let hasRenderedRoute = false;
let pendingHistoryScroll: { renderId: number; position: { left: number; top: number } } | undefined;
window.latentscapeSemanticSearch = async (query) => { const results = await semanticSearch.search(query); void precacheAtlas(); return results.map((result) => ({ slug: result.id, score: result.score })); };

function shell(content: string, renderId?: number) {
  if (renderId !== undefined && !renderGate.isCurrent(renderId)) return false;
  disposeActiveMap?.(); disposeActiveMap = undefined;
  disposeActiveSearch?.(); disposeActiveSearch = undefined;
  app.innerHTML = `<a class="skip" href="#main">Skip to content</a><aside class="rail"><a class="brand" href="#/discover"><span class="brand-dot"></span>latent<span>scape</span></a><nav aria-label="Primary"><a href="#/discover">Discover</a><a href="#/topics">Topics</a><a href="#/map">Knowledge map</a><a href="#/about">About</a></nav><div class="rail-foot">NeurIPS + ICML<br><span>Research, mapped.</span></div></aside><header class="mobile-head"><a class="brand" href="#/discover">latent<span>scape</span></a><button id="theme" aria-label="Toggle theme">◐</button></header><main id="main" tabindex="-1">${content}</main><button class="theme" id="theme-wide" aria-label="Toggle theme">◐</button>`;
  document.querySelectorAll<HTMLButtonElement>('#theme,#theme-wide').forEach((button) => button.onclick = toggleTheme);
  return true;
}

function status(text: string, kind = 'loading') { shell(`<section class="status ${kind}"><span class="orb"></span><p>${esc(text)}</p></section>`); }
function toggleTheme() { document.documentElement.dataset.theme = document.documentElement.dataset.theme === 'dark' ? 'light' : 'dark'; localStorage.setItem('latentscape-theme', document.documentElement.dataset.theme); }
function navigate(path: string) {
  rememberScroll(sessionStorage, route(), { left: window.scrollX, top: window.scrollY });
  navigation.beginNewRoute();
  location.hash = href(path);
}
function finishRoute(renderId: number, current: string, intent: ReturnType<NavigationState['consumeIntent']>, waitForAsyncContent = false) {
  if (!renderGate.isCurrent(renderId) || route() !== current) return;
  const searchResults = document.querySelector<HTMLElement>('#search-results');
  const active = document.activeElement;
  const preserveSearchInteraction = Boolean(waitForAsyncContent && searchResults && (
    searchResults.contains(active)
    || active === document.querySelector('#search')
    || document.querySelector('dialog[data-paper-dialog][open]')
  ));
  if (preserveSearchInteraction) {
    // Semantic search can finish after a reader has opened a card. Do not turn
    // that completion into a route-level scroll or focus change.
    pendingHistoryScroll = undefined;
    hasRenderedRoute = true;
    return;
  }
  const position = intent === 'restore' ? restoreScroll(sessionStorage, current) : undefined;
  pendingHistoryScroll = position ? { renderId, position } : undefined;
  window.scrollTo(position?.left ?? 0, position?.top ?? 0);
  if (!waitForAsyncContent) pendingHistoryScroll = undefined;
  if (hasRenderedRoute) {
    const heading = document.querySelector<HTMLElement>('main h1');
    if (heading) { heading.tabIndex = -1; heading.focus({ preventScroll: true }); }
  }
  hasRenderedRoute = true;
}
function finishDeferredScroll(renderId: number) {
  if (!renderGate.isCurrent(renderId) || pendingHistoryScroll?.renderId !== renderId) return;
  if (document.querySelector('dialog[data-paper-dialog][open]')) { pendingHistoryScroll = undefined; return; }
  window.scrollTo(pendingHistoryScroll.position.left, pendingHistoryScroll.position.top);
  pendingHistoryScroll = undefined;
}

function graphArt() { return `<svg class="hero-graph" viewBox="0 0 620 370" aria-label="Illustration of a connected research graph" role="img"><g stroke="currentColor" stroke-opacity=".23" fill="none"><path d="M87 284 185 190 303 243 440 161 543 215M185 190 235 82 385 91 440 161M303 243 371 320 543 215M87 284 371 320M235 82 303 243" /></g><g fill="currentColor"><circle cx="87" cy="284" r="7"/><circle cx="185" cy="190" r="13"/><circle cx="235" cy="82" r="8"/><circle cx="303" cy="243" r="20"/><circle cx="385" cy="91" r="10"/><circle cx="440" cy="161" r="17"/><circle cx="543" cy="215" r="8"/><circle cx="371" cy="320" r="11"/></g><g class="graph-label"><text x="53" y="312">optimisation</text><text x="168" y="169">representation</text><text x="282" y="278">learning</text><text x="422" y="145">generative</text></g></svg>`; }

async function discover(renderId: number) {
  if (!shell(`<section class="hero"><div class="hero-copy"><p class="kicker">A GUIDE TO AI RESEARCH</p><h1>Find the idea<br>behind the paper.</h1><p class="lede">A concept-first guide to NeurIPS and ICML research. Start with a concept, follow the connections, reach the original work.</p><form id="search-form" class="searchbox"><label class="sr-only" for="search">Search topics and papers</label><input id="search" autocomplete="off" placeholder="Try “diffusion models” or “causal discovery”" /><button>Explore <span>→</span></button></form><p class="hint">Semantic search · ${atlas.manifest?.totalPapers?.toLocaleString() || 'Corpus'}-paper snapshot · works offline after loading</p></div>${graphArt()}</section><section class="strip"><span>START EXPLORING</span><a href="#/topics">Browse by topic <b>→</b></a><a href="#/map">Open knowledge map <b>→</b></a></section><section class="section-head"><div><p class="kicker">AVAILABLE STARTING POINTS</p><h2>Starting points</h2></div><a href="#/search">Search the snapshot →</a></section><section class="grid featured" id="featured-papers"><p class="empty">Loading starting points…</p></section>`, renderId)) return;
  document.querySelector<HTMLFormElement>('#search-form')!.onsubmit = (event) => { event.preventDefault(); navigate(`search?q=${encodeURIComponent(document.querySelector<HTMLInputElement>('#search')!.value.trim())}`); };
  void Promise.all([atlas.ensureTopics(), atlas.featuredPapers()]).then(([, papers]) => {
    const target = document.querySelector<HTMLElement>('#featured-papers');
    if (!renderGate.isCurrent(renderId) || !target) return;
    const featured = papers.filter(Boolean) as Paper[];
    target.innerHTML = featured.length ? featured.map(paperCard).join('') : '<p class="empty">No starting points are available. Try searching for a topic or method.</p>';
  }).catch(() => {
    const target = document.querySelector<HTMLElement>('#featured-papers');
    if (renderGate.isCurrent(renderId) && target) target.innerHTML = '<p class="empty">Starting points are temporarily unavailable. Try searching for a topic or method.</p>';
  });
}

function paperCard(paper: Paper) { return `<article class="paper-card"><p class="eyebrow">${esc(paper.venue)} · ${paper.year}</p><h3>${link('paper', paper.slug, paper.title)}</h3><p>${paper.summary ? renderRichText(paper.summary.tldr) : 'Read the original abstract on the paper page.'}</p>${paper.summary ? `<small class="ai-label">AI-generated summary</small> ${officialLink(paper)}` : ''}<div>${paper.topicSlugs?.slice(0, 2).map((slug) => `<span class="tag">${renderRichText(topicLabel(slug))}</span>`).join('') || ''}</div></article>`; }

async function searchPage(query = '', renderId: number) {
  if (!shell(`<section class="page-top"><p class="kicker">SEARCH LATENTSCAPE</p><h1>Where should we begin?</h1><form id="search-form" class="searchbox wide"><label class="sr-only" for="search">Search topics and papers</label><input id="search" value="${esc(query)}" placeholder="A method, problem, or plain-language question" autofocus /><button>Search</button></form><div id="search-results">${query ? '<p class="loading-inline" aria-live="polite">Loading the search index…</p>' : '<p class="empty">Search mixes concepts and papers, then follows the meaning of your query.</p>'}</div></section>`, renderId)) return;
  document.querySelector<HTMLFormElement>('#search-form')!.onsubmit = (event) => { event.preventDefault(); navigate(`search?q=${encodeURIComponent(document.querySelector<HTMLInputElement>('#search')!.value.trim())}`); };
  if (!query) return;
  const target = document.querySelector<HTMLElement>('#search-results');
  if (!target) return;
  const documents = await atlas.ensureSearch();
  if (!renderGate.isCurrent(renderId)) return;
  configureSearch(documents);
  target.innerHTML = '<p class="result-count" data-search-result-count aria-live="polite"></p><section class="results" data-search-results></section>';
  const cards = mountSearchCards(target.querySelector<HTMLElement>('[data-search-results]')!);
  disposeActiveSearch = cards.dispose;
  const show = (results: Awaited<ReturnType<typeof search>>, note = '') => {
    if (!renderGate.isCurrent(renderId) || !target.isConnected || document.querySelector<HTMLInputElement>('#search')?.value !== query) return;
    const count = target.querySelector<HTMLElement>('[data-search-result-count]');
    if (!results.length) { if (count) count.textContent = 'No close matches yet. Try a broader idea or a named method.'; cards.render([]); return; }
    if (count) count.textContent = `${results.length} results${note ? ` · ${note}` : ''}`;
    cards.render(results.map((result) => result.item));
  };
  show(keywordSearch(query).slice(0, 40), 'Loading semantic search…');
  show(await search(query, documents), lastSemanticNotice);
  finishDeferredScroll(renderId);
}

function topicExcerpt(value: string) {
  const text = plainText(value);
  return text.length > 150 ? `${text.slice(0, 150).replace(/\s+\S*$/, '').trimEnd()}…` : text;
}
async function topicsPage(renderId: number) {
  await atlas.ensureTopics();
  if (!renderGate.isCurrent(renderId)) return;
  shell(`<section class="page-top compact"><p class="kicker">TOPIC LIBRARY</p><h1>Ideas, in context.</h1><p class="lede">Start with the concepts that organize the field. Each path traces the work that shaped it.</p><section class="topic-grid">${atlas.topics.map((topic) => `<article class="topic-card"><p>${renderRichText(topic.area || 'Research area')}</p><h2>${link('topic', topic.slug, topic.title)}</h2><span>${esc(topicExcerpt(topic.overview || 'A connected area of machine learning research.'))}</span><b>Explore →</b></article>`).join('') || '<p class="empty">No topics are available in this snapshot.</p>'}</section></section>`, renderId);
}
async function topicPage(slug: string, query: string, renderId: number) {
  if (!/^[a-z0-9-]+$/.test(slug)) {
    missing('That topic is not in this snapshot.', renderId);
    return undefined;
  }
  const { loadTopicPapers, mountTopicPapers } = await import('./topic-papers');
  const data = await loadTopicPapers(slug);
  if (!renderGate.isCurrent(renderId)) return;
  if (!data) return missing('That topic is not in this snapshot.', renderId);
  if (!shell('<div id="topic-reader-mount"></div>', renderId)) return;
  const mount = document.querySelector<HTMLElement>('#topic-reader-mount');
  if (!mount || !renderGate.isCurrent(renderId)) return;
  const mounted = mountTopicPapers(mount, data, query);
  return mounted.canonicalRoute;
}
function officialLink(paper: Paper) { return `<a class="official" href="${esc(paper.originalUrl || paper.url)}" target="_blank" rel="noreferrer">${esc(paper.sourceLinkLabel || 'Original paper')} ↗</a>`; }
async function paperPage(slug: string, query: string, renderId: number) {
  const [paper] = await Promise.all([atlas.paper(slug), atlas.ensureTopics()]);
  if (!renderGate.isCurrent(renderId)) return;
  if (!paper) return missing('This paper could not be found in the current snapshot.', renderId);
  const similar = (await atlas.papersFor(paper.similarPapers)).filter(Boolean) as Paper[];
  const references = (await atlas.papersFor(paper.references)).filter(Boolean) as Paper[];
  const citedBy = (await atlas.papersFor(paper.citedBy)).filter(Boolean) as Paper[];
  if (!renderGate.isCurrent(renderId)) return;
  const abstract = paper.originalAbstract ?? paper.abstract;
  const awaitingAbstractVerification = paper.abstractStatus === 'needs-source-recovery' || paper.abstractQuality === 'official-landing-page-possibly-full-text';
  const abstractView = abstract ? `<p>${renderRichText(abstract)}</p>` : '<p>Unavailable from the official source in this snapshot.</p>';
  const sourceCharactersNote = /\(cid:\d+\)|\uFFFD/.test(abstract || '') ? 'Some characters in this source text could not be recovered. See the original paper. ' : '';
  const abstractHeading = awaitingAbstractVerification ? 'SOURCE TEXT AWAITING ABSTRACT VERIFICATION' : 'ORIGINAL ABSTRACT';
  const explanation = paper.summary ? summaryView(paper) : `<section class="summary-empty"><p>An AI-generated explanation is unavailable for this paper. Read the original paper for detail.</p></section>`;
  const from = new URLSearchParams(query).get('from');
  const returnHref = from?.startsWith('#/topic/') ? from : '#/search';
  const returnTab = from?.startsWith('#/topic/') ? new URLSearchParams(from.split('?')[1] || '').get('tab') : null;
  const returnLabel = from?.startsWith('#/topic/') ? returnTab === 'papers' ? '← Back to topic papers' : '← Back to topic' : '← Back to search';
  shell(`<article class="detail paper-detail"><a class="crumb"${from?.startsWith('#/topic/') ? ' data-restore-scroll' : ''} href="${esc(returnHref)}">${returnLabel}</a><p class="kicker">${esc(paper.venue)} · ${paper.year}</p><h1>${renderRichText(paper.title)}</h1><p class="authors">${renderRichText(paper.authors?.join(', ') || 'Authors unavailable')}</p><div class="paper-actions">${officialLink(paper)}${paper.codeUrl ? `<a href="${esc(paper.codeUrl)}" target="_blank" rel="noreferrer">Code ↗</a>` : ''}${paper.projectUrl ? `<a href="${esc(paper.projectUrl)}" target="_blank" rel="noreferrer">Project ↗</a>` : ''}</div><section class="official-abstract"><p class="eyebrow">${abstractHeading}</p>${abstractView}<p class="source-note">${awaitingAbstractVerification ? 'This text needs confirmation against the original paper, so no AI explanation is shown. ' : ''}${sourceCharactersNote}Source link. ${officialLink(paper)}</p></section>${explanation}<section class="background"><p class="kicker">BACKGROUND CONCEPTS</p><div>${(paper.topicSlugs || []).map((item) => link('topic', item, topicLabel(item))).join('') || '<span>No background topics are available for this paper.</span>'}</div></section>${paper.references?.length || paper.citedBy?.length ? `<section class="citation-paths"><div><p class="kicker">BUILDS ON</p>${references.map((item) => link('paper', item.slug, item.title)).join('') || '<span>Reference links unavailable.</span>'}</div><div><p class="kicker">BUILT ON THIS</p>${citedBy.map((item) => link('paper', item.slug, item.title)).join('') || '<span>Derivative links unavailable.</span>'}</div></section>` : ''}<section class="section-head"><div><p class="kicker">SIDEWAYS NAVIGATION</p><h2>Similar papers</h2></div></section><section class="grid featured">${similar.map(paperCard).join('') || '<p class="empty">Similar papers are unavailable for this record.</p>'}</section></article>`, renderId);
}
function summaryParagraphs(summary: NonNullable<Paper['summary']>) {
  if (summary.paragraphs && summary.paragraphs.length >= 3 && summary.paragraphs.length <= 5) return summary.paragraphs;
  return [summary.problem, summary.idea, summary.results, summary.limitations].filter((value): value is string => Boolean(value));
}
function summaryView(paper: Paper) {
  const summary = paper.summary!;
  const paragraphs = summaryParagraphs(summary);
  const body = paragraphs.length ? paragraphs.map((paragraph) => `<p>${renderRichText(paragraph)}</p>`).join('') : '<p>An AI-generated explanation is unavailable for this paper. Read the original paper for detail.</p>';
  return `<section class="tldr"><p class="eyebrow">TL;DR <span class="ai-label">AI-generated summary</span> ${officialLink(paper)}</p><p>${renderRichText(summary.tldr)}</p></section><section class="narrative"><p class="eyebrow">WHAT THIS PAPER DID <span class="ai-label">AI-generated summary</span> ${officialLink(paper)}</p>${body}<p class="source-note">Based on the official abstract. Read the original paper for methods, evidence, and full context.</p></section>`;
}
async function mapPage(query: string, renderId: number) {
  const { loadTopicGraph, mountTopicGraph } = await import('./topic-graph');
  const data = await loadTopicGraph();
  if (!renderGate.isCurrent(renderId)) return;
  const params = new URLSearchParams(query);
  const selectedSlug = params.get('topic') || data.defaultTopicSlug;
  shell(`<section class="page-top map-page"><p class="kicker">SEMANTIC NEIGHBOURHOODS</p><h1>Knowledge map</h1><p class="lede">Follow the ideas around a topic.</p><section id="topic-graph-shell"><div class="topic-map-controls"><div class="topic-map-picker" data-topic-picker><span id="map-topic-label">Focus topic</span><button id="map-topic" type="button" aria-labelledby="map-topic-label map-topic-value" aria-haspopup="listbox" aria-controls="map-topic-listbox" aria-expanded="false"><span id="map-topic-value" data-topic-picker-label></span><span aria-hidden="true">⌄</span></button><div id="map-topic-popover" class="topic-map-popover" hidden><label class="sr-only" for="map-topic-search">Search topics</label><div class="topic-map-search-row"><input id="map-topic-search" type="search" role="combobox" aria-expanded="true" aria-controls="map-topic-listbox" autocomplete="off" placeholder="Search topics"><button type="button" data-topic-picker-clear hidden aria-label="Clear topic search">Clear</button></div><p data-topic-picker-count aria-live="polite"></p><div id="map-topic-listbox" role="listbox" aria-label="Topics"></div></div></div></div><p class="topic-map-legend"><i></i> Lines show semantic relationships, not citations.</p><div class="topic-graph-scroller" tabindex="0" aria-label="Topic relationship diagram; swipe sideways on small screens"><div id="topic-graph" class="topic-graph" data-topic-graph-ready="true"></div></div></section></section>`, renderId);
  const container = document.querySelector<HTMLElement>('#topic-graph-shell');
  if (!container || !renderGate.isCurrent(renderId)) return;
  disposeActiveMap = mountTopicGraph(container, data, selectedSlug);
}
async function aboutPage(renderId: number) { shell(renderAbout(atlas.manifest || {}), renderId); }
function missing(message: string, renderId?: number) { shell(`<section class="status error"><p>${esc(message)}</p><a href="#/discover">Return to Discover →</a></section>`, renderId); }
async function render() {
  const renderId = renderGate.begin();
  pendingHistoryScroll = undefined;
  const parsed = parseHashRoute(location.hash);
  const current = parsed.value; let finishedCurrent = current; const { path, query } = parsed; const [kind, slug] = path.split('/');
  const intent = navigation.consumeIntent();
  try {
    if (kind === 'discover') await discover(renderId);
    else if (kind === 'search') await searchPage(new URLSearchParams(query).get('q') || '', renderId);
    else if (kind === 'topics') await topicsPage(renderId);
    else if (kind === 'topic' && slug) {
      const canonicalRoute = await topicPage(slug, query, renderId);
      if (canonicalRoute) finishedCurrent = canonicalRoute;
    }
    else if (kind === 'paper' && slug) await paperPage(slug, query, renderId);
    else if (kind === 'map') await mapPage(query, renderId);
    else if (kind === 'about') await aboutPage(renderId);
    else missing('That route does not exist.', renderId);
    finishRoute(renderId, finishedCurrent, intent, kind === 'search' && Boolean(query));
  } catch (error) {
    if (renderGate.isCurrent(renderId)) {
      missing('This page could not load. Please reload and try again.', renderId);
      finishRoute(renderId, current, intent);
    }
  }
}
async function boot() { status('Loading Latentscape'); try { await atlas.initialize(); await render(); } catch { missing('The site could not load. Please reload and try again.'); } }
async function precacheAtlas() { const registration = await navigator.serviceWorker?.ready; const shards = Object.values(atlas.manifest?.shards?.papers || {}).map((path) => `${import.meta.env.BASE_URL}data/${path}`); const loaded = performance.getEntriesByType('resource').map((entry) => entry.name).filter((url) => url.startsWith(location.origin)); registration?.active?.postMessage({ type: 'precache', urls: [...shards, ...loaded] }); }
const savedTheme = localStorage.getItem('latentscape-theme'); if (savedTheme) document.documentElement.dataset.theme = savedTheme;
document.addEventListener('click', (event) => {
  const element = event.target instanceof Element ? event.target.closest<HTMLAnchorElement>('a[href]') : null;
  if (!element || event.defaultPrevented || event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
  const target = element.getAttribute('href');
  if (target === '#main') {
    event.preventDefault();
    document.querySelector<HTMLElement>('main')?.focus({ preventScroll: true });
    return;
  }
  if (target?.startsWith('#/')) {
    rememberScroll(sessionStorage, route(), { left: window.scrollX, top: window.scrollY });
    if (element.hasAttribute('data-restore-scroll')) navigation.beginHistoryRoute();
    else navigation.beginNewRoute();
  }
});
window.addEventListener('popstate', () => navigation.beginHistoryRoute());
window.addEventListener('hashchange', () => void render());
void boot();
if ('serviceWorker' in navigator) window.addEventListener('load', () => void navigator.serviceWorker.register(`${import.meta.env.BASE_URL}sw.js`));
