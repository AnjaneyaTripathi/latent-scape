export interface TopicPaper { slug: string; title: string; authors: string[]; year: number; venue: string; sourceUrl: string; sourceLinkLabel: string; tldr?: string }
export interface TopicPapersData { version: 1; topic: { slug: string; title: string; area?: string; overview?: string; whyItMatters?: string; evolution?: string; openQuestions?: string[]; difficulty?: string; paperCount: number }; relatedTopics: Array<{ slug: string; title: string; paperCount: number }>; prerequisites: Array<{ slug: string; title: string }>; papers: TopicPaper[] }
export interface TopicPaperState { q: string; year: string; venue: string; sort: 'newest' | 'oldest' | 'title'; page: number }

const PAGE_SIZE = 25;
const esc = (value = '') => value.replace(/[&<>'"]/g, (character) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', "'": '&#39;', '"': '&quot;' })[character]!);
const expandedKey = (slug: string) => `latentscape-topic-expanded:${slug}`;

export async function loadTopicPapers(slug: string) {
  const response = await fetch(`${import.meta.env.BASE_URL}data/topic-papers/${encodeURIComponent(slug)}.json`);
  if (response.status === 404) return undefined;
  if (!response.ok) throw new Error('Topic papers unavailable');
  return await response.json() as TopicPapersData;
}
export function topicYears(papers: TopicPaper[]) { return [...new Set(papers.map((paper) => paper.year).filter(Number.isFinite))].sort((a, b) => b - a); }
export function topicVenues(papers: TopicPaper[]) { return [...new Set(papers.map((paper) => paper.venue).filter(Boolean))].sort((a, b) => a.localeCompare(b)); }
export function normalizeTopicPaperState(rawQuery: string, papers: TopicPaper[]): TopicPaperState {
  const params = new URLSearchParams(rawQuery); const years = topicYears(papers); const venues = topicVenues(papers); const page = Number(params.get('page')); const sort = params.get('sort');
  return { q: params.get('q') || '', year: years.includes(Number(params.get('year'))) ? String(Number(params.get('year'))) : '', venue: venues.includes(params.get('venue') || '') ? params.get('venue') || '' : '', sort: sort === 'oldest' || sort === 'title' ? sort : 'newest', page: Number.isInteger(page) && page > 0 ? page : 1 };
}
export function filterTopicPapers(papers: TopicPaper[], state: TopicPaperState) {
  const needle = state.q.toLocaleLowerCase();
  return papers.filter((paper) => (!state.year || String(paper.year) === state.year) && (!state.venue || paper.venue === state.venue) && (!needle || `${paper.title} ${paper.authors.join(' ')} ${paper.tldr || ''}`.toLocaleLowerCase().includes(needle))).sort((a, b) => state.sort === 'title' ? a.title.localeCompare(b.title) : state.sort === 'oldest' ? a.year - b.year || a.title.localeCompare(b.title) : b.year - a.year || a.title.localeCompare(b.title));
}
export function topicRoute(slug: string, tab: 'overview' | 'papers', state?: TopicPaperState) {
  const params = new URLSearchParams();
  if (tab === 'papers') { params.set('tab', 'papers'); if (state?.q) params.set('q', state.q); if (state?.year) params.set('year', state.year); if (state?.venue) params.set('venue', state.venue); if (state?.sort && state.sort !== 'newest') params.set('sort', state.sort); if (state?.page && state.page > 1) params.set('page', String(state.page)); }
  return `#/topic/${encodeURIComponent(slug)}${params.size ? `?${params}` : ''}`;
}
function readExpanded(slug: string) { try { const values = JSON.parse(sessionStorage.getItem(expandedKey(slug)) || '[]'); return new Set(Array.isArray(values) ? values.filter((item): item is string => typeof item === 'string') : []); } catch { return new Set<string>(); } }
function saveExpanded(slug: string, values: Set<string>) { try { sessionStorage.setItem(expandedKey(slug), JSON.stringify([...values])); } catch { /* optional storage */ } }
function paperRow(paper: TopicPaper, expanded: Set<string>, backRoute: string) {
  const summary = paper.tldr ? `<details data-topic-paper-tldr="${esc(paper.slug)}"${expanded.has(paper.slug) ? ' open' : ''}><summary>TL;DR <span>AI-generated summary</span></summary><p>${esc(paper.tldr)}</p></details>` : '<p class="topic-paper-no-tldr">No AI-generated summary is available.</p>';
  return `<article class="topic-paper-row" data-topic-paper-row><div><h3><a href="#/paper/${encodeURIComponent(paper.slug)}?from=${encodeURIComponent(backRoute)}">${esc(paper.title)}</a></h3><p>${esc(paper.authors.join(', '))}</p></div><p class="topic-paper-meta">${paper.year} · ${esc(paper.venue)}</p><a class="topic-paper-source" href="${esc(paper.sourceUrl)}" target="_blank" rel="noreferrer">${esc(paper.sourceLinkLabel || 'Official source')} ↗</a>${summary}</article>`;
}
function paperCard(paper: TopicPaper, backRoute: string) {
  const sourceLabel = paper.sourceLinkLabel || 'Original paper';
  return `<article class="paper-card topic-paper-card"><p class="eyebrow">${esc(paper.venue)} · ${paper.year}</p><h3><a href="#/paper/${encodeURIComponent(paper.slug)}?from=${encodeURIComponent(backRoute)}">${esc(paper.title)}</a></h3><p class="topic-paper-card-authors">${esc(paper.authors.join(', '))}</p>${paper.tldr ? `<p class="topic-paper-card-tldr">${esc(paper.tldr)}</p>` : ''}<div class="topic-paper-card-footer">${paper.tldr ? '<small class="ai-label">AI-generated summary</small>' : ''}<a class="official" href="${esc(paper.sourceUrl)}" target="_blank" rel="noreferrer">${esc(sourceLabel)} ↗</a></div></article>`;
}
function overviewMarkup(data: TopicPapersData, backRoute: string) {
  const related = data.relatedTopics.map((topic) => `<li><a href="#/topic/${encodeURIComponent(topic.slug)}"><span class="connection-title">${esc(topic.title)}</span><span class="connection-count">${topic.paperCount} papers</span><span class="connection-arrow">→</span></a></li>`).join('') || '<li>No related topics are listed yet.</li>';
  const prerequisites = data.prerequisites.length ? data.prerequisites.map((topic) => `<a class="tag" href="#/topic/${encodeURIComponent(topic.slug)}">${esc(topic.title)}</a>`).join('') : '<p class="topic-reader-fallback">No prerequisites are listed for this topic.</p>';
  const chronological = data.papers.slice().sort((a, b) => a.year - b.year || a.title.localeCompare(b.title));
  const early = chronological.slice(0, 6);
  const earlySlugs = new Set(early.map((paper) => paper.slug));
  const recent = data.papers.slice().sort((a, b) => b.year - a.year || a.title.localeCompare(b.title)).filter((paper) => !earlySlugs.has(paper.slug)).slice(0, 6);
  return `<div class="topic-layout"><section class="prose"><h2>Why it matters</h2><p>${esc(data.topic.whyItMatters || data.topic.overview || 'This topic connects a body of research in the snapshot.')}</p><h2>How it evolved</h2><p>${esc(data.topic.evolution || 'Explore the early and recent work below to follow this area.')}</p>${data.topic.openQuestions?.length ? `<h2>Open questions</h2><ul>${data.topic.openQuestions.map((question) => `<li>${esc(question)}</li>`).join('')}</ul>` : '<h2>Open questions</h2><p>No open questions are listed yet.</p>'}</section><aside class="info-panel"><p class="eyebrow">RELATED TOPICS</p><ul class="topic-connections">${related}</ul><p class="eyebrow">PREREQUISITES</p>${prerequisites}${data.topic.difficulty ? `<p>${esc(data.topic.difficulty)}</p>` : ''}</aside></div><section class="section-head topic-paper-preview-head"><div><p class="kicker">READING THE FIELD</p><h2>Early work</h2></div></section><section class="grid featured topic-paper-preview">${early.map((paper) => paperCard(paper, backRoute)).join('') || '<p class="empty">No papers are available for this topic.</p>'}</section>${recent.length ? `<section class="section-head topic-paper-preview-head"><div><p class="kicker">READING THE FIELD</p><h2>Recent work</h2></div></section><section class="grid featured topic-paper-preview">${recent.map((paper) => paperCard(paper, backRoute)).join('')}</section>` : ''}`;
}
function controlsMarkup(years: number[], venues: string[], state: TopicPaperState) {
  const select = (attribute: string, options: string) => `<span class="topic-paper-select"><select ${attribute}>${options}</select></span>`;
  return `<section class="topic-paper-controls"><label>Search papers<input data-topic-paper-search value="${esc(state.q)}" placeholder="Title, author, or summary"></label><label>Year${select('data-topic-paper-year', `<option value="">All years</option>${years.map((year) => `<option value="${year}"${String(year) === state.year ? ' selected' : ''}>${year}</option>`).join('')}`)}</label><label>Venue${select('data-topic-paper-venue', `<option value="">All venues</option>${venues.map((venue) => `<option value="${esc(venue)}"${venue === state.venue ? ' selected' : ''}>${esc(venue)}</option>`).join('')}`)}</label><label>Sort${select('data-topic-paper-sort', `<option value="newest">Newest</option><option value="oldest"${state.sort === 'oldest' ? ' selected' : ''}>Oldest</option><option value="title"${state.sort === 'title' ? ' selected' : ''}>Title</option>`)}</label><button type="button" data-topic-paper-reset>Reset</button></section>`;
}
export function mountTopicPapers(container: HTMLElement, data: TopicPapersData, rawQuery: string) {
  const tab = new URLSearchParams(rawQuery).get('tab') === 'papers' ? 'papers' : 'overview'; const state = normalizeTopicPaperState(rawQuery, data.papers); const expanded = readExpanded(data.topic.slug); const years = topicYears(data.papers); const venues = topicVenues(data.papers);
  if (tab === 'papers') state.page = Math.min(state.page, Math.max(1, Math.ceil(filterTopicPapers(data.papers, state).length / PAGE_SIZE)));
  const canonical = topicRoute(data.topic.slug, tab, tab === 'papers' ? state : undefined);
  if (`#/${location.hash.replace(/^#\/?/, '')}` !== canonical) history.replaceState(null, '', canonical);
  container.innerHTML = `<article class="detail topic-reader${tab === 'papers' ? ' topic-reader--papers' : ''}" data-topic-papers-ready="true"><a class="crumb" data-restore-scroll href="#/topics">← All topics</a><p class="kicker">${esc(data.topic.area || 'TOPIC')}</p><h1>${esc(data.topic.title)}</h1>${tab === 'overview' ? `<p class="detail-lede">${esc(data.topic.overview || '')}</p>` : ''}<nav class="topic-reader-tabs" aria-label="Topic sections"><a href="${topicRoute(data.topic.slug, 'overview')}"${tab === 'overview' ? ' aria-current="page"' : ''}>Overview</a><a href="${topicRoute(data.topic.slug, 'papers', { ...state, page: 1 })}"${tab === 'papers' ? ' aria-current="page"' : ''}>All papers (${data.topic.paperCount})</a></nav><div data-topic-paper-content></div></article>`;
  const content = container.querySelector<HTMLElement>('[data-topic-paper-content]')!;
  const bindDetails = (target: ParentNode) => target.querySelectorAll<HTMLDetailsElement>('details[data-topic-paper-tldr]').forEach((details) => details.addEventListener('toggle', () => { const slug = details.dataset.topicPaperTldr!; if (details.open) expanded.add(slug); else expanded.delete(slug); saveExpanded(data.topic.slug, expanded); }));
  const renderList = () => {
    const filtered = filterTopicPapers(data.papers, state); const pageCount = Math.max(1, Math.ceil(filtered.length / PAGE_SIZE)); state.page = Math.min(state.page, pageCount); const visible = filtered.slice((state.page - 1) * PAGE_SIZE, state.page * PAGE_SIZE);
    const results = content.querySelector<HTMLElement>('[data-topic-paper-results]')!;
    results.innerHTML = `<p class="topic-paper-count" aria-live="polite">${filtered.length} of ${data.papers.length} papers</p><section class="topic-paper-list">${visible.map((paper) => paperRow(paper, expanded, topicRoute(data.topic.slug, 'papers', state))).join('') || '<p class="empty">No papers match these filters.</p>'}</section>${pageCount > 1 ? `<nav class="topic-paper-pages"><button type="button"${state.page === 1 ? ' disabled' : ''} data-topic-paper-page="${state.page - 1}">Previous</button><span>Page ${state.page} of ${pageCount}</span><button type="button"${state.page === pageCount ? ' disabled' : ''} data-topic-paper-page="${state.page + 1}">Next</button></nav>` : ''}`;
    results.querySelectorAll<HTMLButtonElement>('[data-topic-paper-page]').forEach((button) => button.addEventListener('click', () => update({ page: Number(button.dataset.topicPaperPage) })));
    bindDetails(results);
  };
  const syncControls = () => {
    content.querySelector<HTMLInputElement>('[data-topic-paper-search]')!.value = state.q;
    content.querySelector<HTMLSelectElement>('[data-topic-paper-year]')!.value = state.year;
    content.querySelector<HTMLSelectElement>('[data-topic-paper-venue]')!.value = state.venue;
    content.querySelector<HTMLSelectElement>('[data-topic-paper-sort]')!.value = state.sort;
  };
  const update = (patch: Partial<TopicPaperState>) => {
    const previousPage = state.page;
    Object.assign(state, patch);
    const count = Math.max(1, Math.ceil(filterTopicPapers(data.papers, state).length / PAGE_SIZE));
    state.page = Math.min(state.page, count);
    syncControls();
    history.replaceState(null, '', topicRoute(data.topic.slug, 'papers', state));
    renderList();
    if (Object.keys(patch).length === 1 && patch.page !== undefined && state.page !== previousPage) {
      const resultCount = content.querySelector<HTMLElement>('.topic-paper-count')!;
      resultCount.tabIndex = -1;
      resultCount.focus({ preventScroll: true });
      resultCount.scrollIntoView({ block: 'start' });
    }
  };
  if (tab === 'papers') {
    content.innerHTML = `${controlsMarkup(years, venues, state)}<div data-topic-paper-results></div>`;
    const search = content.querySelector<HTMLInputElement>('[data-topic-paper-search]')!;
    search.addEventListener('input', () => update({ q: search.value, page: 1 }));
    content.querySelector<HTMLSelectElement>('[data-topic-paper-year]')!.addEventListener('change', (event) => update({ year: (event.target as HTMLSelectElement).value, page: 1 }));
    content.querySelector<HTMLSelectElement>('[data-topic-paper-venue]')!.addEventListener('change', (event) => update({ venue: (event.target as HTMLSelectElement).value, page: 1 }));
    content.querySelector<HTMLSelectElement>('[data-topic-paper-sort]')!.addEventListener('change', (event) => update({ sort: (event.target as HTMLSelectElement).value as TopicPaperState['sort'], page: 1 }));
    content.querySelector('[data-topic-paper-reset]')!.addEventListener('click', () => update({ q: '', year: '', venue: '', sort: 'newest', page: 1 }));
    renderList();
  } else { content.innerHTML = overviewMarkup(data, topicRoute(data.topic.slug, 'overview')); }
  return { canonicalRoute: canonical.replace(/^#\//, '') };
}
