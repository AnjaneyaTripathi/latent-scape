#!/usr/bin/env node
/**
 * Smoke-test a production artifact with Playwright. It has no project dependency:
 * set LATENTSCAPE_PLAYWRIGHT_ROOT to a directory containing node_modules, or use
 * the workspace's shared Playwright installation.
 */
import { createRequire } from 'node:module';
import { createServer } from 'node:http';
import { readFile, stat } from 'node:fs/promises';
import { extname, join, normalize, resolve } from 'node:path';

const require = createRequire(import.meta.url);
const moduleRoot = process.env.LATENTSCAPE_PLAYWRIGHT_ROOT || '/Users/anjantri/Desktop/dust/node_modules';
let chromium;
try {
  ({ chromium } = require(join(moduleRoot, 'playwright')));
} catch (error) {
  console.error(`Playwright was not found at ${moduleRoot}. Set LATENTSCAPE_PLAYWRIGHT_ROOT.`);
  throw error;
}

const artifact = resolve(process.argv[2] || 'site/dist');
const dataManifest = JSON.parse(await readFile(join(artifact, 'data', 'manifest.json'), 'utf8'));
const dataTopics = JSON.parse(await readFile(join(artifact, 'data', 'topics.json'), 'utf8'));
let authoredPaper;
for (const filename of Object.values(dataManifest.shards?.papers || {})) {
  const shard = JSON.parse(await readFile(join(artifact, 'data', filename), 'utf8'));
  authoredPaper = shard.find((paper) => paper.summary?.paragraphs?.length || paper.summary?.problem);
  if (authoredPaper) break;
}
if (!authoredPaper) throw new Error('Smoke test needs one authored paper summary');
const mime = { '.css': 'text/css', '.html': 'text/html', '.js': 'text/javascript', '.mjs': 'text/javascript', '.json': 'application/json', '.onnx': 'application/octet-stream', '.wasm': 'application/wasm', '.txt': 'text/plain' };
const server = createServer(async (request, response) => {
  let pathname = decodeURIComponent(new URL(request.url, 'http://local').pathname);
  if (pathname === '/latent-scape' || pathname === '/latent-scape/') pathname = '/';
  else if (pathname.startsWith('/latent-scape/')) pathname = pathname.slice('/latent-scape'.length);
  const candidate = normalize(join(artifact, pathname === '/' ? 'index.html' : pathname));
  const file = candidate.startsWith(artifact) ? candidate : join(artifact, 'index.html');
  try {
    const target = (await stat(file)).isFile() ? file : join(artifact, 'index.html');
    response.writeHead(200, { 'content-type': mime[extname(target)] || 'application/octet-stream', 'cache-control': 'no-store' });
    response.end(await readFile(target));
  } catch {
    response.writeHead(404); response.end('not found');
  }
});
await new Promise((resolveServer) => server.listen(0, '127.0.0.1', resolveServer));
const base = `http://127.0.0.1:${server.address().port}`;

const browser = await chromium.launch({ headless: true });
const context = await browser.newContext({ viewport: { width: 1440, height: 920 } });
const page = await context.newPage();
const failures = [];
const external = [];
const localRequests = [];
page.on('pageerror', (error) => failures.push(`page error: ${error.message}`));
page.on('console', (message) => { if (message.type() === 'error') failures.push(`console: ${message.text()}`); });
context.on('page', (child) => child.on('pageerror', (error) => failures.push(`child page error: ${error.message}`)));
context.on('request', (request) => {
  const url = new URL(request.url());
  if (url.origin !== base && !url.protocol.startsWith('data')) external.push(request.url());
  if (url.origin === base) localRequests.push(url.pathname);
});

async function assert(condition, message) { if (!condition) failures.push(message); }
async function search(query) {
  const input = page.locator('input').first();
  await input.fill(query);
  await input.press('Enter');
  await page.waitForTimeout(500);
  await page.waitForLoadState('networkidle').catch(() => {});
}

