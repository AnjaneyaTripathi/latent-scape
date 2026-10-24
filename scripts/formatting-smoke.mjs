#!/usr/bin/env node
/**
 * Rich-text acceptance against production assets, without the full search index.
 * Usage: node scripts/formatting-smoke.mjs site/dist [--webkit]
 * Real title/abstract/summary text is unchanged. Related-paper lists are omitted;
 * a compact mixed-topic catalog and one explicitly synthetic hostile record test
 * the same renderer in every reading surface. No corpus files are modified.
 */
import { createRequire } from 'node:module';
import { createServer } from 'node:http';
import { readFile, stat } from 'node:fs/promises';
import { createHash } from 'node:crypto';
import { extname, join, resolve, sep } from 'node:path';

const require = createRequire(import.meta.url);
const { chromium, webkit } = require(join(process.env.LATENTSCAPE_PLAYWRIGHT_ROOT || '/Users/anjantri/Desktop/dust/node_modules', 'playwright'));
const useWebKit = process.argv.includes('--webkit');
const artifact = resolve(process.argv.slice(2).find((value) => value !== '--webkit') || 'site/dist');
const suffix = useWebKit ? '-webkit' : '';
const html = await readFile(join(artifact, 'index.html'), 'utf8');
if (!html.includes('/latent-scape/assets/')) throw new Error('Build the artifact with the /latent-scape/ deployment base first.');
const manifest = JSON.parse(await readFile(join(artifact, 'data/manifest.json'), 'utf8'));
const prefix = (slug) => createHash('sha256').update(slug).digest('hex').slice(0, 2);
async function realPaper(slug) {
  const records = JSON.parse(await readFile(join(artifact, 'data', manifest.shards.papers[prefix(slug)]), 'utf8'));
  const paper = records.find((record) => record.slug === slug);
  if (!paper) throw new Error(`Missing real formatting sample: ${slug}`);
  return { ...paper, references: [], citedBy: [], similarPapers: [] };
}
const [orf, emphasis, titleMath] = await Promise.all([
  realPaper('neurips-2016-orthogonal-random-features-d8d32c68f9'),
  realPaper('icml-2025-optimization-over-sparse-support-preserving-sets-two-step-projection-with-global-optimal-1921e732ed'),
  realPaper('neurips-2023-subp-soft-uniform-block-pruning-for-1-times-n-sparse-cnns-multithreading-acceleration-581b8946c4'),
]);
const safeProbe = 'Synthetic formatting fixture: <em>emphasis</em>, <strong>weight</strong>, H<sub>2</sub>O, x<sup>2</sup>, &amp; $\\frac{1}{2}$.';
const hostileProbe = `${safeProbe} <script>window.__formattingPwned=1</script><img src="https://formatting.invalid/probe" onerror="window.__formattingPwned=2"><svg onload="window.__formattingPwned=3"></svg><iframe src="https://formatting.invalid/frame"></iframe><a href="javascript:window.__formattingPwned=4">unsafe link</a> $\\href{javascript:window.__formattingPwned=5}{unsafe}$ $\\includegraphics{https://formatting.invalid/tex}$`;
const hostile = { ...orf, slug: 'synthetic-formatting-security-fixture', title: 'Synthetic formatting security fixture', abstract: hostileProbe, originalAbstract: hostileProbe, summary: { tldr: safeProbe, paragraphs: [safeProbe, safeProbe, hostileProbe] } };
const papers = [orf, emphasis, titleMath, hostile];
const originalCatalog = JSON.parse(await readFile(join(artifact, 'data/topic-papers', `${orf.topicSlugs[0]}.json`), 'utf8'));
const catalog = {
  ...originalCatalog,
  topic: { ...originalCatalog.topic, overview: safeProbe, whyItMatters: safeProbe, evolution: safeProbe, openQuestions: [safeProbe], paperCount: papers.length },
  relatedTopics: [], prerequisites: [],
  papers: papers.map((paper) => ({ slug: paper.slug, title: paper.title, authors: paper.authors, year: paper.year, venue: paper.venue, sourceUrl: paper.originalUrl || paper.url, sourceLinkLabel: paper.sourceLinkLabel, tldr: paper.summary?.tldr })),
};
const query = 'qzxformattingfixture';
const fixtureIndex = papers.map((paper) => ({ ...paper, id: paper.slug, type: 'paper', abstract: `${paper.abstract} ${query}`, tldr: paper.summary?.tldr }));
const fixtureManifest = { ...manifest, featuredSlugs: [], shards: { ...manifest.shards, papers: Object.fromEntries(papers.map((paper) => [prefix(paper.slug), `papers/formatting-${prefix(paper.slug)}.json`])) } };
const fixtures = new Map([
  ['/data/manifest.json', fixtureManifest], ['/data/search.json', fixtureIndex],
  ['/data/topics.json', [{ ...catalog.topic, paperSlugs: papers.map((paper) => paper.slug) }]],
  [`/data/topic-papers/${catalog.topic.slug}.json`, catalog],
  ...papers.map((paper) => [`/data/papers/formatting-${prefix(paper.slug)}.json`, papers.filter((other) => prefix(other.slug) === prefix(paper.slug))]),
]);
const mime = { '.html': 'text/html', '.css': 'text/css', '.js': 'text/javascript', '.json': 'application/json', '.svg': 'image/svg+xml', '.woff2': 'font/woff2', '.woff': 'font/woff', '.ttf': 'font/ttf' };
const problems = [];
const check = (condition, message) => { if (!condition) problems.push(message); };
process.on('uncaughtExceptionMonitor', () => { if (problems.length) console.error(problems.join('\n')); });
const server = createServer(async (request, response) => {
  const pathname = decodeURIComponent(new URL(request.url, 'http://local').pathname).replace(/^\/latent-scape(?=\/|$)/, '') || '/';
  if (fixtures.has(pathname)) { response.writeHead(200, { 'content-type': 'application/json', 'cache-control': 'no-store' }); response.end(JSON.stringify(fixtures.get(pathname))); return; }
  // Semantic ranking is outside this formatting test; fail before model loading.
  if (pathname === '/data/vectors.json') { response.writeHead(503); response.end('semantic fixture intentionally omitted'); return; }
  const file = resolve(artifact, `.${pathname === '/' ? '/index.html' : pathname}`);
  if (!file.startsWith(`${artifact}${sep}`)) { response.writeHead(403); response.end(); return; }
  try { if (!(await stat(file)).isFile()) throw new Error('not a file'); response.writeHead(200, { 'content-type': mime[extname(file)] || 'application/octet-stream' }); response.end(await readFile(file)); }
  catch { response.writeHead(404); response.end('not found'); }
});
await new Promise((done) => server.listen(0, '127.0.0.1', done));
const base = `http://127.0.0.1:${server.address().port}`;
let browser; let context;
const external = []; const fonts = new Set();
try {
  browser = await (useWebKit ? webkit : chromium).launch({ headless: true });
  context = await browser.newContext({ viewport: { width: 1440, height: 920 }, serviceWorkers: 'block' });
  await context.route('**/*', async (route) => {
    const request = route.request(); const url = new URL(request.url());
    if (url.origin !== base && !['data:', 'blob:'].includes(url.protocol)) { external.push(request.url()); await route.abort(); return; }
    if (/\.(?:woff2?|ttf)(?:\?|$)/.test(url.pathname)) fonts.add(url.pathname);
    await route.continue();
  });
  const page = await context.newPage();
  page.on('console', (message) => {
    if (message.type() === 'error' && !message.location().url?.endsWith('/data/vectors.json')) problems.push(`console: ${message.text()}`);
  });
  page.on('pageerror', (error) => problems.push(`page error: ${error.message}`));
  page.on('response', (response) => { if (response.status() >= 400 && !response.url().endsWith('/data/vectors.json')) problems.push(`HTTP ${response.status()}: ${response.url()}`); });
  let activeTheme = 'light';
  const visit = async (route, ready) => {
    await page.goto(`${base}/latent-scape/#/${route}`, { waitUntil: 'domcontentloaded' });
    await page.evaluate((theme) => { document.documentElement.dataset.theme = theme; localStorage.setItem('latentscape-theme', theme); }, activeTheme);
    await page.locator(ready).first().waitFor({ timeout: 15_000 });
    if (route.startsWith('paper/')) {
      const paper = papers.find((item) => item.slug === route.slice(6));
      if (paper) await page.waitForFunction((url) => document.querySelector('.paper-detail .paper-actions a')?.getAttribute('href') === url, paper.originalUrl || paper.url);
    }
  };
  const noOverflow = async (label) => {
    await page.evaluate(() => document.fonts.ready);
    const widths = await page.evaluate(() => ({ page: document.documentElement.scrollWidth, viewport: innerWidth }));
    check(widths.page <= widths.viewport + 1, `${label}: page overflows ${widths.page}/${widths.viewport}`);
  };
  const hasMath = async (locator, label) => {
    check(await locator.locator('.katex').count() > 0, `${label}: math is not typeset`);
    check(await locator.locator('.katex-mathml math').count() > 0, `${label}: accessible MathML is absent`);
    check(await locator.locator('.katex-error').count() === 0, `${label}: valid source math has a rendering error`);
  };
  const preserved = async (locator, source, label) => {
    // Restore the exact TeX from its accessibility annotation, then compare all
    // prose. This catches lost words without expecting duplicate MathML glyphs.
    const restored = await locator.evaluate((element) => {
      const clone = element.cloneNode(true);
      for (const math of clone.querySelectorAll('.katex')) {
        const annotation = math.querySelector('annotation[encoding="application/x-tex"]');
        if (annotation) math.replaceWith(document.createTextNode(`$${annotation.textContent}$`));
      }
      return clone.textContent;
    });
    const expected = source.replace(/<\/?(?:em|strong|b|i|sub|sup|code)>/gi, '').replace(/\r\n?/g, '\n');
    check(restored === expected, `${label}: source prose or math changed`);
    check(!/<\/?(?:em|strong|b|i|sub|sup)>/i.test(await locator.innerText()), `${label}: recognized HTML is visible as literal tags`);
  };
  const safe = async (label) => {
    check(await page.evaluate(() => !window.__formattingPwned), `${label}: source executed JavaScript`);
    check(await page.locator('main script, main iframe, main img, main svg[onload], dialog script, dialog iframe, dialog img, [href^="javascript:"]').count() === 0, `${label}: unsafe source created active DOM`);
  };

  for (const width of [1440, 320]) {
    await page.setViewportSize({ width, height: width === 320 ? 844 : 920 });
    for (const theme of ['light', 'dark']) {
      activeTheme = theme;
      const label = `${width}-${theme}`;
      await visit(`paper/${orf.slug}`, '.official-abstract');
      const abstract = page.locator('.official-abstract > p').nth(1);
      await hasMath(abstract, `${label} ORF abstract`);
      await preserved(abstract, orf.originalAbstract, `${label} ORF abstract`);
      await noOverflow(`${label} full paper`);
      await abstract.screenshot({ path: `/tmp/latentscape-formatting-orf-${label}${suffix}.png` });

      await visit(`paper/${emphasis.slug}`, '.official-abstract');
      const emphasized = page.locator('.official-abstract > p').nth(1);
      await hasMath(emphasized, `${label} HTML abstract`);
      await preserved(emphasized, emphasis.originalAbstract, `${label} HTML abstract`);
      check(await emphasized.locator('em').textContent() === 'support-preserving', `${label}: real emphasis is absent`);
      await noOverflow(`${label} HTML paper`);

      await visit(`paper/${titleMath.slug}`, '.paper-detail h1');
      await hasMath(page.locator('.paper-detail h1'), `${label} full paper title`);
      await preserved(page.locator('.paper-detail h1'), titleMath.title, `${label} full paper title`);
      await noOverflow(`${label} math title paper`);

      await visit(`search?q=${query}`, '[data-search-card]');
      await page.waitForFunction(() => !document.querySelector('[data-search-result-count]')?.textContent.includes('Loading semantic search'));
      const mathCard = page.locator(`[data-search-card][data-paper-slug="${titleMath.slug}"]`);
      await hasMath(mathCard.locator('h3'), `${label} search title`);
      await preserved(mathCard.locator('h3 a'), titleMath.title, `${label} search title`);
      const syntheticCard = page.locator(`[data-search-card][data-paper-slug="${hostile.slug}"]`);
      await hasMath(syntheticCard.locator('.search-result-tldr'), `${label} search TL;DR`);
      check(await syntheticCard.locator('.search-result-tldr em').count() === 1, `${label}: search TL;DR emphasis is absent`);
      await noOverflow(`${label} search cards`);
      const dialog = page.locator('dialog[data-paper-dialog][open]');
      for (const paper of [orf, emphasis, hostile]) {
        await page.locator(`[data-search-card][data-paper-slug="${paper.slug}"] [data-summary-toggle]`).click();
        await dialog.locator('.search-abstract-text').waitFor();
        if (paper !== hostile) {
          await hasMath(dialog.locator('.search-abstract-text'), `${label} modal ${paper.slug}`);
          await preserved(dialog.locator('.search-abstract-text'), paper.originalAbstract, `${label} modal ${paper.slug}`);
        } else {
          await hasMath(dialog.locator('.search-summary-tldr'), `${label} modal TL;DR`);
          await safe(`${label} hostile modal`);
        }
        const bounds = await dialog.boundingBox();
        check(bounds && bounds.x >= -1 && bounds.x + bounds.width <= width + 1, `${label}: modal extends past viewport`);
        await noOverflow(`${label} modal`);
        await page.keyboard.press('Escape');
      }

      await visit(`topic/${catalog.topic.slug}`, '.topic-reader');
      await hasMath(page.locator('.detail-lede'), `${label} topic introduction`);
      await hasMath(page.locator('.topic-paper-preview'), `${label} topic cards`);
      check(await page.locator('.detail-lede em').count() === 1, `${label}: topic introduction emphasis is absent`);
      await noOverflow(`${label} topic overview`);
      await visit(`topic/${catalog.topic.slug}?tab=papers`, '[data-topic-paper-row]');
      await hasMath(page.locator('.topic-paper-list'), `${label} catalog title`);
      const detail = page.locator(`[data-topic-paper-tldr="${hostile.slug}"]`);
      if (await detail.getAttribute('open') === null) await detail.locator('summary').click();
      await hasMath(detail.locator('p'), `${label} catalog TL;DR`);
      check(await detail.locator('p em').count() === 1, `${label}: catalog TL;DR emphasis is absent`);
      await noOverflow(`${label} topic catalog`);

      await visit(`paper/${hostile.slug}`, '.official-abstract');
      await safe(`${label} hostile full paper`);
      await noOverflow(`${label} hostile full paper`);
    }
  }
  await page.evaluate(() => document.fonts.ready);
  const loadedFonts = await page.evaluate(() => [...document.fonts].filter((font) => /KaTeX/i.test(font.family) && font.status === 'loaded').map((font) => font.family));
  check(fonts.size > 0 && loadedFonts.length > 0, 'Bundled KaTeX fonts were not requested and loaded');
  check([...fonts].every((path) => path.startsWith('/latent-scape/assets/')), 'Math fonts do not use deployed local assets');
  check(external.length === 0, `Unexpected external requests: ${[...new Set(external)].join(', ')}`);
} finally { await context?.close(); await browser?.close(); await new Promise((done) => server.close(done)); }
if (problems.length) { console.error(`Formatting smoke failed:\n${problems.map((problem) => `- ${problem}`).join('\n')}`); process.exitCode = 1; }
else console.log(JSON.stringify({ status: 'ok', browser: useWebKit ? 'webkit' : 'chromium', samples: papers.map((paper) => paper.slug), localFonts: fonts.size, screenshots: `/tmp/latentscape-formatting-orf-*${suffix}.png` }));
