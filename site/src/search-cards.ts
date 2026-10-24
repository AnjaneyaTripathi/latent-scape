import { atlas, type Paper, type SearchDoc, type Summary } from './data';

const esc = (value = '') => value.replace(/[&<>'"]/g, (char) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', "'": '&#39;', '"': '&quot;' })[char]!);
const href = (path: string) => `#/${encodeURIComponent(path).replace(/%2F/g, '/')}`;
let nextDialogId = 0;

type Source = { url?: string; originalUrl?: string; sourceLinkLabel?: string };
function sourceLink(paper: Source) {
  const url = paper.originalUrl || paper.url;
  return url ? `<a class="search-summary-source" href="${esc(url)}" target="_blank" rel="noreferrer">${esc(paper.sourceLinkLabel || 'Original paper')} ↗</a>` : '<span class="search-summary-fallback">Original source unavailable</span>';
}
function attribution(paper: Source) {
  return `<span class="search-summary-attribution"><span class="ai-label">AI-generated summary</span>${sourceLink(paper)}</span>`;
}
function paragraphs(summary: Summary) {
  if (summary.paragraphs?.length) return summary.paragraphs;
  return [summary.problem, summary.idea, summary.results, summary.limitations].filter((value): value is string => Boolean(value));
}
function metadata(paper: { venue?: string; year?: number }) {
  return [paper.venue || 'Paper', paper.year].filter(Boolean).map((value) => esc(String(value))).join(' · ');
}
function topicCard(doc: SearchDoc) {
  return `<article class="card search-result-card topic" data-search-card data-topic-slug="${esc(doc.slug)}"><p class="eyebrow">Topic</p><h3><a href="${href(`topic/${doc.slug}`)}">${esc(doc.title)}</a></h3><p class="search-topic-description">${esc(doc.description || 'Explore this part of the research landscape.')}</p><span class="arrow" aria-hidden="true">→</span></article>`;
}
function paperCard(doc: SearchDoc, dialogId: string) {
  return `<article class="card search-result-card paper" data-search-card data-paper-slug="${esc(doc.slug)}"><p class="eyebrow">${metadata(doc)}</p><h3><a href="${href(`paper/${doc.slug}`)}">${esc(doc.title)}</a></h3><p class="search-result-tldr">${esc(doc.tldr || 'A summary is unavailable for this paper.')}</p><div class="search-result-attribution">${doc.tldr ? attribution(doc) : sourceLink(doc)}</div><div class="search-result-actions"><button type="button" class="search-summary-toggle" data-summary-toggle aria-haspopup="dialog" aria-controls="${dialogId}">Read summary <span aria-hidden="true">→</span></button></div></article>`;
}
function paperHeading(paper: SearchDoc | Paper, titleId: string) {
  const authors = 'authors' in paper ? `<p class="search-dialog-authors">${esc(paper.authors?.join(', ') || 'Authors unavailable')}</p>` : '';
  return `<p class="eyebrow">${metadata(paper)}</p><h2 id="${titleId}" class="search-dialog-title">${esc(paper.title)}</h2>${authors}<div class="search-dialog-links">${sourceLink(paper)}<a href="${href(`paper/${paper.slug}`)}">Open paper page →</a></div>`;
}
function summaryMarkup(paper: Paper) {
  const summary = paper.summary;
  const explanation = summary ? paragraphs(summary).map((paragraph) => `<p>${esc(paragraph)}</p>`).join('') : '';
  const summaryView = summary ? `<section class="search-dialog-summary"><h3 class="search-summary-heading">TL;DR</h3>${attribution(paper)}<p class="search-summary-tldr">${esc(summary.tldr)}</p></section><section class="search-summary-narrative"><h3 class="search-summary-heading">What this paper did</h3>${attribution(paper)}${explanation || '<p>An explanation is unavailable for this paper.</p>'}<p class="search-summary-note">Based on the official abstract. Read the original paper for methods, evidence, and full context.</p></section>` : `<section class="search-dialog-summary"><p class="search-summary-fallback">An AI-generated explanation is unavailable for this paper. Read the original paper for detail.</p></section>`;
  const abstract = paper.originalAbstract ?? paper.abstract;
  const awaitingVerification = paper.abstractStatus === 'needs-source-recovery' || paper.abstractQuality === 'official-landing-page-possibly-full-text';
  return `${summaryView}<section class="search-summary-abstract"><h3 class="search-summary-heading">${awaitingVerification ? 'Source text awaiting abstract verification' : 'Original abstract'}</h3><p class="search-abstract-text">${esc(abstract || 'Unavailable from the official source in this snapshot.')}</p><p class="search-summary-note">${awaitingVerification ? 'This text needs confirmation against the original paper. ' : ''}Source link. ${sourceLink(paper)}</p></section>`;
}

