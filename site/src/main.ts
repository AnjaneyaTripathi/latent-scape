import './style.css';
import './overrides.css';
import { atlas, type Paper, type SearchDoc, type Topic } from './data';
import Graph from 'graphology';
import Sigma from 'sigma';
import { configureSearch, keywordSearch, lastSemanticNotice, search } from './search';
import { semanticSearch } from './semantic';

const app = document.querySelector<HTMLDivElement>('#app')!;
const esc = (value = '') => value.replace(/[&<>'"]/g, (char) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', "'": '&#39;', '"': '&quot;' })[char]!);
const route = () => decodeURIComponent(location.hash.replace(/^#\/?/, '') || 'discover');
const href = (path: string) => `#/${encodeURIComponent(path).replace(/%2F/g, '/')}`;
const link = (kind: 'paper' | 'topic', slug: string, label: string) => `<a href="${href(`${kind}/${slug}`)}">${esc(label)}</a>`;
const topicLabel = (slug: string) => atlas.topic(slug)?.title || slug.replaceAll('-', ' ');
let activeSigma: Sigma | undefined;
window.latentscapeSemanticSearch = async (query) => { const results = await semanticSearch.search(query); void precacheAtlas(); return results.map((result) => ({ slug: result.id, score: result.score })); };

function shell(content: string) {
  activeSigma?.kill(); activeSigma = undefined;
  app.innerHTML = `<a class="skip" href="#main">Skip to content</a><aside class="rail"><a class="brand" href="#/discover"><span class="brand-dot"></span>latent<span>scape</span></a><nav aria-label="Primary"><a href="#/discover">Discover</a><a href="#/topics">Topics</a><a href="#/map">Knowledge map</a><a href="#/about">About</a></nav><div class="rail-foot">NeurIPS + ICML<br><span>Research, mapped.</span></div></aside><header class="mobile-head"><a class="brand" href="#/discover">latent<span>scape</span></a><button id="theme" aria-label="Toggle theme">◐</button></header><main id="main">${content}</main><button class="theme" id="theme-wide" aria-label="Toggle theme">◐</button>`;
  document.querySelectorAll<HTMLButtonElement>('#theme,#theme-wide').forEach((button) => button.onclick = toggleTheme);
}

function status(text: string, kind = 'loading') { shell(`<section class="status ${kind}"><span class="orb"></span><p>${esc(text)}</p></section>`); }
function toggleTheme() { document.documentElement.dataset.theme = document.documentElement.dataset.theme === 'dark' ? 'light' : 'dark'; localStorage.setItem('latentscape-theme', document.documentElement.dataset.theme); }

function card(doc: SearchDoc) {
  const type = doc.type === 'topic' ? 'Topic' : `${doc.venue || 'Paper'} · ${doc.year || ''}`;
  const sourceUrl = doc.originalUrl || doc.url;
  return `<article class="card ${doc.type}"><p class="eyebrow">${esc(type)}</p><h3>${link(doc.type, doc.slug, doc.title)}</h3><p>${esc(doc.description || doc.abstract || 'Explore this part of the research landscape.').slice(0, 210)}</p>${doc.type === 'paper' && sourceUrl ? `<a class="source-link" href="${esc(sourceUrl)}" target="_blank" rel="noreferrer">${esc(doc.sourceLinkLabel || 'Original paper')} ↗</a>` : ''}<span class="arrow">↗</span></article>`;
}
function graphArt() { return `<svg class="hero-graph" viewBox="0 0 620 370" aria-label="Illustration of a connected research graph" role="img"><g stroke="currentColor" stroke-opacity=".23" fill="none"><path d="M87 284 185 190 303 243 440 161 543 215M185 190 235 82 385 91 440 161M303 243 371 320 543 215M87 284 371 320M235 82 303 243" /></g><g fill="currentColor"><circle cx="87" cy="284" r="7"/><circle cx="185" cy="190" r="13"/><circle cx="235" cy="82" r="8"/><circle cx="303" cy="243" r="20"/><circle cx="385" cy="91" r="10"/><circle cx="440" cy="161" r="17"/><circle cx="543" cy="215" r="8"/><circle cx="371" cy="320" r="11"/></g><g class="graph-label"><text x="53" y="312">optimisation</text><text x="168" y="169">representation</text><text x="282" y="278">learning</text><text x="422" y="145">generative</text></g></svg>`; }

async function discover() {
  shell(`<section class="hero"><div class="hero-copy"><p class="kicker">AN ATLAS FOR AI RESEARCH</p><h1>Find the idea<br>behind the paper.</h1><p class="lede">A concept-first guide to NeurIPS and ICML research. Start with a concept, follow the connections, reach the original work.</p><form id="search-form" class="searchbox"><label class="sr-only" for="search">Search concepts and papers</label><input id="search" autocomplete="off" placeholder="Try “diffusion models” or “causal discovery”" /><button>Explore <span>↗</span></button></form><p class="hint">Semantic search · ${atlas.manifest?.totalPapers?.toLocaleString() || 'Corpus'}-paper snapshot · works offline after loading</p></div>${graphArt()}</section><section class="strip"><span>START EXPLORING</span><a href="#/topics">Browse by topic <b>→</b></a><a href="#/map">Open knowledge map <b>→</b></a></section><section class="section-head"><div><p class="kicker">AVAILABLE STARTING POINTS</p><h2>Starting points</h2></div><a href="#/search">Search the snapshot →</a></section><section class="grid featured" id="featured-papers"><p class="empty">Loading starting points…</p></section>`);
  document.querySelector<HTMLFormElement>('#search-form')!.onsubmit = (event) => { event.preventDefault(); const value = document.querySelector<HTMLInputElement>('#search')!.value.trim(); location.hash = href(`search?q=${encodeURIComponent(value)}`); };
  void Promise.all([atlas.ensureTopics(), atlas.featuredPapers()]).then(([, papers]) => {
    const target = document.querySelector<HTMLElement>('#featured-papers');
    if (!target) return;
    const featured = papers.filter(Boolean) as Paper[];
    target.innerHTML = featured.length ? featured.map(paperCard).join('') : '<p class="empty">Paper detail shards will appear here once the data pipeline finishes.</p>';
  }).catch(() => {
    const target = document.querySelector<HTMLElement>('#featured-papers');
    if (target) target.innerHTML = '<p class="empty">Starting points are unavailable while the offline bundle loads.</p>';
  });
}

function paperCard(paper: Paper) { return `<article class="paper-card"><p class="eyebrow">${esc(paper.venue)} · ${paper.year}</p><h3>${link('paper', paper.slug, paper.title)}</h3><p>${esc(paper.summary?.tldr || paper.abstract).slice(0, 180)}</p>${paper.summary ? `<small class="ai-label">AI-generated summary</small> ${officialLink(paper)}` : ''}<div>${paper.topicSlugs?.slice(0, 2).map((slug) => `<span class="tag">${esc(topicLabel(slug))}</span>`).join('') || ''}</div></article>`; }

async function searchPage(query = '') {
  shell(`<section class="page-top"><p class="kicker">SEARCH THE ATLAS</p><h1>Where should we begin?</h1><form id="search-form" class="searchbox wide"><label class="sr-only" for="search">Search concepts and papers</label><input id="search" value="${esc(query)}" placeholder="A method, problem, or plain-language question" autofocus /><button>Search</button></form><div id="search-results" aria-live="polite">${query ? '<p class="loading-inline">Loading the search index…</p>' : '<p class="empty">Search mixes concepts and papers, then follows the meaning of your query.</p>'}</div></section>`);
  document.querySelector<HTMLFormElement>('#search-form')!.onsubmit = (event) => { event.preventDefault(); const value = document.querySelector<HTMLInputElement>('#search')!.value.trim(); location.hash = href(`search?q=${encodeURIComponent(value)}`); };
  if (!query) return;
  const target = document.querySelector('#search-results');
  if (!target) return;
  const documents = await atlas.ensureSearch();
  configureSearch(documents);
  const show = (results: Awaited<ReturnType<typeof search>>, note = '') => { if (!target.isConnected || document.querySelector<HTMLInputElement>('#search')?.value !== query) return; target.innerHTML = results.length ? `<p class="result-count">${results.length} paths through the atlas${note ? ` · ${note}` : ''}</p><section class="results">${results.map((result) => card(result.item)).join('')}</section>` : `<p class="empty">No close matches yet. Try a broader idea or a named method.</p>`; };
  show(keywordSearch(query), 'Loading semantic search…');
  show(await search(query, documents), lastSemanticNotice);
}

async function topicsPage() {
  await atlas.ensureTopics();
  shell(`<section class="page-top compact"><p class="kicker">CONCEPT LIBRARY</p><h1>Ideas, in context.</h1><p class="lede">Start with the concepts that organize the field. Each path traces the work that shaped it.</p><section class="topic-grid">${atlas.topics.map((topic) => `<article class="topic-card"><p>${esc(topic.area || 'Research area')}</p><h2>${link('topic', topic.slug, topic.title)}</h2><span>${esc(topic.overview || 'A connected area of machine learning research.').slice(0, 150)}</span><b>Explore →</b></article>`).join('') || '<p class="empty">Topic maps are being generated from the corpus.</p>'}</section></section>`);
}
function topicGraph(topic: Topic) { const nodes = [topic.slug, ...(topic.relatedTopics || []).slice(0, 5)]; return `<div class="mini-graph">${nodes.map((node, index) => `<span style="--i:${index}">${index === 0 ? esc(topic.title) : link('topic', node, topicLabel(node))}</span>`).join('')}<i></i><i></i><i></i></div>`; }
async function topicPage(slug: string) {
  await atlas.ensureTopics();
  const topic = atlas.topic(slug); if (!topic) return missing('That topic is not in this atlas yet.');
  // The offline topic builder persists paperSlugs chronologically, so topic
  // pages can load their paper shards without first fetching the search index.
  const ordered = (topic.paperSlugs || []).slice();
  const earlySlugs = ordered.slice(0, 6); const recentSlugs = ordered.slice(-6).filter((item) => !earlySlugs.includes(item));
  const [early, recent] = await Promise.all([atlas.papersFor(earlySlugs), atlas.papersFor(recentSlugs)]);
  const earlyPapers = early.filter(Boolean) as Paper[]; const recentPapers = recent.filter(Boolean) as Paper[];
  shell(`<article class="detail"><a class="crumb" href="#/topics">← All topics</a><p class="kicker">${esc(topic.area || 'CONCEPT')}</p><h1>${esc(topic.title)}</h1><p class="detail-lede">${esc(topic.overview || 'This concept is being mapped from the research corpus.')}</p><div class="topic-layout"><div><section class="prose"><h2>Why it matters</h2><p>${esc(topic.whyItMatters || 'Its influence is visible through the research questions and methods it connects.')}</p><h2>How it evolved</h2><p>${esc(topic.evolution || 'The corpus traces how this idea developed over time.')}</p>${topic.openQuestions?.length ? `<h2>Open questions</h2><ul>${topic.openQuestions.map((question) => `<li>${esc(question)}</li>`).join('')}</ul>` : ''}</section></div><aside class="info-panel"><p class="eyebrow">CONNECTIONS</p>${topicGraph(topic)}<p class="eyebrow">PREREQUISITES</p>${topic.prerequisites?.map((item) => `<span class="tag">${esc(topicLabel(item))}</span>`).join('') || '<p>Explore the linked papers to build context.</p>'}</aside></div><section class="section-head"><div><p class="kicker">READING THE FIELD</p><h2>Early work</h2></div></section><section class="grid featured">${earlyPapers.map(paperCard).join('') || '<p class="empty">Associated papers are loading with the next pipeline run.</p>'}</section>${recentPapers.length ? `<section class="section-head"><div><p class="kicker">READING THE FIELD</p><h2>Recent work</h2></div></section><section class="grid featured">${recentPapers.map(paperCard).join('')}</section>` : ''}<section class="related"><p class="kicker">NEIGHBOURING TOPICS</p><div>${(topic.relatedTopics || []).map((item) => link('topic', item, topicLabel(item))).join('') || 'Connections will appear as clustering completes.'}</div></section></article>`);
}
function officialLink(paper: Paper) { return `<a class="official" href="${esc(paper.originalUrl || paper.url)}" target="_blank" rel="noreferrer">${esc(paper.sourceLinkLabel || 'Original paper')} ↗</a>`; }
async function paperPage(slug: string) {
  const [paper] = await Promise.all([atlas.paper(slug), atlas.ensureTopics()]); if (!paper) return missing('This paper could not be found in the current snapshot.');
  const similar = (await atlas.papersFor(paper.similarPapers)).filter(Boolean) as Paper[];
  const references = (await atlas.papersFor(paper.references)).filter(Boolean) as Paper[];
  const citedBy = (await atlas.papersFor(paper.citedBy)).filter(Boolean) as Paper[];
  const abstract = paper.originalAbstract ?? paper.abstract;
  const awaitingAbstractVerification = paper.abstractStatus === 'needs-source-recovery' || paper.abstractQuality === 'official-landing-page-possibly-full-text';
  const abstractView = abstract ? `<p>${esc(abstract)}</p>` : '<p>Unavailable from the official source in this snapshot.</p>';
  const abstractHeading = awaitingAbstractVerification ? 'SOURCE TEXT AWAITING ABSTRACT VERIFICATION' : 'ORIGINAL ABSTRACT';
  const explanation = paper.summary ? summaryView(paper) : `<section class="summary-empty"><p>An AI-generated explanation is pending or unavailable for this paper. Read the original paper for detail.</p></section>`;
  shell(`<article class="detail paper-detail"><a class="crumb" href="#/search">← Back to search</a><p class="kicker">${esc(paper.venue)} · ${paper.year}</p><h1>${esc(paper.title)}</h1><p class="authors">${esc(paper.authors?.join(', ') || 'Authors unavailable')}</p><div class="paper-actions">${officialLink(paper)}${paper.codeUrl ? `<a href="${esc(paper.codeUrl)}" target="_blank" rel="noreferrer">Code ↗</a>` : ''}${paper.projectUrl ? `<a href="${esc(paper.projectUrl)}" target="_blank" rel="noreferrer">Project ↗</a>` : ''}</div><section class="official-abstract"><p class="eyebrow">${abstractHeading}</p>${abstractView}<p class="source-note">${awaitingAbstractVerification ? 'This source text is retained for audit and is not used for an AI explanation. ' : ''}Original source. ${officialLink(paper)}</p></section>${explanation}<section class="background"><p class="kicker">BACKGROUND CONCEPTS</p><div>${(paper.topicSlugs || []).map((item) => link('topic', item, topicLabel(item))).join('') || '<span>Topics are being mapped.</span>'}</div></section>${paper.references?.length || paper.citedBy?.length ? `<section class="citation-paths"><div><p class="kicker">BUILDS ON</p>${references.map((item) => link('paper', item.slug, item.title)).join('') || '<span>Reference links unavailable.</span>'}</div><div><p class="kicker">BUILT ON THIS</p>${citedBy.map((item) => link('paper', item.slug, item.title)).join('') || '<span>Derivative links unavailable.</span>'}</div></section>` : ''}<section class="section-head"><div><p class="kicker">SIDEWAYS NAVIGATION</p><h2>Similar papers</h2></div></section><section class="grid featured">${similar.map(paperCard).join('') || '<p class="empty">Similarity links will appear with the next graph build.</p>'}</section></article>`);
}
function summaryParagraphs(summary: NonNullable<Paper['summary']>) {
  if (summary.paragraphs && summary.paragraphs.length >= 3 && summary.paragraphs.length <= 5) return summary.paragraphs;
  return [summary.problem, summary.idea, summary.results, summary.limitations].filter((value): value is string => Boolean(value));
}
function summaryView(paper: Paper) {
  const summary = paper.summary!;
  const paragraphs = summaryParagraphs(summary);
  const body = paragraphs.length ? paragraphs.map((paragraph) => `<p>${esc(paragraph)}</p>`).join('') : '<p>This explanation is being regenerated from the official abstract.</p>';
  return `<section class="tldr"><p class="eyebrow">TL;DR <span class="ai-label">AI-generated summary</span> ${officialLink(paper)}</p><p>${esc(summary.tldr)}</p></section><section class="narrative"><p class="eyebrow">WHAT THIS PAPER DID <span class="ai-label">AI-generated summary</span> ${officialLink(paper)}</p>${body}<p class="source-note">Based on the official abstract. Read the original paper for methods, evidence, and full context.</p></section>`;
}
async function mapPage() {
  await atlas.ensureTopics();
  shell(`<section class="page-top map-page"><p class="kicker">THE FIELD AT A GLANCE</p><h1>Knowledge map</h1><p class="lede">A navigable view of topic connections in this ${atlas.manifest?.totalPapers?.toLocaleString() || 'research'}-paper snapshot.</p><div class="map-shell"><div id="sigma-map" class="map-placeholder" aria-label="Interactive topic connection graph">${graphArt()}</div><aside><p class="eyebrow">LEGEND</p><p><b>Nodes</b><br>research topics</p><p><b>Lines</b><br>neighbouring concepts</p><label for="map-topic">Open a topic</label><select id="map-topic"><option value="">Choose a topic</option>${atlas.topics.map((topic) => `<option value="${esc(topic.slug)}">${esc(topic.title)}</option>`).join('')}</select></aside></div></section>`);
  document.querySelector<HTMLSelectElement>('#map-topic')!.onchange = (event) => {
    const slug = (event.currentTarget as HTMLSelectElement).value;
    if (slug) location.hash = href(`topic/${slug}`);
  };
  void mountMap();
}
async function mountMap() { const element = document.querySelector<HTMLDivElement>('#sigma-map'); if (!element || !atlas.topics.length) return; const graph = new Graph(); const style = getComputedStyle(document.documentElement); const ink = style.getPropertyValue('--ink').trim(); const line = style.getPropertyValue('--line').trim(); atlas.topics.forEach((topic, index) => graph.addNode(topic.slug, { label: topic.title, x: Math.cos(index * 2.4) * (1 + index / 80), y: Math.sin(index * 2.4) * (1 + index / 80), size: 5 + Math.min((topic.paperSlugs?.length || 0), 18) / 5, color: '#e65322' })); atlas.topics.forEach((topic) => topic.relatedTopics?.slice(0, 4).forEach((other) => { if (graph.hasNode(other) && !graph.hasEdge(topic.slug, other)) graph.addEdge(topic.slug, other, { color: line, size: 1 }); })); element.innerHTML = ''; activeSigma = new Sigma(graph, element, { renderEdgeLabels: false, labelRenderedSizeThreshold: 8, labelColor: { color: ink } }); activeSigma.on('clickNode', ({ node }) => { location.hash = href(`topic/${node}`); }); }
async function aboutPage() { const report = await atlas.report().catch(() => undefined); const summaries = report?.summaryCoverage; shell(`<section class="page-top about"><p class="kicker">ABOUT LATENTSCAPE</p><h1>Research is a landscape.</h1><p class="lede">Latentscape is a static, offline-ready guide to NeurIPS and ICML research. It is built from a fixed snapshot of official proceedings and points you back to the source paper whenever you need it.</p><div class="about-grid"><article><h2>Concept first</h2><p>Browse the vocabulary and connections that make individual papers easier to place.</p></article><article><h2>Traceable</h2><p>Every generated summary sits next to a direct link to the original publication.</p></article><article><h2>Static by design</h2><p>No accounts, backend, tracking, or runtime research APIs.</p></article></div>${report ? `<section class="report"><p class="kicker">CURRENT SNAPSHOT</p><p>This snapshot contains <b>${report.papers || atlas.manifest?.totalPapers || 0} papers</b> and <b>${report.topics || atlas.manifest?.totalTopics || 0} topics</b> (${report.curatedTopics || 0} with curated guides). Citation-data match rate: ${((report.citationMatchRate || 0) * 100).toFixed(1)}%.</p>${summaries ? `<p>AI explanations: ${summaries.narrative} current narratives, ${summaries.legacy} legacy summaries, ${summaries.pending} pending, and ${summaries.unavailable} unavailable from sufficient source evidence.</p>` : ''}<p>Some historical source gaps remain; coverage is reported rather than hidden.</p><p><a href="${import.meta.env.BASE_URL}data/corpus-report.json" target="_blank">Corpus report ↗</a> · <a href="${import.meta.env.BASE_URL}data/coverage.json" target="_blank">Coverage audit ↗</a> · <a href="${import.meta.env.BASE_URL}summary-review.html" target="_blank">Summary review ↗</a></p></section>` : ''}</section>`); }
function missing(message: string) { shell(`<section class="status error"><p>${esc(message)}</p><a href="#/discover">Return to the atlas →</a></section>`); }
async function render() {
  const current = route(); const [path, query = ''] = current.split('?'); const [kind, slug] = path.split('/');
  try { if (kind === 'discover') await discover(); else if (kind === 'search') await searchPage(new URLSearchParams(query).get('q') || ''); else if (kind === 'topics') await topicsPage(); else if (kind === 'topic' && slug) await topicPage(slug); else if (kind === 'paper' && slug) await paperPage(slug); else if (kind === 'map') await mapPage(); else if (kind === 'about') await aboutPage(); else missing('That route does not exist.'); }
  catch (error) { missing(error instanceof Error ? `${error.message}. The offline data bundle may still be building.` : 'The atlas could not load.'); }
}
async function boot() { status('Loading the research atlas'); try { await atlas.initialize(); await render(); } catch (error) { missing(error instanceof Error ? `${error.message}. Add pipeline output to site/public/data.` : 'Data could not load.'); } }
async function precacheAtlas() { const registration = await navigator.serviceWorker?.ready; const shards = Object.values(atlas.manifest?.shards?.papers || {}).map((path) => `${import.meta.env.BASE_URL}data/${path}`); const loaded = performance.getEntriesByType('resource').map((entry) => entry.name).filter((url) => url.startsWith(location.origin)); registration?.active?.postMessage({ type: 'precache', urls: [...shards, ...loaded] }); }
const savedTheme = localStorage.getItem('latentscape-theme'); if (savedTheme) document.documentElement.dataset.theme = savedTheme;
window.addEventListener('hashchange', () => void render());
void boot();
if ('serviceWorker' in navigator) window.addEventListener('load', () => void navigator.serviceWorker.register(`${import.meta.env.BASE_URL}sw.js`));
