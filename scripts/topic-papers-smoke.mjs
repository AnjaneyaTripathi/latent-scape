#!/usr/bin/env node
/**
 * Production acceptance test for compact topic-paper readers.
 * Usage: node scripts/topic-papers-smoke.mjs site/dist [--webkit]
 * Screenshots: /tmp/latentscape-papers-*.png
 */
import { createRequire } from 'node:module';
import { createServer } from 'node:http';
import { access, readFile, stat } from 'node:fs/promises';
import { constants } from 'node:fs';
import { extname, join, normalize, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

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
const artifact = resolve(process.argv.slice(2).find((arg) => arg !== '--webkit') || 'site/dist');
const papersDir = join(artifact, 'data', 'topic-papers');
const files = (await (await import('node:fs/promises')).readdir(papersDir)).filter((name) => name.endsWith('.json'));
const catalogs = await Promise.all(files.map(async (name) => JSON.parse(await readFile(join(papersDir, name), 'utf8'))));
const problems = [];
const check = (condition, message) => { if (!condition) problems.push(message); };
// A later Playwright timeout would otherwise obscure soft assertions collected
// earlier in the run. Monitoring does not consume the exception or alter exit
// behavior; it only preserves that evidence in the test output.
process.on('uncaughtExceptionMonitor', () => { if (problems.length) console.error('Topic papers smoke assertions before abort:\n' + problems.map((item) => `- ${item}`).join('\n')); });
for (const data of catalogs) {
  check(data?.version === 1, `catalog ${data?.topic?.slug || 'unknown'} has unsupported version`);
  check(Array.isArray(data?.papers) && data.papers.length === data?.topic?.paperCount, `catalog ${data?.topic?.slug || 'unknown'} paperCount disagrees with papers`);
}
const diffusion = catalogs.find((data) => data.topic?.paperCount === 295 && /diffusion/i.test(data.topic?.title || ''));
const largest = catalogs.reduce((best, data) => !best || data.papers.length > best.papers.length ? data : best, null);
const fixture = diffusion || largest;
if (!fixture) throw new Error('No topic-paper catalogs were found in the production artifact.');
// Use the renderer's public text normalization, not its filtering/sorting code.
// The independent expected ordering below still verifies chronology, tie breaks,
// filters, pagination, and identity without counting KaTeX's duplicate DOM text.
const { createServer: createViteServer } = await import(new URL('../site/node_modules/vite/dist/node/index.js', import.meta.url));
const normalizationServer = await createViteServer({ configFile: false, root: fileURLToPath(new URL('../site', import.meta.url)), optimizeDeps: { noDiscovery: true, include: [] }, server: { middlewareMode: true, hmr: false, watch: null }, appType: 'custom', logLevel: 'error' });
let plainText;
try { ({ plainText } = await normalizationServer.ssrLoadModule('/src/rich-text.ts')); }
finally { await normalizationServer.close(); }
const readerText = new Map(fixture.papers.map((paper) => {
  const title = plainText(paper.title); const authors = plainText(paper.authors.join(' ')); const tldr = plainText(paper.tldr || '');
  return [paper.slug, { title, authors, tldr, search: `${title} ${authors} ${tldr}`.toLocaleLowerCase() }];
}));
const slugFromHref = (href) => { const match = (href || '').match(/^#\/paper\/([^?]+)/); return match ? decodeURIComponent(match[1]) : ''; };
const titleOrder = (a, b) => readerText.get(a.slug).title.localeCompare(readerText.get(b.slug).title);

const specialQuery = 'L1 & L2 regularization + % ? κ';
const sourceByLabel = fixture.papers.find((paper) => /publication record/i.test(paper.sourceLinkLabel || ''));
const originalByLabel = fixture.papers.find((paper) => /original paper/i.test(paper.sourceLinkLabel || ''));
const authorProbe = fixture.papers.find((paper) => paper.authors?.length && !readerText.get(paper.slug).title.toLocaleLowerCase().includes(plainText(paper.authors[0]).toLocaleLowerCase()));
const tldrProbe = fixture.papers.find((paper) => paper.tldr && !readerText.get(paper.slug).title.toLocaleLowerCase().includes(readerText.get(paper.slug).tldr.split(/\s+/)[0].toLocaleLowerCase()));
const missingTldr = fixture.papers.find((paper) => !paper.tldr);
const mime = { '.css': 'text/css', '.html': 'text/html', '.js': 'text/javascript', '.json': 'application/json', '.mjs': 'text/javascript', '.svg': 'image/svg+xml', '.woff2': 'font/woff2' };
const server = createServer(async (request, response) => {
  let pathname = decodeURIComponent(new URL(request.url, 'http://local').pathname);
  if (pathname === '/latent-scape' || pathname === '/latent-scape/') pathname = '/';
  else if (pathname.startsWith('/latent-scape/')) pathname = pathname.slice('/latent-scape'.length);
  const candidate = normalize(join(artifact, pathname === '/' ? 'index.html' : pathname));
  const file = candidate.startsWith(artifact) ? candidate : join(artifact, 'index.html');
  try { const target = (await stat(file)).isFile() ? file : join(artifact, 'index.html'); response.writeHead(200, { 'content-type': mime[extname(target)] || 'application/octet-stream', 'cache-control': 'no-store' }); response.end(await readFile(target)); }
  catch { response.writeHead(404); response.end('not found'); }
});
await new Promise((done) => server.listen(0, '127.0.0.1', done));
const base = `http://127.0.0.1:${server.address().port}`;
const suffix = useWebKit ? '-webkit' : '';
const hash = (slug, params = new URLSearchParams()) => `${base}/latent-scape/#/topic/${encodeURIComponent(slug)}${params.toString() ? `?${params}` : ''}`;
const forbidden = /\/data\/(?:topics|search|vectors)\.json$|\/data\/papers\/|\/(?:models|model)\/|\.(?:onnx|wasm)$/;
const browser = await (useWebKit ? webkit : chromium).launch({ headless: true });
let context;
try {
  context = await browser.newContext({ viewport: { width: 1440, height: 920 }, serviceWorkers: 'allow' });
  const page = await context.newPage(); const external = []; const cold = []; let captureCold = true;
  page.on('pageerror', (error) => problems.push(`page error: ${error.message}`));
  page.on('console', (message) => { if (message.type() === 'error') problems.push(`console: ${message.text()}`); });
  context.on('request', (request) => { const url = new URL(request.url()); if (url.origin !== base && !url.protocol.startsWith('data')) external.push(request.url()); else if (captureCold && url.origin === base) cold.push(url.pathname); });
  const reader = page.locator('[data-topic-papers-ready="true"], .topic-reader').first();
  const tabs = page.locator('.topic-reader-tabs');
  const list = page.locator('.topic-paper-list');
  const rows = page.locator('[data-topic-paper-row], .topic-paper-list .topic-paper-row');
  const search = page.locator('[data-topic-paper-search], .topic-paper-controls input').first();
  const controls = page.locator('.topic-paper-controls');
  const getSelect = (name, index) => page.locator(`[data-topic-paper-${name}], .topic-paper-controls select`).nth(index);
  const year = getSelect('year', 0); const venue = getSelect('venue', 1); const sort = getSelect('sort', 2);
  const reset = page.locator('[data-topic-paper-reset], .topic-paper-controls button').first();
  const screenshot = async (name, locator = reader) => { await page.waitForTimeout(150); await locator.screenshot({ path: `/tmp/latentscape-papers-${name}${suffix}.png` }); await page.evaluate(() => scrollTo(0, 0)); await page.screenshot({ path: `/tmp/latentscape-papers-${name}${suffix}-page.png`, fullPage: true }); };
  const viewportScreenshot = async (name) => { await page.evaluate(() => scrollTo(0, 0)); await page.waitForTimeout(100); await page.screenshot({ path: `/tmp/latentscape-papers-${name}${suffix}-viewport.png` }); };
  const titleText = async (locator) => plainText(await locator.evaluate((element) => { const clone = element.cloneNode(true); for (const math of clone.querySelectorAll('.katex')) { const tex = math.querySelector('annotation[encoding="application/x-tex"]'); if (tex) math.replaceWith(document.createTextNode(`$${tex.textContent}$`)); } return clone.textContent || ''; }));
  const ready = async (tab = 'overview', expectedQuery) => { await reader.waitFor(); await page.locator('.topic-reader h1').waitFor(); check(await titleText(page.locator('.topic-reader h1')) === plainText(fixture.topic.title), 'topic reader title differs from its catalog'); if (tab === 'papers') { await controls.waitFor(); if (expectedQuery !== undefined) await page.waitForFunction((query) => document.querySelector('[data-topic-paper-search], .topic-paper-controls input')?.value === query, expectedQuery); } };
  const waitForPaper = async (slug) => { await page.waitForFunction((expected) => decodeURIComponent(location.hash.split('?')[0]) === `#/paper/${expected}`, slug); const heading = page.locator('.paper-detail h1'); await heading.waitFor(); check(await titleText(heading) === readerText.get(slug)?.title, `paper detail heading did not match selected paper: ${slug}`); };
  const waitForScroll = async (position) => page.waitForFunction((top) => Math.abs(scrollY - top) < 80, position, { timeout: 5000 }).catch(() => {});
  const noOverflow = async (name) => { const size = await page.evaluate(() => ({ scroll: document.documentElement.scrollWidth, viewport: innerWidth })); check(size.scroll <= size.viewport + 1, `${name} has horizontal overflow (${size.scroll} > ${size.viewport})`); };
  const paperControlStyle = async () => page.evaluate(() => {
    const controls = [...document.querySelectorAll('.topic-paper-controls input, .topic-paper-controls select, .topic-paper-controls button')];
    const selects = [...document.querySelectorAll('.topic-paper-controls select')];
    const wrappers = [...document.querySelectorAll('.topic-paper-select')];
    return {
      heights: controls.map((control) => Math.round(control.getBoundingClientRect().height)),
      appearances: selects.map((select) => { const style = getComputedStyle(select); return { standard: style.appearance, webkit: style.webkitAppearance }; }),
      chevrons: wrappers.map((wrapper) => getComputedStyle(wrapper, '::after').content),
    };
  });
  const currentRows = async () => rows.evaluateAll((items) => items.map((item) => ({ text: item.textContent || '', title: item.querySelector('h3')?.textContent || '', href: item.querySelector('h3 a')?.getAttribute('href') || '' })));
  const navigationDiagnostic = async (label) => {
    const state = await page.evaluate(() => ({ url: location.href, rows: document.querySelectorAll('[data-topic-paper-row], .topic-paper-list .topic-paper-row').length, count: document.querySelector('.topic-paper-count')?.textContent?.trim() || null, controls: [...document.querySelectorAll('[data-topic-paper-search], [data-topic-paper-year], [data-topic-paper-venue], [data-topic-paper-sort]')].map((control) => ({ name: control.getAttributeNames().find((name) => name.startsWith('data-topic-paper-')) || null, value: control instanceof HTMLInputElement || control instanceof HTMLSelectElement ? control.value : null })), activeElement: document.activeElement instanceof HTMLElement ? { tag: document.activeElement.tagName, data: document.activeElement.getAttributeNames().filter((name) => name.startsWith('data-topic-paper')).join(',') || null } : null }));
    console.error(`topic-paper navigation diagnostic (${label}): ${JSON.stringify(state)}`);
    if (state.rows === 0) await viewportScreenshot(`diagnostic-${label}`);
    return state;
  };
  const waitForExactRows = async (expectedCount, label) => { try { await page.waitForFunction((expected) => document.querySelectorAll('[data-topic-paper-row], .topic-paper-list .topic-paper-row').length === expected, expectedCount, { timeout: 5000 }); } catch { const state = await navigationDiagnostic(label); check(false, `${label} has ${state.rows} rows; expected ${expectedCount}`); } };
  const expected = (query = '', expectedYear = '', expectedVenue = '', expectedSort = 'newest') => fixture.papers.filter((paper) => (!expectedYear || String(paper.year) === expectedYear) && (!expectedVenue || paper.venue === expectedVenue) && (!query || readerText.get(paper.slug).search.includes(query.toLocaleLowerCase()))).sort((a, b) => expectedSort === 'title' ? titleOrder(a, b) : expectedSort === 'oldest' ? a.year - b.year || titleOrder(a, b) : b.year - a.year || titleOrder(a, b));
  const rowSlugs = async () => (await currentRows()).map((item) => slugFromHref(item.href));
  const paperRow = (slug) => rows.filter({ has: page.locator(`h3 a[href^="#/paper/${encodeURIComponent(slug)}?"]`) });

  await page.goto(hash(fixture.topic.slug), { waitUntil: 'networkidle' }); await ready(); captureCold = false;
  check(await tabs.getByText('Overview', { exact: true }).count() === 1, 'Overview tab is absent');
  check(await tabs.getByText(`All papers (${fixture.papers.length})`, { exact: true }).count() === 1, 'All papers tab has an incorrect count');
  check(!cold.some((path) => forbidden.test(path)), `cold overview fetched heavyweight data: ${cold.filter((path) => forbidden.test(path)).join(', ')}`);
  check(cold.some((path) => path.endsWith(`/data/topic-papers/${encodeURIComponent(fixture.topic.slug)}.json`)), 'overview did not fetch its compact topic catalog');
  const overviewCards = page.locator('.topic-paper-preview .paper-card.topic-paper-card');
  const overviewHeaders = page.locator('.topic-paper-preview-head h2');
  check(await overviewHeaders.filter({ hasText: /^Early work$/ }).count() === 1, 'Overview does not restore its Early work section');
  check(await overviewHeaders.filter({ hasText: /^Recent work$/ }).count() === 1, 'Overview does not restore its Recent work section');
  check(await overviewCards.count() === Math.min(6, fixture.papers.length) * 2, 'Overview does not render separate early and recent paper card collections');
  const earlyExpected = expected('', '', '', 'oldest').slice(0, 6).map((paper) => paper.slug);
  const recentExpected = expected().filter((paper) => !earlyExpected.includes(paper.slug)).slice(0, 6).map((paper) => paper.slug);
  const overviewSlugs = (await overviewCards.locator('h3 a').evaluateAll((links) => links.map((link) => link.getAttribute('href')))).map(slugFromHref);
  check(overviewSlugs.slice(0, earlyExpected.length).join('|') === earlyExpected.join('|'), 'Overview early cards are not chronological');
  check(overviewSlugs.slice(earlyExpected.length).join('|') === recentExpected.join('|'), 'Overview recent cards are not newest-first and distinct from early cards');
  check(new Set(overviewSlugs).size === overviewSlugs.length, 'Overview repeats paper identities between early and recent work');
  check(await page.locator('.topic-paper-card-footer').count() === await overviewCards.count(), 'Overview cards are missing their source/AI-summary footers');
  const overviewContextHref = await overviewCards.first().locator('h3 a').getAttribute('href');
  check(Boolean(overviewContextHref && /[?&]from=%23%2Ftopic%2F/i.test(overviewContextHref)), 'Overview paper card does not preserve its topic context link');
  await screenshot('overview');
  await tabs.getByText(`All papers (${fixture.papers.length})`, { exact: true }).click(); await ready('papers');
  check(await rows.count() === Math.min(25, fixture.papers.length), 'all-papers first page does not show 25 (or fewer final) rows');
  check(await rows.count() <= 25, 'all-papers page exceeds 25 rows');
  check((await rowSlugs()).join('|') === expected().slice(0, 25).map((paper) => paper.slug).join('|'), 'All-papers default rows changed from the catalog ordering');
  const lightControlStyle = await paperControlStyle();
  check(lightControlStyle.heights.length === 5 && lightControlStyle.heights.every((height) => height === 56), `All-papers controls must share a 56px height (${lightControlStyle.heights.join(', ')})`);
  check(lightControlStyle.appearances.length === 3 && lightControlStyle.appearances.every(({ standard, webkit }) => standard === 'none' && webkit === 'none'), `native All-papers selects must disable browser appearance (${JSON.stringify(lightControlStyle.appearances)})`);
  check(lightControlStyle.chevrons.length === 3 && lightControlStyle.chevrons.every((content) => content && content !== 'none'), 'All-papers select wrappers have no visible custom chevron');
  await page.goto(`${base}/latent-scape/#/map`, { waitUntil: 'networkidle' });
  const mapTrigger = page.locator('#map-topic'); await mapTrigger.waitFor();
  const mapTriggerHeight = Math.round(await mapTrigger.evaluate((trigger) => trigger.getBoundingClientRect().height));
  check(mapTriggerHeight === 56, `map topic picker must be 56px high (${mapTriggerHeight})`);
  check(lightControlStyle.heights.every((height) => height === mapTriggerHeight), `All-papers controls do not match map picker height (${lightControlStyle.heights.join(', ')} vs ${mapTriggerHeight})`);
  await page.goto(hash(fixture.topic.slug, new URLSearchParams({ tab: 'papers' })), { waitUntil: 'networkidle' }); await ready('papers');
  await viewportScreenshot('list');
  const pagination = page.locator('.topic-paper-pages');
  const allSeen = new Set();
  for (let index = 1; index <= Math.ceil(fixture.papers.length / 25); index += 1) {
    await page.waitForTimeout(30); const visible = await currentRows(); visible.forEach((item) => { const match = new URL(item.href, base).hash.match(/^#\/paper\/([^?]+)/); if (match) allSeen.add(decodeURIComponent(match[1])); });
    if (index < Math.ceil(fixture.papers.length / 25)) { await page.locator('[data-topic-paper-page]:has-text("Next"), .topic-paper-pages button:has-text("Next")').click(); await page.waitForFunction((pageNumber) => location.hash.includes(`page=${pageNumber}`), index + 1); }
  }
  check(allSeen.size === fixture.papers.length, `pagination exposed ${allSeen.size}/${fixture.papers.length} unique paper links`);
  check(new Set(fixture.papers.map((paper) => paper.slug)).size === fixture.papers.length, 'catalog fixture itself has duplicate paper slugs');
  await page.goto(hash(fixture.topic.slug, new URLSearchParams({ tab: 'papers', page: '99999' })), { waitUntil: 'networkidle' }); await ready('papers');
  check((await pagination.textContent() || '').includes(`Page ${Math.ceil(fixture.papers.length / 25)} of`), 'out-of-range page was not clamped to final page');
  for (const malformed of ['1.5', 'Infinity']) { await page.goto(hash(fixture.topic.slug, new URLSearchParams({ tab: 'papers', page: malformed })), { waitUntil: 'networkidle' }); await ready('papers'); check(/Page 1 of/.test(await pagination.textContent() || ''), `malformed page=${malformed} was not normalized to page 1`); }

  const selectedYear = String(fixture.papers[0].year); await year.selectOption(selectedYear); await page.waitForTimeout(80);
  check((await currentRows()).every((item) => item.text.includes(selectedYear)), 'year filter rows disagree with catalog');
  await venue.selectOption(fixture.papers[0].venue); await page.waitForTimeout(80);
  const expectedFiltered = expected('', selectedYear, fixture.papers[0].venue); check((await rows.count()) === Math.min(25, expectedFiltered.length), 'year/venue filter count disagrees with catalog');
  await sort.selectOption('oldest'); await page.waitForTimeout(80); check((await rowSlugs()).join('|') === expected('', selectedYear, fixture.papers[0].venue, 'oldest').slice(0, 25).map((paper) => paper.slug).join('|'), 'oldest sort disagrees with catalog');
  await sort.selectOption('title'); await page.waitForTimeout(80); check((await rowSlugs()).join('|') === expected('', selectedYear, fixture.papers[0].venue, 'title').slice(0, 25).map((paper) => paper.slug).join('|'), 'title sort disagrees with catalog');
  if (authorProbe) { const authorQuery = plainText(authorProbe.authors[0]); await reset.click(); await search.focus(); await page.keyboard.type(authorQuery, { delay: 12 }); await page.waitForTimeout(80); check(await search.inputValue() === authorQuery, 'incremental search lost or changed the typed query'); check(await page.evaluate(() => document.activeElement?.matches('[data-topic-paper-search], .topic-paper-controls input')), 'incremental search rerendered and lost input focus'); check((await rowSlugs()).includes(authorProbe.slug), 'author-only query did not find its catalog paper'); }
  if (tldrProbe) { await search.fill(readerText.get(tldrProbe.slug).tldr.split(/\s+/).slice(0, 2).join(' ')); await page.waitForTimeout(80); check((await rowSlugs()).includes(tldrProbe.slug), 'TL;DR-only query did not find its catalog paper'); }
  await search.fill('__no_topic_paper_match__'); await page.waitForTimeout(80); check((await list.innerText()).includes('No papers match'), 'zero-result state is absent'); await reset.click(); await page.waitForTimeout(80); check(await search.inputValue() === '', 'Reset did not clear the search query'); check(await year.inputValue() === '', 'Reset did not clear the year filter'); check(await venue.inputValue() === '', 'Reset did not clear the venue filter'); check(await sort.inputValue() === 'newest', 'Reset did not restore newest sort'); check((await rows.count()) === Math.min(25, fixture.papers.length), 'Reset did not restore first-page rows');
  const inspectPaperRow = async (paper, assertion, message) => { if (!paper) return; await search.fill(readerText.get(paper.slug).title); await page.waitForTimeout(80); const row = paperRow(paper.slug); check(await row.count() === 1 && await assertion(row), message); await reset.click(); await page.waitForTimeout(80); };
  await inspectPaperRow(missingTldr, async (row) => /(?:no|without|unavailable).{0,45}(?:AI.generated )?summary|(?:AI.generated )?summary.{0,45}(?:no|unavailable)/i.test(await row.innerText()), 'paper without TL;DR lacks an honest unavailable-summary message');
  await inspectPaperRow(sourceByLabel, async (row) => (await row.locator('.topic-paper-source').innerText()).includes(sourceByLabel.sourceLinkLabel), 'Publication record label is not rendered accurately');
  await inspectPaperRow(originalByLabel, async (row) => (await row.locator('.topic-paper-source').innerText()).includes(originalByLabel.sourceLinkLabel), 'Original paper label is not rendered accurately');
  await screenshot('filtered');

  await reset.click(); await waitForExactRows(Math.min(25, fixture.papers.length), 'after-reset-before-navigation'); await page.locator('[data-topic-paper-page]:has-text("Next"), .topic-paper-pages button:has-text("Next")').click(); await page.waitForFunction(() => location.hash.includes('page=2')); await waitForExactRows(Math.min(25, fixture.papers.length - 25), 'after-next-before-navigation'); await navigationDiagnostic('before-first-paper-link'); const preservedList = await page.evaluate(() => location.hash); await page.evaluate(() => scrollTo(0, Math.min(450, document.documentElement.scrollHeight - innerHeight))); const requestedScroll = await page.evaluate(() => scrollY); check(requestedScroll > 100, 'topic paper listing is too short for scroll restoration regression test');
  const firstLink = rows.first().locator('h3 a'); const firstHref = await firstLink.getAttribute('href'); const selectedSlug = slugFromHref(firstHref); await firstLink.focus(); const savedScroll = await page.evaluate(() => scrollY); check(savedScroll > 100, 'focusing the selected paper moved the listing outside the scroll restoration test range'); await page.keyboard.press('Enter'); await page.waitForFunction((href) => location.hash === href, firstHref); await waitForPaper(selectedSlug); check((await page.evaluate(() => scrollY)) <= 1, 'new paper navigation did not start at page top');
  await page.goBack({ waitUntil: 'networkidle' }); await ready('papers', ''); await rows.first().waitFor(); check(await page.evaluate((expected) => location.hash === expected, preservedList), 'browser Back did not preserve listing query, filters, and page'); check(await search.inputValue() === '' && await year.inputValue() === '' && await venue.inputValue() === '' && await sort.inputValue() === 'newest', 'browser Back did not restore the listing control values'); await waitForScroll(savedScroll); const browserBackScroll = await page.evaluate(() => scrollY); check(Math.abs(browserBackScroll - savedScroll) < 80, `browser Back did not restore listing scroll (${browserBackScroll}; expected actual click scroll ${savedScroll})`);
  const returnLink = rows.first().locator('h3 a'); const returnSlug = slugFromHref(await returnLink.getAttribute('href')); await returnLink.focus(); const returnClickScroll = await page.evaluate(() => scrollY); await page.keyboard.press('Enter'); await waitForPaper(returnSlug); const paperBack = page.locator('[data-topic-paper-return], .paper-detail .crumb').first(); check(await paperBack.count() === 1, 'paper detail has no visible Back to listing crumb'); if (await paperBack.count()) { await paperBack.focus(); await page.keyboard.press('Enter'); await page.waitForFunction((expected) => location.hash === expected, preservedList).catch(() => {}); await ready('papers', ''); await rows.first().waitFor(); check(await page.evaluate((expected) => location.hash === expected, preservedList), 'paper listing crumb did not preserve query, filters, and page'); check(await search.inputValue() === '' && await year.inputValue() === '' && await venue.inputValue() === '' && await sort.inputValue() === 'newest', 'paper listing crumb did not restore listing control values'); await waitForScroll(returnClickScroll); const restored = await page.evaluate(() => scrollY); check(Math.abs(restored - returnClickScroll) < 80, `paper listing crumb did not restore scroll (${restored}; expected actual click scroll ${returnClickScroll})`); } else { await page.goBack({ waitUntil: 'networkidle' }); await ready('papers', ''); await rows.first().waitFor(); }
  const crumb = page.locator('[data-topic-paper-back], .topic-reader .crumb').first(); await crumb.focus(); await page.keyboard.press('Enter'); await page.waitForFunction(() => location.hash.startsWith('#/topics')); check(true, 'visible Back to listing crumb is keyboard operable');

  const encoded = new URLSearchParams({ tab: 'papers', q: specialQuery }); await page.goto(hash(fixture.topic.slug, encoded), { waitUntil: 'networkidle' }); await ready('papers'); check(await search.inputValue() === specialQuery, 'query punctuation or Unicode was corrupted in a deep link');
  for (const [width, height, name] of [[1440, 920, 'desktop'], [900, 860, 'tablet'], [390, 844, 'mobile'], [320, 760, 'narrow']]) { await page.setViewportSize({ width, height }); await page.goto(hash(fixture.topic.slug, new URLSearchParams({ tab: 'papers' })), { waitUntil: 'networkidle' }); await ready('papers'); await noOverflow(name); const clipped = await rows.evaluateAll((items) => items.some((item) => { const title = item.querySelector('h3'); return Boolean(title && (title.scrollWidth > title.clientWidth + 1 || getComputedStyle(title).textOverflow === 'ellipsis')); })); check(!clipped, `${name} paper title is clipped`); if (name === 'mobile' || name === 'narrow') { const mobileControls = await paperControlStyle(); check(mobileControls.heights.every((controlHeight) => controlHeight === 56), `${name} All-papers controls lost their 56px target`); check(mobileControls.appearances.every(({ standard, webkit }) => standard === 'none' && webkit === 'none'), `${name} native select appearance reappeared`); check(mobileControls.chevrons.every((content) => content && content !== 'none'), `${name} select chevron is absent`); } if (name === 'mobile') { await viewportScreenshot('mobile'); await screenshot('mobile'); } }
  await page.setViewportSize({ width: 1440, height: 920 }); const theme = page.locator('#theme-wide:visible, #theme:visible').first(); if (await theme.count()) { await theme.click(); check(await page.locator('html').getAttribute('data-theme') === 'dark', 'dark theme did not activate'); const darkControls = await paperControlStyle(); check(darkControls.heights.every((controlHeight) => controlHeight === 56), 'dark All-papers controls lost their 56px target'); check(darkControls.appearances.every(({ standard, webkit }) => standard === 'none' && webkit === 'none'), 'dark native select appearance reappeared'); check(darkControls.chevrons.every((content) => content && content !== 'none'), 'dark select chevron is absent'); await screenshot('dark'); }
  await page.goto(hash(fixture.topic.slug, new URLSearchParams({ tab: 'papers' })), { waitUntil: 'networkidle' }); await ready('papers'); await page.evaluate(() => navigator.serviceWorker?.ready); await page.reload({ waitUntil: 'networkidle' }); await ready('papers'); await context.setOffline(true); await page.reload({ waitUntil: 'domcontentloaded' }); await ready('papers').catch((error) => problems.push(`fresh service-worker controlled offline reload failed: ${error.message}`)); await context.setOffline(false);
  if (external.length) problems.push(`off-origin browser requests: ${[...new Set(external)].join(', ')}`);
} finally { await context?.close(); await browser.close(); await new Promise((done) => server.close(done)); }
if (problems.length) { console.error('Topic papers smoke failed:\n' + problems.map((item) => `- ${item}`).join('\n')); process.exitCode = 1; } else console.log(JSON.stringify({ base, topic: fixture.topic.slug, papers: fixture.papers.length, status: 'ok' }));