/** Freeze the page on mobile too; restore only the inline properties we own. */
function lockPageScroll() {
  const position = { left: window.scrollX, top: window.scrollY };
  const body = document.body;
  const root = document.documentElement;
  const properties = ['position', 'top', 'left', 'width', 'overflow', 'padding-right'];
  const saved = properties.map((property) => [property, body.style.getPropertyValue(property), body.style.getPropertyPriority(property)]);
  const rootOverflow = [root.style.getPropertyValue('overflow'), root.style.getPropertyPriority('overflow')];
  const scrollbar = window.innerWidth - root.clientWidth;
  const padding = getComputedStyle(body).paddingRight;
  body.style.position = 'fixed';
  body.style.top = `-${position.top}px`;
  body.style.left = `-${position.left}px`;
  body.style.width = '100%';
  body.style.overflow = 'hidden';
  if (scrollbar > 0) body.style.paddingRight = `calc(${padding} + ${scrollbar}px)`;
  root.style.overflow = 'hidden';
  return () => {
    for (const [property, value, priority] of saved) {
      if (value) body.style.setProperty(property, value, priority);
      else body.style.removeProperty(property);
    }
    if (rootOverflow[0]) root.style.setProperty('overflow', rootOverflow[0], rootOverflow[1]);
    else root.style.removeProperty('overflow');
    window.scrollTo({ ...position, behavior: 'instant' });
  };
}

export interface SearchCardController { render(documents: SearchDoc[]): void; dispose(): void }

