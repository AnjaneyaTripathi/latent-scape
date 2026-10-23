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
page.on('pageerror', (error) => failures.push(`page error: ${error.message}`));
page.on('console', (message) => { if (message.type() === 'error') failures.push(`console: ${message.text()}`); });
context.on('page', (child) => child.on('pageerror', (error) => failures.push(`child page error: ${error.message}`)));
context.on('request', (request) => {
  const url = new URL(request.url());
  if (url.origin !== base && !url.protocol.startsWith('data')) external.push(request.url());
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
  await page.screenshot({ path: '/tmp/latentscape-home-light.png' });

  await search('learning from rewards');
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

  await page.goto(`${base}/latent-scape/#/topics`, { waitUntil: 'networkidle' });
  const firstTopic = page.locator('.topic-card a').first();
  if (await firstTopic.count()) { await firstTopic.click(); await page.waitForTimeout(350); await page.screenshot({ path: '/tmp/latentscape-topic.png', fullPage: true }); }

  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto(`${base}/latent-scape/`, { waitUntil: 'networkidle' });
  const widths = await page.evaluate(() => ({ scroll: document.documentElement.scrollWidth, viewport: window.innerWidth }));
  await assert(widths.scroll <= widths.viewport + 1, `mobile page overflows horizontally (${widths.scroll} > ${widths.viewport})`);
  await page.screenshot({ path: '/tmp/latentscape-mobile.png', fullPage: true });

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
