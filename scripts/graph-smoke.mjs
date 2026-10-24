#!/usr/bin/env node
/**
 * Production smoke test for the focused semantic topic graph.
 *
 * Usage: node scripts/graph-smoke.mjs site/dist
 * Screenshots are written to /tmp/latentscape-graph-*.png.
 */
import { createRequire } from 'node:module';
import { createServer } from 'node:http';
import { access, readFile, stat } from 'node:fs/promises';
import { constants } from 'node:fs';
import { extname, join, normalize, resolve } from 'node:path';

const require = createRequire(import.meta.url);
const moduleRoot = process.env.LATENTSCAPE_PLAYWRIGHT_ROOT || '/Users/anjantri/Desktop/dust/node_modules';
let chromium; let webkit;
try { ({ chromium, webkit } = require(join(moduleRoot, 'playwright'))); } catch (error) {
  console.error(`Playwright was not found at ${moduleRoot}. Set LATENTSCAPE_PLAYWRIGHT_ROOT.`);
  throw error;
}

let webkitAvailable = false;
try { await access(webkit.executablePath(), constants.R_OK); webkitAvailable = true; } catch { /* optional browser is not installed */ }
const useWebKit = process.argv.includes('--webkit');
if (useWebKit && !webkitAvailable) throw new Error(`Playwright WebKit is not installed at ${webkit?.executablePath?.() || 'an unknown path'}. Run this smoke without --webkit, or install the browser separately.`);
const artifactArg = process.argv.slice(2).find((value) => value !== '--webkit');
const artifact = resolve(artifactArg || 'site/dist');
const screenshotSuffix = useWebKit ? '-webkit' : '';
const graphData = JSON.parse(await readFile(join(artifact, 'data', 'topic-graph.json'), 'utf8'));
if (graphData.layoutKind !== 'focused-semantic-neighborhoods') throw new Error('topic graph has an unexpected layout kind');
if (graphData.topics?.length !== 256 || graphData.neighborhoods?.length !== 256) throw new Error('topic graph must contain 256 topics and neighborhoods');
for (const neighborhood of graphData.neighborhoods) {
  if (neighborhood.nodes.some((node) => !Number.isFinite(node.x) || !Number.isFinite(node.y)) || neighborhood.edges.some((edge) => !Number.isInteger(edge.source) || !Number.isInteger(edge.target))) {
    throw new Error(`topic graph has invalid coordinates or edges for root ${neighborhood.rootTopic}`);
  }
}
const defaultIndex = graphData.topics.findIndex((topic) => topic.slug === graphData.defaultTopicSlug);
if (defaultIndex < 0) throw new Error('default topic is absent from topic graph');
const defaultNeighborhood = graphData.neighborhoods.find((item) => item.rootTopic === defaultIndex);
if (!defaultNeighborhood?.nodes?.some((node) => node.degree === 1) || !defaultNeighborhood.nodes.some((node) => node.degree === 2)) {
  throw new Error('default graph must include both first- and second-degree neighbors');
}

const mime = { '.css': 'text/css', '.html': 'text/html', '.js': 'text/javascript', '.json': 'application/json', '.mjs': 'text/javascript', '.svg': 'image/svg+xml' };
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
  } catch { response.writeHead(404); response.end('not found'); }
});
await new Promise((done) => server.listen(0, '127.0.0.1', done));
const base = `http://127.0.0.1:${server.address().port}`;
const failures = [];
const coldPaths = [];
const external = [];
const coldBodies = [];
let captureCold = true;
const forbidden = /\/data\/(?:topics|search|vectors)\.json$|\/data\/papers\/|\/(?:models|model)\/|\.(?:onnx|wasm)$/;
const assert = (condition, message) => { if (!condition) failures.push(message); };
const mapUrl = (slug = graphData.defaultTopicSlug, depth = 2) => `${base}/latent-scape/#/map?topic=${encodeURIComponent(slug)}&depth=${depth}`;

