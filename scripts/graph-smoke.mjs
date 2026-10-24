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
// The static host publishes this artifact below /latent-scape/. The local
// server accepts root aliases for routing convenience, so reject a root-base
// Vite build here instead of accidentally masking a broken Pages deploy.
const artifactIndex = await readFile(join(artifact, 'index.html'), 'utf8');
const deploymentLinks = [...artifactIndex.matchAll(/\b(?:src|href)="([^"]+)"/g)].map((match) => match[1]).filter((value) => /(?:^|\/)(?:assets\/|favicon\.svg(?:$|\?))/.test(value));
if (!deploymentLinks.length || deploymentLinks.some((value) => !value.startsWith('/latent-scape/'))) throw new Error(`artifact is not built for /latent-scape/: ${deploymentLinks.join(', ') || 'no asset links'}`);
const graphData = JSON.parse(await readFile(join(artifact, 'data', 'topic-graph.json'), 'utf8'));
if (graphData.version !== 2 || graphData.layoutKind !== 'focused-semantic-neighborhoods') throw new Error('topic graph has an unexpected version or layout kind');
if (graphData.topics?.length !== 256 || graphData.neighborhoods?.length !== 256) throw new Error('topic graph must contain 256 topics and neighborhoods');
const incoming = graphData.topics.map(() => []);
for (const [source, topic] of graphData.topics.entries()) {
  for (const target of topic.relatedTopics || []) incoming[target]?.push(source);
}
const neighborsOf = (index) => new Set([...(graphData.topics[index]?.relatedTopics || []), ...(incoming[index] || [])]);
const directFor = (index) => neighborsOf(index);
const secondFor = (index) => {
  const direct = directFor(index); const second = new Set();
  for (const neighbor of direct) for (const candidate of neighborsOf(neighbor)) {
    if (candidate !== index && !direct.has(candidate)) second.add(candidate);
  }
  return second;
};
const sameSet = (actual, expected) => actual.size === expected.size && [...actual].every((item) => expected.has(item));
for (const neighborhood of graphData.neighborhoods) {
  if (neighborhood.nodes.some((node) => !Number.isFinite(node.x) || !Number.isFinite(node.y)) || neighborhood.edges.some((edge) => !Number.isInteger(edge.source) || !Number.isInteger(edge.target))) {
    throw new Error(`topic graph has invalid coordinates or edges for root ${neighborhood.rootTopic}`);
  }
  const root = neighborhood.rootTopic;
  const direct = directFor(root); const second = secondFor(root);
  const directNodes = new Set(neighborhood.nodes.filter((node) => node.degree === 1).map((node) => node.topic));
  const secondNodes = new Set(neighborhood.nodes.filter((node) => node.degree === 2).map((node) => node.topic));
  if (!sameSet(directNodes, direct)) throw new Error(`root ${root} does not render the exact outgoing+incoming direct-neighbor union`);
  if (!sameSet(secondNodes, second) || [...secondNodes].some((node) => direct.has(node) || node === root)) throw new Error(`root ${root} does not render the complete known undirected distance-two set`);
  if (neighborhood.directNeighborTotal !== direct.size || neighborhood.secondDegreeTotal !== second.size) throw new Error(`root ${root} neighborhood totals do not match its complete graph distances`);
  const bounds = neighborhood.bounds;
  if (!bounds || bounds.width !== 1040 || !Number.isFinite(bounds.height) || bounds.height < 900) throw new Error(`root ${root} has invalid per-neighborhood bounds`);
  const rectangles = neighborhood.nodes.map((node) => {
    const size = node.degree === 0 ? graphData.cardDimensions.root : graphData.cardDimensions.neighbor;
    return { ...node, right: node.x + size.width, bottom: node.y + size.height };
  });
  if (rectangles.some((box) => box.x < 0 || box.y < 0 || box.right > bounds.width || box.bottom > bounds.height)) throw new Error(`root ${root} has a card outside its own graph bounds`);
  for (let left = 0; left < rectangles.length; left += 1) for (let right = left + 1; right < rectangles.length; right += 1) {
    const a = rectangles[left]; const b = rectangles[right];
    if (a.x < b.right && a.right > b.x && a.y < b.bottom && a.bottom > b.y) throw new Error(`root ${root} has overlapping graph cards (${a.topic}, ${b.topic})`);
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
  const pickerSearch = page.locator('#map-topic-search');
  const pickerList = page.locator('#map-topic-listbox');
  const pickerClear = page.locator('[data-topic-picker-clear]');
  const pickerPopover = page.locator('#map-topic-popover');
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
    // A complete distance-two neighborhood can be very tall. Preserve a useful
    // viewport capture without creating impractically large PNG artifacts.
    const targetHeight = await target.boundingBox().then((box) => box?.height || 0);
    if (targetHeight <= 1800) await target.screenshot({ path: `/tmp/latentscape-graph-${name}${screenshotSuffix}.png` });
    else {
      await page.evaluate(() => window.scrollTo(0, 0));
      await page.screenshot({ path: `/tmp/latentscape-graph-${name}${screenshotSuffix}.png` });
    }
    // Locator screenshots scroll their target into view. Reset before the
    // full-page capture so fixed navigation is never recorded mid-document.
    await page.evaluate(() => window.scrollTo(0, 0));
    await page.waitForTimeout(50);
    const pageHeight = await page.evaluate(() => document.documentElement.scrollHeight);
    await page.screenshot({ path: `/tmp/latentscape-graph-${name}${screenshotSuffix}-page.png`, fullPage: pageHeight <= 3000 });
  };
  const screenshotControls = async (name) => {
    await page.waitForTimeout(500);
    await controls.screenshot({ path: `/tmp/latentscape-graph-controls-${name}${screenshotSuffix}.png` });
    await page.evaluate(() => window.scrollTo(0, 0));
    await page.waitForTimeout(50);
    await page.screenshot({ path: `/tmp/latentscape-graph-controls-${name}${screenshotSuffix}-page.png`, fullPage: true });
  };
  const screenshotPicker = async (name) => {
    await page.waitForTimeout(500);
    await pickerPopover.screenshot({ path: `/tmp/latentscape-graph-picker-${name}${screenshotSuffix}.png` });
    await page.evaluate(() => window.scrollTo(0, 0));
    await page.waitForTimeout(50);
    await page.screenshot({ path: `/tmp/latentscape-graph-picker-${name}${screenshotSuffix}-page.png`, fullPage: true });
  };
  const controlStyle = async () => page.evaluate(() => {
    const select = document.querySelector('#map-topic');
    const checkbox = document.querySelector('#map-two-hop');
    const track = document.querySelector('.topic-map-switch');
    if (!(select instanceof HTMLButtonElement) || !(checkbox instanceof HTMLInputElement) || !(track instanceof HTMLElement)) return null;
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
  const activeHeight = (rootIndex, includeSecond) => {
    const neighborhood = graphData.neighborhoods[rootIndex];
    const visible = neighborhood.nodes.filter((node) => includeSecond || node.degree <= 1);
    const bottom = Math.max(...visible.map((node) => node.y + (node.degree === 0 ? graphData.cardDimensions.root.height : graphData.cardDimensions.neighbor.height)));
    return Math.max(900, bottom + 60);
  };
  const assertRenderedNeighborhood = async (rootIndex, name, includeSecond = true) => {
    const expectedDirect = directFor(rootIndex);
    const expectedSecond = secondFor(rootIndex);
    const rendered = await page.evaluate(() => ({
      nodes: [...document.querySelectorAll('.topic-graph-card[data-topic-index]')].map((card) => ({ index: Number(card.getAttribute('data-topic-index')), degree: Number(card.getAttribute('data-degree')) })),
      caption: document.querySelector('.topic-graph-subset')?.textContent || '',
      height: document.querySelector('#topic-graph')?.getBoundingClientRect().height || 0,
      viewBox: document.querySelector('.topic-graph-edges')?.getAttribute('viewBox') || '',
      headings: [...document.querySelectorAll('.topic-graph-column')].map((heading) => heading.textContent?.replace(/\s+/g, ' ').trim() || ''),
      edgeBoxes: [...document.querySelectorAll('.topic-graph-edge')].map((edge) => { const box = edge.getBBox(); return { x: box.x, y: box.y, right: box.x + box.width, bottom: box.y + box.height }; }),
      cards: [...document.querySelectorAll('.topic-graph-card')].map((card) => {
        const box = card.getBoundingClientRect(); const title = card.querySelector('h2')?.getBoundingClientRect();
        return { left: box.left, right: box.right, top: box.top, bottom: box.bottom, title: title && { left: title.left, right: title.right, top: title.top, bottom: title.bottom }, scrollWidth: card.scrollWidth, clientWidth: card.clientWidth, scrollHeight: card.scrollHeight, clientHeight: card.clientHeight };
      }),
      deepestCardBottom: Math.max(0, ...[...document.querySelectorAll('.topic-graph-card')].map((card) => card.offsetTop + card.offsetHeight)),
      captionBottom: (() => { const caption = document.querySelector('.topic-graph-subset'); return caption instanceof HTMLElement ? caption.offsetTop + caption.offsetHeight : 0; })(),
    }));
    const actualDirect = new Set(rendered.nodes.filter((node) => node.degree === 1).map((node) => node.index));
    const actualSecond = new Set(rendered.nodes.filter((node) => node.degree === 2).map((node) => node.index));
    assert(sameSet(actualDirect, expectedDirect), `${name} rendered direct neighbors are not the exact outgoing+incoming union`);
    assert(includeSecond && sameSet(actualSecond, expectedSecond) && [...actualSecond].every((node) => !expectedDirect.has(node) && node !== rootIndex) || !includeSecond && actualSecond.size === 0, `${name} rendered second-degree neighbors are not the complete genuine distance-two set`);
    assert(rendered.nodes.length === new Set(rendered.nodes.map((node) => node.index)).size, `${name} rendered duplicate topic cards`);
    assert(rendered.caption.includes(`${expectedDirect.size} direct neighbours`), `${name} direct-neighbor count label is incorrect: ${rendered.caption}`);
    assert(rendered.headings.some((heading) => heading.includes(`Direct neighbours ${expectedDirect.size}`)), `${name} direct-neighbor heading count is incorrect: ${rendered.headings.join(' | ')}`);
    if (includeSecond) {
      assert(rendered.caption.includes(`${expectedSecond.size} second-degree neighbours`), `${name} second-degree count label is incorrect: ${rendered.caption}`);
      assert(rendered.headings.some((heading) => heading.includes(`Second-degree neighbours ${expectedSecond.size}`)), `${name} second-degree heading count is incorrect: ${rendered.headings.join(' | ')}`);
    }
    const expectedBounds = graphData.neighborhoods[rootIndex].bounds;
    const expectedHeight = activeHeight(rootIndex, includeSecond);
    if (includeSecond) assert(expectedBounds.height === expectedHeight, `${name} artifact bounds do not fit its complete neighborhood (${expectedBounds.height} !== ${expectedHeight})`);
    assert(Math.abs(rendered.height - expectedHeight) < 2, `${name} graph height (${rendered.height}) does not fit its visible cards (${expectedHeight})`);
    assert(rendered.viewBox === `0 0 ${expectedBounds.width} ${expectedHeight}`, `${name} graph SVG does not use its active visible bounds (${rendered.viewBox})`);
    assert(rendered.deepestCardBottom <= expectedHeight, `${name} deepest graph card escapes its active bounds (${rendered.deepestCardBottom} > ${expectedHeight})`);
    assert(rendered.captionBottom <= expectedHeight, `${name} graph count caption escapes its active bounds (${rendered.captionBottom} > ${expectedHeight})`);
    assert(rendered.edgeBoxes.every((edge) => edge.x >= -1 && edge.y >= -1 && edge.right <= expectedBounds.width + 1 && edge.bottom <= expectedHeight + 1), `${name} graph edge escapes its active bounds`);
    const cardsOverlap = rendered.cards.some((a, index) => rendered.cards.slice(index + 1).some((b) => a.left < b.right && a.right > b.left && a.top < b.bottom && a.bottom > b.top));
    assert(!cardsOverlap, `${name} rendered graph cards overlap`);
    const clipped = rendered.cards.some((card) => card.scrollWidth > card.clientWidth + 1 || card.scrollHeight > card.clientHeight + 1 || (card.title && (card.title.left < card.left - 1 || card.title.right > card.right + 1 || card.title.top < card.top - 1 || card.title.bottom > card.bottom + 1)));
    assert(!clipped, `${name} rendered graph cards are clipped`);
  };

  const started = performance.now();
  await page.goto(mapUrl(), { waitUntil: 'domcontentloaded' });
  await ready();
  const readyMs = Math.round(performance.now() - started);
  captureCold = false;
  const readyBytes = (await Promise.all(coldBodies)).reduce((total, bytes) => total + bytes, 0);
  await picker.click(); await pickerSearch.waitFor();
  const optionCount = await pickerList.locator('[role="option"]').count();
  assert(optionCount === 256, `topic picker has ${optionCount} options, expected 256`);
  const rootBeforeSearch = await root.getAttribute('data-topic-slug');
  await pickerList.locator(`[data-topic-picker-option="${rootBeforeSearch}"]`).click();
  assert(await page.evaluate(() => document.activeElement?.id === 'map-topic'), 'selecting the current topic did not return focus to the picker trigger');
  await picker.click();
  const pickerProbe = graphData.topics.find((topic) => topic.slug !== rootBeforeSearch) || graphData.topics[0];
  const partial = pickerProbe.title.slice(0, Math.max(3, Math.ceil(pickerProbe.title.length / 2)));
  await pickerSearch.fill(partial.toUpperCase());
  assert(await pickerList.locator(`[data-topic-picker-option="${pickerProbe.slug}"]`).count() === 1, 'title-case topic query did not find its topic');
  assert((await root.getAttribute('data-topic-slug')) === rootBeforeSearch, 'typing into topic search unexpectedly changed the graph');
  await screenshotPicker('light-filtered');
  await pickerSearch.fill('not-a-real-topic-query');
  assert(await page.locator('[data-topic-picker-empty]').count() === 1, 'topic picker did not show an empty result state');
  await pickerClear.click();
  assert(await pickerList.locator('[role="option"]').count() === 256, 'clearing topic search did not restore all 256 topics');
  await pickerSearch.fill(partial);
  await pickerSearch.press('Escape');
  assert(await page.locator('#map-topic-popover[hidden]').count() === 1, 'Escape did not close the topic picker');
  assert((await root.getAttribute('data-topic-slug')) === rootBeforeSearch, 'Escape changed the selected topic');
  await picker.click();
  assert(await pickerList.locator('[role="option"]').count() === 256, 'Escape did not clear the topic query for the next open');
  await pickerSearch.press('Tab'); await page.waitForTimeout(20);
  const tabState = await page.evaluate(() => ({ hidden: document.querySelector('#map-topic-popover')?.hasAttribute('hidden'), active: document.activeElement?.id, expanded: document.querySelector('#map-topic-search')?.getAttribute('aria-expanded') }));
  assert(tabState.hidden, `Tab away did not close the topic picker (active=${tabState.active || 'none'}, expanded=${tabState.expanded})`);
  await picker.click(); await pickerSearch.fill(partial);
  await page.locator('.map-page>h1').click();
  assert(await page.locator('#map-topic-popover[hidden]').count() === 1, 'outside click did not close the topic picker');
  await picker.click(); await pickerSearch.fill(pickerProbe.title.toUpperCase());
  await pickerSearch.press('ArrowDown'); await pickerSearch.press('Enter');
  await page.waitForFunction((slug) => location.hash.includes(`topic=${encodeURIComponent(slug)}`), pickerProbe.slug);
  await ready(pickerProbe.slug);
  assert((await root.getAttribute('data-topic-slug')) === pickerProbe.slug, 'keyboard topic selection did not focus the expected graph root');
  await page.goto(mapUrl(), { waitUntil: 'networkidle' }); await ready();
  assert(await page.locator('.topic-graph-card[data-degree="0"]').count() === 1, 'default graph has no single root card');
  assert(await page.locator('.topic-graph-card[data-degree="1"]').count() > 0, 'default graph has no first-degree labels');
  assert(await page.locator('.topic-graph-card[data-degree="2"]').count() > 0, 'default graph has no second-degree labels');
  assert(await page.locator('.topic-graph-edge[data-degree="1"], .topic-graph-edge.degree-1').count() > 0, 'default graph has no first-degree edges');
  assert(await page.locator('.topic-graph-edge[data-degree="2"], .topic-graph-edge.degree-2').count() > 0, 'default graph has no second-degree edges');
  await assertRenderedNeighborhood(defaultIndex, 'default');
  const lightOnStyle = await controlStyle();
  assert(lightOnStyle?.selectHeight === 56, `topic picker must be 56px high (${lightOnStyle?.selectHeight})`);
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
  await assertRenderedNeighborhood(defaultIndex, 'default depth=1', false);
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
  await assertRenderedNeighborhood(defaultIndex, 'default restored');
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
    await picker.click(); await pickerSearch.fill(topic.title);
    await pickerList.locator(`[data-topic-picker-option="${topic.slug}"]`).click();
    await page.waitForFunction((slug) => location.hash.includes(`topic=${encodeURIComponent(slug)}`), topic.slug);
    await ready(topic.slug);
    assert((await root.locator('h2').textContent())?.trim() === topic.title, `long-title topic did not become the root: ${topic.slug}`);
    await assertCardTextFits(`long-title ${topic.slug}`);
  }
  await page.goto(mapUrl(), { waitUntil: 'networkidle' }); await ready();

  const highDegree = graphData.neighborhoods.reduce((best, item) => item.secondDegreeTotal > best.secondDegreeTotal ? item : best, graphData.neighborhoods[0]);
  const highTopic = graphData.topics[highDegree.rootTopic];
  await page.goto(mapUrl(highTopic.slug), { waitUntil: 'networkidle' }); await ready(highTopic.slug);
  await assertRenderedNeighborhood(highDegree.rootTopic, 'largest-second-degree');
  await screenshot('largest-second-degree');
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
    assert(darkOnStyle?.selectHeight === 56, `dark theme picker lost its 56px target (${darkOnStyle?.selectHeight})`);
    await depth.focus(); await page.keyboard.press('Space');
    await page.waitForFunction(() => !location.hash.includes('depth=2') && document.querySelectorAll('.topic-graph-card[data-degree="2"]').length === 0);
    await page.waitForTimeout(250);
    const darkOffStyle = await controlStyle();
    assert(darkOffStyle?.visual !== darkOnStyle?.visual, 'dark theme second-degree switch did not visibly change when unchecked');
    await screenshotControls('dark-off');
    await page.setViewportSize({ width: 390, height: 844 });
    await picker.click(); await pickerSearch.fill(partial);
    const mobilePickerWidth = await page.evaluate(() => ({ scroll: document.documentElement.scrollWidth, viewport: innerWidth }));
    assert(mobilePickerWidth.scroll <= mobilePickerWidth.viewport + 1, `mobile open topic picker overflows (${mobilePickerWidth.scroll} > ${mobilePickerWidth.viewport})`);
    await screenshotPicker('mobile-dark-filtered');
    await pickerSearch.press('Escape');
    await page.setViewportSize({ width: 1440, height: 920 });
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
