#!/usr/bin/env node
/**
 * Focused production acceptance test for Discover search-result summaries.
 *
 * Usage: node scripts/search-cards-smoke.mjs site/dist [--webkit]
 *
 * The server returns a compact real-record search index fixture so the interaction is
 * exercised without loading the corpus-wide search index or semantic model.
 * Paper detail shards remain the real built records: opening a result therefore
 * proves that the published explanation and source abstract are rendered
 * verbatim on demand. No browser corpus-wide index or model is required.
 */
import { createRequire } from 'node:module';
import { createServer } from 'node:http';
import { access, readFile, stat } from 'node:fs/promises';
import { constants } from 'node:fs';
import { createHash } from 'node:crypto';
import { extname, join, normalize, resolve } from 'node:path';

const require = createRequire(import.meta.url);
const moduleRoot = process.env.LATENTSCAPE_PLAYWRIGHT_ROOT || '/Users/anjantri/Desktop/dust/node_modules';
let chromium; let webkit;
try { ({ chromium, webkit } = require(join(moduleRoot, 'playwright'))); } catch (error) {
  console.error(`Playwright was not found at ${moduleRoot}. Set LATENTSCAPE_PLAYWRIGHT_ROOT.`); throw error;
}
const useWebKit = process.argv.includes('--webkit');
let webkitAvailable = false;
try { await access(webkit.executablePath(), constants.R_OK); webkitAvailable = true; } catch { /* optional */ }
if (useWebKit && !webkitAvailable) throw new Error('Playwright WebKit is not installed. Run without --webkit or install it.');
const artifact = resolve(process.argv.slice(2).find((value) => value !== '--webkit') || 'site/dist');
const suffix = useWebKit ? '-webkit' : '';
const indexHtml = await readFile(join(artifact, 'index.html'), 'utf8');
const deploymentLinks = [...indexHtml.matchAll(/\b(?:src|href)="([^"]+)"/g)].map((match) => match[1]).filter((value) => /(?:^|\/)(?:assets\/|favicon\.svg(?:$|\?))/.test(value));
if (!deploymentLinks.length || deploymentLinks.some((value) => !value.startsWith('/latent-scape/'))) throw new Error(`artifact is not built for /latent-scape/: ${deploymentLinks.join(', ') || 'no asset links'}`);

const manifest = JSON.parse(await readFile(join(artifact, 'data', 'manifest.json'), 'utf8'));
const fullSearch = JSON.parse(await readFile(join(artifact, 'data', 'search.json'), 'utf8'));
const prefixFor = (slug) => createHash('sha256').update(slug).digest('hex').slice(0, 2);
const shardFor = (slug) => manifest.shards?.papers?.[prefixFor(slug)];
const shardCache = new Map();
async function paperFor(slug) {
  const path = shardFor(slug);
  if (!path) return undefined;
  if (!shardCache.has(path)) shardCache.set(path, JSON.parse(await readFile(join(artifact, 'data', path), 'utf8')));
  return shardCache.get(path).find((paper) => paper.slug === slug);
}