try {
  await page.goto(`${base}/latent-scape/`, { waitUntil: 'networkidle' });
  await assert((await page.locator('body').innerText()).length > 80, 'home page did not render');
  // The landing page is deliberately usable before the 50+ MB search index is
  // fetched. Wait for the background featured cards so their topic labels have
  // also resolved before making the cold-start request assertion.
  await page.locator('#featured-papers .paper-card').first().waitFor();
  await assert(!localRequests.some((pathname) => pathname.endsWith('/data/search.json')), 'cold home loaded search.json before a search was requested');
  const featuredTags = page.locator('#featured-papers .tag');
  await assert(await featuredTags.count() > 0, 'featured cards did not render topic labels');
  await assert((await featuredTags.allTextContents()).every((label) => !/^topic-\d{3}(?:-|$)/i.test(label.trim())), 'featured cards exposed raw topic IDs instead of curated labels');
  // Ensure the service worker controls this client before testing a full offline reload.
  await page.evaluate(() => navigator.serviceWorker?.ready);
  await page.reload({ waitUntil: 'networkidle' });
  await page.screenshot({ path: '/tmp/latentscape-home-light.png' });

  await search('learning from rewards');
  await assert(await page.getByLabel('Search concepts and papers').count() === 1, 'search input does not have an accessible label');
  const results = page.locator('.results a, .card a, .paper-card a');
  await assert(await results.count() > 0, 'search returned no navigable result');
  const semanticResults = await page.evaluate(async () => {
    const bridge = window.latentscapeSemanticSearch;
    return bridge ? bridge('learning from rewards') : [];
  });
  await assert(semanticResults.length > 0, 'semantic bridge returned no meaningful result');
  await assert(/reinforcement|reward/i.test(await page.locator('#search-results').innerText()), 'reward-learning query did not surface a recognizably relevant result');

  await page.screenshot({ path: '/tmp/latentscape-desktop-search.png', fullPage: true });

  // Warm model/vector/shard fetches, then make another search while offline.
  await context.setOffline(true);
  await search('generate pictures from noise');
  await assert(await results.count() > 0, 'semantic/keyword search did not remain available offline after loading');
  await page.reload({ waitUntil: 'networkidle' });
  const offlineSemantic = await page.evaluate(async () => window.latentscapeSemanticSearch?.('generate pictures from noise') || []);
  await assert(offlineSemantic.length > 0, 'semantic bridge did not survive a fully offline reload');
  const first = results.first();
  if (await first.count()) {
    await first.click();
    await page.waitForTimeout(400);
    await assert(/paper|topic/.test(page.url()), 'search result did not route to a detail page');
    // A result page must lead to its source in one more click when it is a paper.
    if (page.url().includes('/paper/')) {
      await assert(await page.locator('.official').count() > 0, 'paper detail does not expose its original-paper link');
      await page.screenshot({ path: '/tmp/latentscape-paper.png', fullPage: true });
    } else await page.screenshot({ path: '/tmp/latentscape-topic.png', fullPage: true });
  }
  await context.setOffline(false);

  // A paper page must preserve the official abstract verbatim, then give a
  // bounded 3–5 paragraph explanation rather than replacing the source text.
  await page.goto(`${base}/latent-scape/#/paper/${encodeURIComponent(authoredPaper.slug)}`, { waitUntil: 'networkidle' });
  await page.locator('.paper-detail h1').filter({ hasText: authoredPaper.title }).waitFor();
  const abstract = page.locator('.official-abstract > p:not(.eyebrow):not(.source-note)').first();
  const renderedAbstract = await abstract.textContent();
  const expectedAbstract = authoredPaper.originalAbstract ?? authoredPaper.abstract;
  await assert(renderedAbstract === expectedAbstract, `paper page changed the official abstract text (${JSON.stringify(renderedAbstract)} !== ${JSON.stringify(expectedAbstract)})`);
  const explanation = page.locator('.narrative > p:not(.eyebrow):not(.source-note)');
  const paragraphCount = await explanation.count();
  await assert(paragraphCount >= 3 && paragraphCount <= 5, `paper explanation has ${paragraphCount} paragraphs, expected 3–5`);

  await page.goto(`${base}/latent-scape/#/topics`, { waitUntil: 'networkidle' });
  const firstTopic = page.locator('.topic-card a').first();
  if (await firstTopic.count()) { await firstTopic.click(); await page.waitForTimeout(350); await page.screenshot({ path: '/tmp/latentscape-topic.png', fullPage: true }); }

  await page.goto(`${base}/latent-scape/#/map`, { waitUntil: 'networkidle' });
  const topicPicker = page.locator('#map-topic');
  await topicPicker.waitFor();
  const optionCount = await topicPicker.locator('option').count();
  await assert(dataManifest.totalTopics === 256, `map fixture has ${dataManifest.totalTopics} topics, expected 256`);
  await assert(optionCount - 1 === 256, `map topic picker has ${optionCount - 1} topics, expected 256`);
  await topicPicker.selectOption({ index: 1 });
  await page.waitForFunction(() => location.hash.startsWith('#/topic/'));
  await assert(page.url().includes('/topic/'), 'keyboard-accessible map topic picker did not navigate to a topic page');

  await page.setViewportSize({ width: 390, height: 844 });
  const assertMobileWidth = async (name) => {
    const widths = await page.evaluate(() => ({ scroll: document.documentElement.scrollWidth, viewport: window.innerWidth }));
    await assert(widths.scroll <= widths.viewport + 1, `mobile ${name} page overflows horizontally (${widths.scroll} > ${widths.viewport})`);
  };
  await page.goto(`${base}/latent-scape/`, { waitUntil: 'networkidle' });
  await assertMobileWidth('home');
  await page.screenshot({ path: '/tmp/latentscape-mobile.png', fullPage: true });

  await page.goto(`${base}/latent-scape/#/paper/${encodeURIComponent(authoredPaper.slug)}`, { waitUntil: 'networkidle' });
  await page.locator('.paper-detail h1').waitFor();
  await assertMobileWidth('paper');
  const mobileTopic = dataTopics[0];
  if (mobileTopic?.slug) {
    await page.goto(`${base}/latent-scape/#/topic/${encodeURIComponent(mobileTopic.slug)}`, { waitUntil: 'networkidle' });
    await page.locator('.detail h1').waitFor();
    await assertMobileWidth('topic');
  } else failures.push('mobile topic overflow check needs one topic');

  const theme = page.locator('#theme-wide:visible, #theme:visible').first();
  if (await theme.count()) { const before = await page.locator('html').getAttribute('data-theme'); await theme.click(); await assert((await page.locator('html').getAttribute('data-theme')) !== before, 'theme toggle did not change the document theme'); }
  await page.screenshot({ path: '/tmp/latentscape-dark.png', fullPage: true });

  await page.goto(`${base}/latent-scape/#/not-a-real-route`, { waitUntil: 'networkidle' });
  await assert((await page.locator('body').innerText()).length > 20, 'missing route renders a blank page');
} finally {
  await browser.close();
  await new Promise((resolveServer) => server.close(resolveServer));
}

if (external.length) failures.push(`off-origin browser requests: ${[...new Set(external)].join(', ')}`);
if (failures.length) {
  console.error('Browser smoke failed:\n' + failures.map((item) => `- ${item}`).join('\n'));
  process.exitCode = 1;
} else {
  console.log(JSON.stringify({ base, status: 'ok' }));
}