/** Search records stay compact. A paper shard loads only on an explicit click. */
export function mountSearchCards(container: HTMLElement): SearchCardController {
  const dialogId = `search-paper-dialog-${++nextDialogId}`;
  const titleId = `${dialogId}-title`;
  const dialog = document.createElement('dialog');
  dialog.id = dialogId;
  dialog.className = 'search-paper-dialog';
  dialog.dataset.paperDialog = '';
  dialog.setAttribute('aria-labelledby', titleId);
  dialog.innerHTML = '<div class="search-dialog-bar"><span>Paper details</span><button type="button" data-dialog-close aria-label="Close paper details" autofocus>×</button></div><div class="search-dialog-body" data-dialog-body tabindex="0"></div>';
  document.body.append(dialog);
  const body = dialog.querySelector<HTMLElement>('[data-dialog-body]')!;
  const closeButton = dialog.querySelector<HTMLButtonElement>('[data-dialog-close]')!;
  let disposed = false;
  let hasPainted = false;
  let requestVersion = 0;
  let activeDocument: SearchDoc | undefined;
  let opener: HTMLButtonElement | undefined;
  let unlockScroll: (() => void) | undefined;
  let documentsBySlug = new Map<string, SearchDoc>();
  let pendingDocuments: SearchDoc[] | undefined;

  const paint = (documents: SearchDoc[]) => {
    documentsBySlug = new Map(documents.filter((doc) => doc.type === 'paper').map((doc) => [doc.slug, doc]));
    container.innerHTML = documents.map((doc) => doc.type === 'paper' ? paperCard(doc, dialogId) : topicCard(doc)).join('');
    hasPainted = true;
  };
  const flushPending = () => {
    if (disposed || !pendingDocuments || activeDocument || container.contains(document.activeElement)) return;
    const next = pendingDocuments;
    pendingDocuments = undefined;
    paint(next);
  };
  const close = (restoreFocus = true) => {
    if (!activeDocument && !unlockScroll) return;
    requestVersion++;
    activeDocument = undefined;
    delete container.dataset.paperDialogOpen;
    delete dialog.dataset.paperSlug;
    if (dialog.open) dialog.close();
    unlockScroll?.();
    unlockScroll = undefined;
    if (restoreFocus && opener?.isConnected) opener.focus({ preventScroll: true });
    opener = undefined;
    body.innerHTML = '';
    queueMicrotask(flushPending);
  };
  const load = async (doc: SearchDoc) => {
    const version = ++requestVersion;
    body.innerHTML = `${paperHeading(doc, titleId)}<p class="search-summary-loading" role="status">Loading paper details…</p>`;
    body.scrollTop = 0;
    try {
      const paper = await atlas.paper(doc.slug);
      if (disposed || version !== requestVersion || !dialog.open || activeDocument?.slug !== doc.slug) return;
      body.innerHTML = paper ? `${paperHeading(paper, titleId)}${summaryMarkup(paper)}` : `${paperHeading(doc, titleId)}<p class="search-summary-fallback" role="status">This paper record is unavailable in the current snapshot. Read the original paper for detail.</p><button type="button" class="search-summary-retry" data-summary-retry>Retry</button>`;
    } catch {
      if (disposed || version !== requestVersion || !dialog.open || activeDocument?.slug !== doc.slug) return;
      body.innerHTML = `${paperHeading(doc, titleId)}<p class="search-summary-fallback" role="status">Paper details could not load. Please try again.</p><button type="button" class="search-summary-retry" data-summary-retry>Retry</button>`;
    }
  };
  const onClick = (event: MouseEvent) => {
    const button = event.target instanceof Element ? event.target.closest<HTMLButtonElement>('[data-summary-toggle]') : null;
    const slug = button?.closest<HTMLElement>('[data-search-card]')?.dataset.paperSlug;
    const doc = slug ? documentsBySlug.get(slug) : undefined;
    if (!button || !doc || disposed) return;
    event.preventDefault();
    opener = button;
    activeDocument = doc;
    container.dataset.paperDialogOpen = 'true';
    dialog.dataset.paperSlug = doc.slug;
    if (!unlockScroll) unlockScroll = lockPageScroll();
    // Populate the accessible title before entering the browser's modal top layer.
    body.innerHTML = paperHeading(doc, titleId);
    if (!dialog.open) dialog.showModal();
    closeButton.focus({ preventScroll: true });
    void load(doc);
  };
  const onDialogClick = (event: MouseEvent) => {
    const target = event.target instanceof Element ? event.target : null;
    if (target?.closest('[data-dialog-close]')) { close(); return; }
    if (target?.closest('[data-summary-retry]') && activeDocument) {
      closeButton.focus({ preventScroll: true });
      void load(activeDocument);
      return;
    }
    // Restore the page before the router records its scroll position.
    const link = target?.closest<HTMLAnchorElement>('a[href]');
    if (link?.getAttribute('href')?.startsWith('#/') && event.button === 0 && !event.metaKey && !event.ctrlKey && !event.shiftKey && !event.altKey) close(false);
  };
  const onCancel = (event: Event) => { event.preventDefault(); close(); };
  const onClose = () => { if (!dialog.open) close(); };
  const onDialogKeydown = (event: KeyboardEvent) => {
    if (!dialog.open || event.key !== 'Tab' || event.altKey || event.ctrlKey || event.metaKey) return;
    const focusable = [...dialog.querySelectorAll<HTMLElement>('a[href], button, input, select, textarea, [tabindex]')]
      .filter((element) => element.tabIndex >= 0 && !element.matches(':disabled') && element.getClientRects().length > 0 && getComputedStyle(element).visibility !== 'hidden');
    if (!focusable.length) return;
    const current = focusable.indexOf(document.activeElement as HTMLElement);
    const next = current < 0 ? (event.shiftKey ? focusable.length - 1 : 0)
      : (current + (event.shiftKey ? -1 : 1) + focusable.length) % focusable.length;
    // Safari may skip links in its native Tab order. Keep every visible action
    // reachable and focus inside the modal in both keyboard navigation modes.
    event.preventDefault();
    focusable[next].focus();
  };
  const onFocusOut = () => queueMicrotask(flushPending);
  container.addEventListener('click', onClick);
  container.addEventListener('focusout', onFocusOut);
  dialog.addEventListener('click', onDialogClick);
  dialog.addEventListener('cancel', onCancel);
  dialog.addEventListener('close', onClose);
  dialog.addEventListener('keydown', onDialogKeydown);
  return {
    render(documents) {
      if (disposed) return;
      if (hasPainted && (activeDocument || container.contains(document.activeElement))) { pendingDocuments = documents; return; }
      pendingDocuments = undefined;
      paint(documents);
    },
    dispose() {
      if (disposed) return;
      disposed = true;
      pendingDocuments = undefined;
      close(false);
      container.removeEventListener('click', onClick);
      container.removeEventListener('focusout', onFocusOut);
      dialog.removeEventListener('click', onDialogClick);
      dialog.removeEventListener('cancel', onCancel);
      dialog.removeEventListener('close', onClose);
      dialog.removeEventListener('keydown', onDialogKeydown);
      dialog.remove();
    },
  };
}