const sampleTitles = [
  'Pruning Randomly Initialized Neural Networks with Iterative Randomization',
  'From Algorithmic to Subjective Randomness',
];
const detailedDoc = sampleTitles.map((title) => fullSearch.find((doc) => doc.type === 'paper' && doc.title === title)).find(Boolean);
const detailed = detailedDoc && await paperFor(detailedDoc.slug);
if (!detailed?.summary?.tldr || !detailed.summary.paragraphs?.length) throw new Error('The screenshot sample paper must have a complete narrative summary.');
let missing;
for (const filename of Object.values(manifest.shards?.papers || {})) {
  if (`/data/${filename}` === `/data/${shardFor(detailed.slug)}`) continue;
  const papers = JSON.parse(await readFile(join(artifact, 'data', filename), 'utf8'));
  missing = papers.find((paper) => !paper.summary && String(paper.originalAbstract ?? paper.abstract ?? '').trim().length > 80
    && !(paper.topicSlugs || []).some((slug) => (detailed.topicSlugs || []).includes(slug))
    && fullSearch.some((doc) => doc.type === 'paper' && doc.slug === paper.slug));
  if (missing) break;
}
if (!missing) throw new Error('Search-card smoke requires a summary-unavailable paper in a different shard and topic.');
const missingDoc = fullSearch.find((doc) => doc.type === 'paper' && doc.slug === missing.slug);
const topicDoc = fullSearch.find((doc) => doc.type === 'topic' && typeof doc.description === 'string' && doc.description.length > 210);
if (!missingDoc || !topicDoc) throw new Error('Search-card smoke could not make a compact real-data search fixture.');
if (detailedDoc.tldr !== detailed.summary.tldr) throw new Error('Built search index must contain the exact complete screenshot paper TL;DR. Rebuild the content projection.');
// The shared token gives two cards in one row; the unique unbroken token makes
// exactly one lexical hit for the deferred semantic ranking regression.
const layoutQuery = 'qzxlayoutfixture';
const semanticQuery = 'qzxuniquenarrativefixture';
const fixtureIndex = [
  { ...detailedDoc, abstract: `${detailedDoc.abstract || ''} ${layoutQuery} ${semanticQuery}` },
  { ...missingDoc, abstract: `${missingDoc.abstract || ''} ${layoutQuery}` },
  topicDoc,
];
const detailedShard = `/data/${shardFor(detailed.slug)}`;
const missingShard = `/data/${shardFor(missing.slug)}`;
const mime = { '.css': 'text/css', '.html': 'text/html', '.js': 'text/javascript', '.json': 'application/json', '.mjs': 'text/javascript', '.svg': 'image/svg+xml', '.wasm': 'application/wasm', '.onnx': 'application/octet-stream' };
let failDetailedFetch = true;
let detailedFetches = 0;
let delayDetailedFetch = true;
const intentionalFailures = new Set();
let delayMissingFetch = false;
let missingFetches = 0;
const server = createServer(async (request, response) => {
  let pathname = decodeURIComponent(new URL(request.url, 'http://local').pathname);
  if (pathname === '/latent-scape' || pathname === '/latent-scape/') pathname = '/';
  else if (pathname.startsWith('/latent-scape/')) pathname = pathname.slice('/latent-scape'.length);
  if (pathname === '/data/search.json') {
    response.writeHead(200, { 'content-type': 'application/json', 'cache-control': 'no-store' }); response.end(JSON.stringify(fixtureIndex)); return;
  }
  // The semantic worker is deliberately outside this narrow interaction test.
  // Failing its local index immediately retains the lexical fixture result.
  if (pathname === '/data/vectors.json' || pathname === '/data/topics.json') { intentionalFailures.add(pathname); response.writeHead(503); response.end('fixture omits semantic index'); return; }
  if (pathname === detailedShard) {
    detailedFetches += 1;
    if (delayDetailedFetch) await new Promise((done) => setTimeout(done, 350));
    if (failDetailedFetch) { intentionalFailures.add(pathname); response.writeHead(503, { 'cache-control': 'no-store' }); response.end('temporary fixture failure'); return; }
  }
  if (pathname === missingShard) {
    missingFetches += 1;
    if (delayMissingFetch) await new Promise((done) => setTimeout(done, 700));
  }
  const candidate = normalize(join(artifact, pathname === '/' ? 'index.html' : pathname));
  const file = candidate.startsWith(artifact) ? candidate : join(artifact, 'index.html');
  try { const target = (await stat(file)).isFile() ? file : join(artifact, 'index.html'); response.writeHead(200, { 'content-type': mime[extname(target)] || 'application/octet-stream', 'cache-control': 'no-store' }); response.end(await readFile(target)); }
  catch { response.writeHead(404); response.end('not found'); }
});
await new Promise((done) => server.listen(0, '127.0.0.1', done));
const base = `http://127.0.0.1:${server.address().port}`;
const url = (query) => `${base}/latent-scape/#/search?q=${encodeURIComponent(query)}`;
const problems = [];
const check = (condition, message) => { if (!condition) problems.push(message); };
const sourceText = (paper) => paper.originalAbstract ?? paper.abstract;
const summaryParagraphs = (paper) => paper.summary?.paragraphs?.length ? paper.summary.paragraphs : [paper.summary?.problem, paper.summary?.idea, paper.summary?.results, paper.summary?.limitations].filter(Boolean);