const browser = await (useWebKit ? webkit : chromium).launch({ headless: true });
let context;
try {
  context = await browser.newContext({ viewport: { width: 1440, height: 920 }, serviceWorkers: 'allow' });
  const page = await context.newPage();
  page.on('pageerror', (error) => failures.push(`page error: ${error.message}`));
  page.on('console', (message) => { if (message.type() === 'error') failures.push(`console: ${message.text()}`); });
  context.on('request', (request) => {
    const url = new URL(request.url());
    if (url.origin === base) {
      if (captureCold) coldPaths.push(url.pathname);
    } else if (!url.protocol.startsWith('data')) external.push(request.url());
  });
  page.on('response', (response) => {
    const url = new URL(response.url());
    if (captureCold && url.origin === base) coldBodies.push(response.body().then((body) => body.byteLength).catch(() => 0));
  });
  const graph = page.locator('#topic-graph[data-topic-graph-ready="true"]');
  const picker = page.locator('#map-topic');
  const depth = page.locator('#map-two-hop');
  const controls = page.locator('.topic-map-controls');
  const switchTrack = page.locator('.topic-map-switch');
  const graphFrame = page.locator('.topic-graph-scroller');
  const root = page.locator('.topic-graph-card.is-root');
  const ready = async (slug = graphData.defaultTopicSlug) => {
    await graph.waitFor();
    await root.locator('[data-topic-slug], h2').first().waitFor();
    await page.waitForFunction((expected) => document.querySelector('.topic-graph-card.is-root')?.dataset.topicSlug === expected || document.querySelector('.topic-graph-card.is-root h2')?.textContent?.trim() === expected, graphData.topics.find((topic) => topic.slug === slug)?.title || slug);
  };
  const screenshot = async (name, target = graph) => {
    await page.waitForTimeout(500);
    await target.screenshot({ path: `/tmp/latentscape-graph-${name}${screenshotSuffix}.png` });
    // Locator screenshots scroll their target into view. Reset before the
    // full-page capture so fixed navigation is never recorded mid-document.
    await page.evaluate(() => window.scrollTo(0, 0));
    await page.waitForTimeout(50);
    await page.screenshot({ path: `/tmp/latentscape-graph-${name}${screenshotSuffix}-page.png`, fullPage: true });
  };
  const screenshotControls = async (name) => {
    await page.waitForTimeout(500);
    await controls.screenshot({ path: `/tmp/latentscape-graph-controls-${name}${screenshotSuffix}.png` });
    await page.evaluate(() => window.scrollTo(0, 0));
    await page.waitForTimeout(50);
    await page.screenshot({ path: `/tmp/latentscape-graph-controls-${name}${screenshotSuffix}-page.png`, fullPage: true });
  };
  const controlStyle = async () => page.evaluate(() => {
    const select = document.querySelector('#map-topic');
    const checkbox = document.querySelector('#map-two-hop');
    const track = document.querySelector('.topic-map-switch');
    if (!(select instanceof HTMLSelectElement) || !(checkbox instanceof HTMLInputElement) || !(track instanceof HTMLElement)) return null;
    const selectStyle = getComputedStyle(select); const trackStyle = getComputedStyle(track); const knob = getComputedStyle(track, '::after');
    return {
      appearance: selectStyle.appearance,
      selectHeight: select.getBoundingClientRect().height,
      switchWidth: track.getBoundingClientRect().width,
      switchHeight: track.getBoundingClientRect().height,
      switchDisplay: trackStyle.display,
      checked: checkbox.checked,
      visual: `${trackStyle.backgroundColor}|${trackStyle.borderColor}|${knob.backgroundColor}|${knob.transform}`,
    };
  });
  const assertCardTextFits = async (name) => {
    const overflowing = await page.locator('.topic-graph-card').evaluateAll((cards) => cards.some((card) => {
      const title = card.querySelector('h2');
      if (!title) return true;
      const box = card.getBoundingClientRect(); const titleBox = title.getBoundingClientRect();
      return card.scrollHeight > card.clientHeight + 1 || card.scrollWidth > card.clientWidth + 1
        || titleBox.left < box.left - 1 || titleBox.right > box.right + 1
        || titleBox.top < box.top - 1 || titleBox.bottom > box.bottom + 1;
    }));
    assert(!overflowing, `${name} graph card text is clipped or overflows its card`);
  };

  const started = performance.now();
  await page.goto(mapUrl(), { waitUntil: 'domcontentloaded' });
  await ready();
  const readyMs = Math.round(performance.now() - started);
  captureCold = false;
  const readyBytes = (await Promise.all(coldBodies)).reduce((total, bytes) => total + bytes, 0);
  const optionCount = await picker.locator('option').count();
  assert(optionCount === 256, `topic picker has ${optionCount} options, expected 256`);
  assert(await page.locator('.topic-graph-card[data-degree="0"]').count() === 1, 'default graph has no single root card');
  assert(await page.locator('.topic-graph-card[data-degree="1"]').count() > 0, 'default graph has no first-degree labels');
  assert(await page.locator('.topic-graph-card[data-degree="2"]').count() > 0, 'default graph has no second-degree labels');
  assert(await page.locator('.topic-graph-edge[data-degree="1"], .topic-graph-edge.degree-1').count() > 0, 'default graph has no first-degree edges');
  assert(await page.locator('.topic-graph-edge[data-degree="2"], .topic-graph-edge.degree-2').count() > 0, 'default graph has no second-degree edges');
  const lightOnStyle = await controlStyle();
  assert(lightOnStyle?.appearance === 'none', `topic picker native appearance was not removed (${lightOnStyle?.appearance})`);
  assert((lightOnStyle?.selectHeight || 0) >= 44, `topic picker is shorter than 44px (${lightOnStyle?.selectHeight})`);
  assert((lightOnStyle?.switchWidth || 0) >= 32 && (lightOnStyle?.switchHeight || 0) >= 20 && lightOnStyle?.switchDisplay !== 'none', 'second-degree switch is not visibly styled');
  await assertCardTextFits('default');
  await screenshot('overview');

  await depth.focus(); await page.keyboard.press('Space');
  await page.waitForFunction(() => !location.hash.includes('depth=2'));
  await page.waitForTimeout(150);
  assert(!(await depth.isChecked()), 'Space did not turn off second-degree neighbors');
  const lightOffStyle = await controlStyle();
  assert(lightOffStyle?.visual !== lightOnStyle?.visual, 'second-degree switch did not visibly change when unchecked');
  assert(await page.locator('.topic-graph-card[data-degree="2"]').count() === 0, 'depth=1 still shows second-degree cards');
  const depthOneHasHiddenEndpoint = await page.locator('.topic-graph-edge').evaluateAll((edges) => {
    const visible = new Set([...document.querySelectorAll('.topic-graph-card[data-topic-index]')].map((card) => card.getAttribute('data-topic-index')));
    return edges.some((edge) => !visible.has(edge.getAttribute('data-edge-source')) || !visible.has(edge.getAttribute('data-edge-target')));
  });
  assert(!depthOneHasHiddenEndpoint, 'depth=1 shows an edge whose endpoint is not displayed');
  await screenshotControls('light-off');
  await screenshot('first-degree');
  await depth.focus(); await page.keyboard.press('Space');
  await page.waitForFunction(() => location.hash.includes('depth=2'));
  assert(await depth.isChecked(), 'Space did not restore second-degree neighbors');
  await page.locator('.topic-graph-card[data-degree="2"]').first().waitFor();
  await screenshot('second-degree');

  const exploreAndBack = async (degree) => {
    const card = page.locator(`.topic-graph-card[data-degree="${degree}"]`).first();
    const expectedSlug = await card.getAttribute('data-topic-slug');
    const expectedTitle = await card.locator('h2').textContent();
    const control = card;
    await control.focus(); await page.keyboard.press('Enter');
    await page.waitForFunction((slug) => location.hash.includes(`topic=${encodeURIComponent(slug)}`), expectedSlug);
    await ready(expectedSlug);
    assert((await root.locator('h2').textContent())?.trim() === expectedTitle?.trim(), `degree ${degree} focus did not set the expected root heading`);
    await page.goBack({ waitUntil: 'networkidle' }); await ready();
  };
  await exploreAndBack(1);
  await exploreAndBack(2);

  // Long titles are a realistic stress case for the fixed card geometry. Select
  // the three longest from the accessible picker, then check the root and all
  // displayed neighbors for clipping instead of relying on a short fixture.
  const longestTopics = [...graphData.topics].sort((left, right) => right.title.length - left.title.length).slice(0, 3);
  for (const topic of longestTopics) {
    await picker.selectOption(topic.slug);
    await page.waitForFunction((slug) => location.hash.includes(`topic=${encodeURIComponent(slug)}`), topic.slug);
    await ready(topic.slug);
    assert((await root.locator('h2').textContent())?.trim() === topic.title, `long-title topic did not become the root: ${topic.slug}`);
    await assertCardTextFits(`long-title ${topic.slug}`);
  }
  await page.goto(mapUrl(), { waitUntil: 'networkidle' }); await ready();

  const read = root.locator('[data-graph-read-topic]');
  const rootSlug = await root.getAttribute('data-topic-slug');
  const rootTitle = await root.locator('h2').textContent();
  await read.focus(); await page.keyboard.press('Enter');
  await page.waitForFunction((slug) => location.hash.startsWith(`#/topic/${encodeURIComponent(slug)}`), rootSlug);
  await page.waitForFunction((title) => [...document.querySelectorAll('h1')].some((heading) => heading.textContent?.trim() === title), rootTitle?.trim());
  assert((await page.locator('h1').first().textContent())?.trim() === rootTitle?.trim(), 'Read topic did not open the selected topic');
  await page.goBack({ waitUntil: 'networkidle' }); await ready(rootSlug);

  for (const [width, height, name] of [[2048, 1000, 'wide'], [1440, 920, 'desktop'], [900, 860, 'tablet'], [390, 844, 'mobile'], [320, 760, 'narrow']]) {
    await page.setViewportSize({ width, height });
    await page.goto(mapUrl(), { waitUntil: 'networkidle' }); await ready();
    const dimensions = await page.evaluate(() => ({ documentWidth: document.documentElement.scrollWidth, viewport: innerWidth }));
    assert(dimensions.documentWidth <= dimensions.viewport + 1, `${name} page overflows (${dimensions.documentWidth} > ${dimensions.viewport})`);
    if (name === 'wide' || name === 'desktop') {
      const frame = await page.evaluate(() => {
        const outer = document.querySelector('.topic-graph-scroller');
        const inner = document.querySelector('#topic-graph')?.getBoundingClientRect();
        return outer && inner ? { outer: outer.clientWidth, inner: inner.width } : null;
      });
      assert(Boolean(frame) && frame.outer <= frame.inner + 1, `${name} graph frame has unused right width (${frame?.outer} > ${frame?.inner})`);
    }
    const clipped = await page.locator('.topic-graph-card h2').evaluateAll((titles) => titles.some((title) => {
      const style = getComputedStyle(title); return title.scrollWidth > title.clientWidth + 1 || style.textOverflow === 'ellipsis' || style.overflow === 'hidden';
    }));
    assert(!clipped, `${name} graph card title is truncated or clipped`);
    if (name === 'mobile') { await screenshotControls('mobile'); await screenshot('mobile'); }
  }

  await page.setViewportSize({ width: 1440, height: 920 });
  const theme = page.locator('#theme-wide:visible, #theme:visible').first();
  if (await theme.count()) {
    await theme.click();
    const darkOnStyle = await controlStyle();
    assert(darkOnStyle?.appearance === 'none' && (darkOnStyle?.selectHeight || 0) >= 44, 'dark theme picker lost its styled touch target');
    await depth.focus(); await page.keyboard.press('Space');
    await page.waitForFunction(() => !location.hash.includes('depth=2') && document.querySelectorAll('.topic-graph-card[data-degree="2"]').length === 0);
    await page.waitForTimeout(250);
    const darkOffStyle = await controlStyle();
    assert(darkOffStyle?.visual !== darkOnStyle?.visual, 'dark theme second-degree switch did not visibly change when unchecked');
    await screenshotControls('dark-off');
    await screenshot('dark');
  } else failures.push('theme control is unavailable');

  await page.emulateMedia({ reducedMotion: 'reduce' });
  await page.goto(mapUrl(), { waitUntil: 'networkidle' }); await ready();
  assert(await page.evaluate(() => matchMedia('(prefers-reduced-motion: reduce)').matches), 'reduced-motion media preference was not applied');

  // Ensure the installed service worker controls a warm client, then prove a
  // fresh controlled page can render the already-cached map without a network.
  await page.evaluate(() => navigator.serviceWorker?.ready);
  await page.reload({ waitUntil: 'networkidle' }); await ready();
  const workerControlled = await page.waitForFunction(() => Boolean(navigator.serviceWorker?.controller), undefined, { timeout: 10_000 }).then(() => true).catch(() => false);
  assert(workerControlled, 'service worker did not control the warm map page');
  if (workerControlled) {
    const offline = await context.newPage();
    offline.on('pageerror', (error) => failures.push(`offline page error: ${error.message}`));
    await context.setOffline(true);
    await offline.goto(mapUrl(), { waitUntil: 'domcontentloaded' }).catch((error) => failures.push(`offline map reload failed: ${error.message}`));
    const offlineGraph = offline.locator('#topic-graph[data-topic-graph-ready="true"]');
    await offlineGraph.waitFor({ timeout: 10_000 }).catch(() => failures.push('service-worker-controlled offline map did not render'));
    await context.setOffline(false);
    await offline.close();
  }

  assert(!coldPaths.some((pathname) => forbidden.test(pathname)), `cold graph loaded forbidden data: ${coldPaths.filter((pathname) => forbidden.test(pathname)).join(', ')}`);
  assert(external.length === 0, `off-origin browser requests: ${[...new Set(external)].join(', ')}`);
  const resources = await readFile(join(artifact, 'data', 'topic-graph.json'));
  console.log(JSON.stringify({ status: failures.length ? 'failed' : 'ok', browser: useWebKit ? 'webkit' : 'chromium', webkitAvailable, readyMs, readyBytes, graphBytes: resources.byteLength, coldPaths: [...new Set(coldPaths)].length }));
} finally {
  if (context) await context.close();
  await browser.close();
  await new Promise((done) => server.close(done));
}

if (failures.length) { console.error('Graph smoke failed:\n' + failures.map((failure) => `- ${failure}`).join('\n')); process.exitCode = 1; }