// HTML parsing normalizes carriage returns; preserve every other source byte.
const htmlText = (value) => String(value ?? '').replace(/\r\n?/g, '\n');
const browser = await (useWebKit ? webkit : chromium).launch({ headless: true });
let context;
try {
  // Keep server-controlled failures and delays deterministic: a prior shard
  // must not bypass this fixture through the service worker's static cache.
  // Atlas's in-memory cache is still exercised; browser-smoke covers real SW/offline.
  context = await browser.newContext({ viewport: { width: 1440, height: 920 }, serviceWorkers: 'block' });
  const page = await context.newPage(); const external = []; const requests = [];
  const localPath = (value) => { try { return new URL(value).pathname.replace(/^\/latent-scape(?=\/)/, ''); } catch { return ''; } };
  page.on('pageerror', (error) => problems.push(`page error: ${error.message}`));
  page.on('console', (message) => {
    if (message.type() !== 'error') return;
    const expected = /503/.test(message.text()) && intentionalFailures.has(localPath(message.location().url));
    if (!expected) problems.push(`console: ${message.text()} (${message.location().url || 'no URL'})`);
  });
  page.on('response', (response) => {
    if (response.status() >= 400 && !(response.status() === 503 && intentionalFailures.has(localPath(response.url())))) problems.push(`unexpected HTTP ${response.status()}: ${response.url()}`);
  });
  context.on('request', (request) => { const requestUrl = new URL(request.url()); if (requestUrl.origin !== base && !requestUrl.protocol.startsWith('data')) external.push(request.url()); else if (requestUrl.origin === base) requests.push(requestUrl.pathname); });
  const results = page.locator('.results[data-search-results]');
  const card = (slug) => page.locator(`[data-search-card][data-paper-slug="${slug}"]`);
  const trigger = (slug) => card(slug).locator('button[data-summary-toggle]');
  const dialog = (slug) => page.locator(`dialog[data-paper-dialog][data-paper-slug="${slug}"]`);
  const ready = async (slug) => { await results.waitFor(); await card(slug).waitFor({ timeout: 15_000 }); };
  const noOverflow = async (name) => { const size = await page.evaluate(() => ({ scroll: document.documentElement.scrollWidth, viewport: innerWidth })); check(size.scroll <= size.viewport + 1, `${name} has horizontal overflow (${size.scroll} > ${size.viewport})`); };
  const search = async (query, slug) => { await page.goto(url(query), { waitUntil: 'domcontentloaded' }); await ready(slug); };
  const open = async (slug) => { await trigger(slug).click(); await dialog(slug).waitFor(); check(await dialog(slug).getAttribute('open') !== null, 'paper dialog is not a native open modal'); };
  const loaded = async (slug) => { await dialog(slug).locator('.search-summary-tldr').waitFor({ timeout: 8_000 }); };
  const heights = async () => results.locator('[data-search-card]').evaluateAll((cards) => cards.map((item) => ({ slug: item.getAttribute('data-paper-slug'), height: item.getBoundingClientRect().height })));
  const stableHeights = (before, after, label) => check(before.length === after.length && before.every((item, index) => item.slug === after[index]?.slug && Math.abs(item.height - after[index].height) < 1), `${label} changed compact card heights`);
  const verifyText = async (paper) => {
    const panel = dialog(paper.slug);
    check(await panel.locator('.search-summary-tldr').textContent() === htmlText(paper.summary.tldr), 'dialog truncates or changes the complete AI TL;DR');
    const paragraphs = await panel.locator('.search-summary-narrative p').allTextContents();
    for (const paragraph of summaryParagraphs(paper)) check(paragraphs.includes(htmlText(paragraph)), 'dialog changes or omits an exact narrative paragraph');
    check(await panel.locator('.search-abstract-text').textContent() === htmlText(sourceText(paper)), 'dialog changes or omits the complete original source abstract');
    check(await panel.getByText('AI-generated summary', { exact: true }).count() > 0, 'dialog is missing its AI-generated label');
    const links = await panel.locator('.search-summary-source').evaluateAll((items) => items.map((item) => item.getAttribute('href')));
    check(links.length > 0 && links.every((link) => link === (paper.originalUrl || paper.url)), 'dialog source links do not preserve the official source URL');
  };
  const close = async (slug, via = 'button', expectedScroll) => {
    if (via === 'escape') await page.keyboard.press('Escape');
    else await dialog(slug).locator('[data-dialog-close]').click();
    await page.waitForFunction(() => !document.querySelector('dialog[data-paper-dialog][open]'));
    check(await trigger(slug).evaluate((element) => document.activeElement === element), `${via} close did not restore focus to its opening button`);
    check(await results.getAttribute('data-paper-dialog-open') === null, `${via} close left the results dialog-open marker`);
    if (expectedScroll !== undefined) check(Math.abs(await page.evaluate(() => scrollY) - expectedScroll) <= 2, `${via} close did not restore page scroll ${expectedScroll}`);
  };
  const closeVisible = async (slug, label) => {
    const bounds = await dialog(slug).locator('[data-dialog-close]').boundingBox();
    const viewport = page.viewportSize();
    check(Boolean(bounds && bounds.x >= 0 && bounds.y >= 0 && bounds.x + bounds.width <= viewport.width + 1 && bounds.y + bounds.height <= viewport.height + 1), `${label}: close control is outside the viewport`);
  };

  await search(layoutQuery, detailed.slug);
  await ready(missing.slug);
  check(await page.locator('[data-search-result-count]').count() === 1, 'search result count does not have its own live region');
  const front = card(detailed.slug);
  check(await front.locator('.search-result-tldr').textContent() === htmlText(detailed.summary.tldr), 'compact card does not show the exact complete AI TL;DR');
  check(await results.locator('.search-result-abstract').count() === 0, 'compact cards still show source-abstract fragments');
  check(await front.getByText('AI-generated summary', { exact: true }).count() > 0, 'compact TL;DR lacks its AI label');
  const frontSource = front.locator('.search-result-attribution .search-summary-source');
  check(await frontSource.count() === 1, 'paper result has no always-visible source link');
  check(await frontSource.getAttribute('href') === (detailed.originalUrl || detailed.url), 'front source link does not preserve the official source URL');
  const initialHeights = await heights();
  for (const slug of [detailed.slug, missing.slug]) { await trigger(slug).hover(); await page.waitForTimeout(450); }
  check(await page.locator('dialog[data-paper-dialog][open]').count() === 0, 'hover opened a paper dialog');
  check(detailedFetches === 0 && missingFetches === 0, 'hover fetched a detail shard');
  check(!requests.some((path) => path.endsWith(detailedShard) || path.endsWith(missingShard)), 'compact results eagerly loaded a paper shard');
  stableHeights(initialHeights, await heights(), 'hover');
  await page.screenshot({ path: `/tmp/latentscape-search-compact${suffix}.png` });

  await open(detailed.slug);
  await dialog(detailed.slug).locator('.search-summary-loading').waitFor();
  await dialog(detailed.slug).locator('[data-summary-retry]').waitFor({ timeout: 8_000 });
  check(detailedFetches === 1, `explicit open did not make exactly one request (${detailedFetches})`);
  failDetailedFetch = false; delayDetailedFetch = false;
  await dialog(detailed.slug).locator('[data-summary-retry]').click();
  await loaded(detailed.slug); await verifyText(detailed);
  check(detailedFetches === 2, `retry did not make one replacement request (${detailedFetches})`);
  stableHeights(initialHeights, await heights(), 'opening the modal');
  check(await results.getAttribute('data-paper-dialog-open') !== null, 'open dialog did not mark its result container');
  await closeVisible(detailed.slug, 'desktop long-title dialog');
  await page.screenshot({ path: `/tmp/latentscape-search-summary${suffix}.png` });
  // Native dialog tab navigation must remain inside the modal in both directions.
  await dialog(detailed.slug).locator('[data-dialog-close]').focus();
  const focusableCount = await dialog(detailed.slug).locator('a[href], button:not([disabled]), [tabindex="0"]').count();
  for (const key of ['Tab', 'Shift+Tab']) {
    for (let index = 0; index < focusableCount + 2; index += 1) {
      await page.keyboard.press(key);
      check(await dialog(detailed.slug).evaluate((element) => element.contains(document.activeElement)), `${key} escaped modal focus`);
    }
  }
  await close(detailed.slug);
  stableHeights(initialHeights, await heights(), 'closing the modal');
  await trigger(detailed.slug).focus(); await page.keyboard.press('Enter'); await loaded(detailed.slug);
  check(detailedFetches === 2, 'reopening a loaded summary refetched its shard');
  await close(detailed.slug, 'escape');

  await page.goto(url(topicDoc.title), { waitUntil: 'domcontentloaded' }); await results.waitFor();
  const topic = page.locator(`[data-search-card][data-topic-slug="${topicDoc.slug}"]`);
  await topic.waitFor({ timeout: 8_000 });
  check(await topic.locator('.search-topic-description').textContent() === htmlText(topicDoc.description), 'topic result description is clipped or changed');

  await search(missing.title, missing.slug);
  delayMissingFetch = true;
  await open(missing.slug);
  await dialog(missing.slug).locator('.search-summary-loading').waitFor();
  await close(missing.slug);
  await page.waitForTimeout(850);
  check(await page.locator('dialog[data-paper-dialog][open]').count() === 0, 'a completed request reopened a dialog closed while pending');
  delayMissingFetch = false;
  await open(missing.slug);
  await dialog(missing.slug).locator('.search-summary-fallback').waitFor({ timeout: 8_000 });
  check(/AI-generated.*unavailable/i.test(await dialog(missing.slug).locator('.search-summary-fallback').textContent()), 'unavailable summary does not state that its AI explanation is unavailable');
  check(await dialog(missing.slug).locator('.search-summary-tldr').count() === 0, 'summary-unavailable result invents an AI TL;DR');
  check(await dialog(missing.slug).locator('.search-abstract-text').textContent() === htmlText(sourceText(missing)), 'unavailable summary dialog omits or changes its source abstract');
  check(await dialog(missing.slug).locator('.search-summary-source').first().getAttribute('href') === (missing.originalUrl || missing.url), 'unavailable summary has an inaccurate source link');
  check(missingFetches === 1, `reopening a closed completed request refetched its shard (${missingFetches})`);
  await close(missing.slug, 'escape');

  for (const width of [390, 320]) {
    await page.setViewportSize({ width, height: 844 });
    await search(detailed.title, detailed.slug);
    // Routine ranking can replace a lexical card between scroll and focus in
    // WebKit. Settle only this mobile layout setup; the later rerank regression
    // intentionally keeps semantic ranking pending while the dialog is open.
    await page.waitForFunction(() => {
      const count = document.querySelector('[data-search-result-count]');
      return count && !count.textContent.includes('Loading semantic search');
    });
    for (const theme of ['light', 'dark']) {
      await page.evaluate((value) => { document.documentElement.dataset.theme = value; }, theme);
      await trigger(detailed.slug).scrollIntoViewIfNeeded();
      // Ensure a genuine scrolled-page regression, even if the compact card fits.
      await page.evaluate(() => { document.body.style.minHeight = '1800px'; window.scrollTo(0, 160); });
      await trigger(detailed.slug).evaluate((element) => element.focus({ preventScroll: true }));
      const beforeScroll = await page.evaluate(() => scrollY);
      check(beforeScroll > 0, `${width}px ${theme} scroll fixture did not scroll`);
      const beforeHeights = await heights();
      // Keyboard activation avoids Playwright scrolling the opening button.
      await page.keyboard.press('Enter'); await loaded(detailed.slug);
      await noOverflow(`${width}px ${theme} modal`); await closeVisible(detailed.slug, `${width}px ${theme} long-title dialog`);
      stableHeights(beforeHeights, await heights(), `${width}px ${theme} modal`);
      const body = dialog(detailed.slug).locator('[data-dialog-body]');
      const bodySize = await body.evaluate((element) => ({ scroll: element.scrollHeight, client: element.clientHeight }));
      check(bodySize.scroll > bodySize.client, `${width}px modal body does not provide internal scrolling`);
      await body.evaluate((element) => { element.scrollTop = element.scrollHeight; });
      await closeVisible(detailed.slug, `${width}px ${theme} internally scrolled dialog`);
      const lockedScroll = await page.evaluate(() => scrollY);
      await page.mouse.move(2, 400); await page.mouse.wheel(0, 500); await page.waitForTimeout(80);
      check(Math.abs(await page.evaluate(() => scrollY) - lockedScroll) <= 1, `${width}px ${theme} backdrop allowed page scrolling`);
      await body.evaluate((element) => { element.scrollTop = 0; });
      await verifyText(detailed);
      if (width === 390 && theme === 'light') await page.screenshot({ path: `/tmp/latentscape-search-summary-mobile${suffix}.png` });
      await page.emulateMedia({ reducedMotion: 'reduce' });
      const transition = await dialog(detailed.slug).evaluate((element) => getComputedStyle(element).transitionDuration);
      check(transition.split(',').every((value) => parseFloat(value) === 0), `reduced-motion dialog still transitions (${transition})`);
      await close(detailed.slug, theme === 'light' ? 'button' : 'escape', beforeScroll);
      await page.evaluate(() => { document.body.style.minHeight = ''; });
    }
  }

  // A semantic response arriving after modal focus must not replace the card,
  // steal focus through finishRoute, or erase the full loaded explanation.
  await page.setViewportSize({ width: 1440, height: 920 });
  await page.evaluate(() => { window.latentscapeSemanticSearch = () => new Promise((resolve) => { window.__latentscapeResolveSemantic = resolve; }); });
  await search(semanticQuery, detailed.slug);
  await page.waitForFunction(() => typeof window.__latentscapeResolveSemantic === 'function');
  check(await card(missing.slug).count() === 0, 'semantic fixture unexpectedly matches the missing-summary paper lexically');
  await open(detailed.slug); await loaded(detailed.slug);
  const focusedSource = dialog(detailed.slug).locator('.search-summary-source').first();
  await focusedSource.focus();
  await page.evaluate((slug) => window.__latentscapeResolveSemantic([{ slug, score: 1 }]), missing.slug);
  await page.waitForTimeout(150);
  check(await card(missing.slug).count() === 0, 'late semantic ranking replaced cards while the dialog was open');
  check(await dialog(detailed.slug).isVisible(), 'late semantic ranking closed the dialog');
  await verifyText(detailed);
  check(await focusedSource.evaluate((element) => document.activeElement === element), 'late semantic ranking stole modal source-link focus');
  await close(detailed.slug, 'escape');
  check(await card(missing.slug).count() === 0, 'late ranking applied before focus left the opening button');
  await page.locator('#search').focus();
  await card(missing.slug).waitFor({ timeout: 8_000 });
  await page.evaluate((slug) => { window.latentscapeSemanticSearch = () => Promise.resolve([{ slug, score: 1 }]); }, detailed.slug);
  await search('qzxsemanticonlyfixturequery', detailed.slug);
  check(await card(detailed.slug).count() === 1, 'semantic-only result did not replace the initial empty keyword state');

  // Reload clears the atlas cache so route cleanup is checked with a real
  // pending detail request, followed by its late completion on another route.
  delayMissingFetch = true;
  await search(missing.title, missing.slug);
  await page.reload({ waitUntil: 'domcontentloaded' }); await ready(missing.slug);
  await open(missing.slug); await dialog(missing.slug).locator('.search-summary-loading').waitFor();
  const oldDialogId = await dialog(missing.slug).getAttribute('id');
  await page.goto(url(topicDoc.title), { waitUntil: 'domcontentloaded' });
  await results.waitFor(); await topic.waitFor(); await page.waitForTimeout(850);
  check(await page.locator(`[id="${oldDialogId}"]`).count() === 0, 'route cleanup left the old paper dialog');
  check(await page.locator('dialog[data-paper-dialog][open], dialog[data-paper-dialog][data-paper-slug]').count() === 0, 'route cleanup left stale dialog paper state');
  check(await page.locator('[data-paper-dialog-open]').count() === 0, 'route cleanup left a dialog-open marker');
  check(await page.locator('.search-summary-tldr, .search-abstract-text').count() === 0, 'late detail response leaked into the next route');
  check((await page.locator('[data-dialog-body]').allTextContents()).every((text) => text === ''), 'next route inherited stale dialog text');
  const lock = await page.evaluate(() => ({ bodyOverflow: getComputedStyle(document.body).overflow, htmlOverflow: getComputedStyle(document.documentElement).overflow, position: getComputedStyle(document.body).position }));
  check(lock.bodyOverflow !== 'hidden' && lock.htmlOverflow !== 'hidden' && lock.position !== 'fixed', 'route cleanup left page scroll locked');
  if (external.length) problems.push(`off-origin browser requests: ${[...new Set(external)].join(', ')}`);
} finally {
  if (context) await context.close();
  await browser.close();
  await new Promise((done) => server.close(done));
}
if (problems.length) { console.error('Search card smoke failed:\n' + problems.map((problem) => `- ${problem}`).join('\n')); process.exitCode = 1; }
else console.log(JSON.stringify({ status: 'ok', browser: useWebKit ? 'webkit' : 'chromium', detailed: detailed.slug, missing: missing.slug, fixtureRecords: fixtureIndex.length }));
