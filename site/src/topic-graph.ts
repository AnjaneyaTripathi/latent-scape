export interface GraphTopic { slug: string; title: string; area: string; paperCount: number; overview?: string; relatedTopics: number[] }
interface GraphNode { topic: number; degree: 0 | 1 | 2; x: number; y: number }
interface GraphEdge { source: number; target: number; degree: 1 | 2 }
interface Neighborhood { rootTopic: number; nodes: GraphNode[]; edges: GraphEdge[]; directNeighborTotal: number; secondDegreeTotal: number }
export interface TopicGraphData { version: 1; layoutKind: 'focused-semantic-neighborhoods'; defaultTopicSlug: string; bounds: { width: number; height: number }; cardDimensions: { root: { width: number; height: number }; neighbor: { width: number; height: number } }; topics: GraphTopic[]; neighborhoods: Neighborhood[] }

let graphLoad: Promise<TopicGraphData> | undefined;
export function loadTopicGraph() {
  graphLoad ??= fetch(`${import.meta.env.BASE_URL}data/topic-graph.json`).then(async (response) => {
    if (!response.ok) throw new Error(`Could not load the knowledge map (${response.status})`);
    return await response.json() as TopicGraphData;
  }).catch((error: unknown) => { graphLoad = undefined; throw error; });
  return graphLoad;
}

const esc = (value: string) => value.replace(/[&<>'"]/g, (char) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', "'": '&#39;', '"': '&quot;' })[char]!);
const count = (value: number) => new Intl.NumberFormat().format(value);

export function mountTopicGraph(container: HTMLElement, data: TopicGraphData, slug?: string, depth: 1 | 2 = 2) {
  const abort = new AbortController();
  const topicIndex = new Map(data.topics.map((topic, index) => [topic.slug, index]));
  let rootIndex = topicIndex.get(slug || '') ?? topicIndex.get(data.defaultTopicSlug) ?? 0;
  const picker = container.querySelector<HTMLSelectElement>('#map-topic');
  const depthControl = container.querySelector<HTMLInputElement>('#map-two-hop');
  const graph = container.querySelector<HTMLElement>('#topic-graph');
  if (!picker || !depthControl || !graph) throw new Error('The knowledge map could not mount');
  const topicPicker = picker;
  const twoHop = depthControl;
  const graphRoot = graph;

  function routeTo(nextIndex: number) {
    const next = data.topics[nextIndex];
    if (!next) return;
    location.hash = `#/map?topic=${encodeURIComponent(next.slug)}&depth=${twoHop.checked ? '2' : '1'}`;
  }
  function render() {
    const neighborhood = data.neighborhoods.find((item) => item.rootTopic === rootIndex) || data.neighborhoods[0];
    if (!neighborhood) return;
    rootIndex = neighborhood.rootTopic;
    const visible = neighborhood.nodes.filter((node) => node.degree <= (twoHop.checked ? 2 : 1));
    const visibleIndices = new Set(visible.map((node) => node.topic));
    const nodeByIndex = new Map(visible.map((node) => [node.topic, node]));
    const root = data.topics[rootIndex];
    const edges = neighborhood.edges.filter((edge) => visibleIndices.has(edge.source) && visibleIndices.has(edge.target));
    const svgEdges = edges.map((edge) => {
      const source = nodeByIndex.get(edge.source)!; const target = nodeByIndex.get(edge.target)!;
      const dimensions = (node: GraphNode) => node.degree === 0 ? data.cardDimensions.root : data.cardDimensions.neighbor;
      const sourceSize = dimensions(source); const targetSize = dimensions(target);
      const leftToRight = source.x <= target.x;
      const x1 = leftToRight ? source.x + sourceSize.width : source.x; const y1 = source.y + sourceSize.height / 2;
      const x2 = leftToRight ? target.x : target.x + targetSize.width; const y2 = target.y + targetSize.height / 2;
      const sameColumn = Math.abs(source.x - target.x) < 1;
      const gutterX = source.x >= 700 ? source.x - 34 : source.x + sourceSize.width + 34;
      const path = sameColumn
        ? `M ${source.x >= 700 ? source.x : source.x + sourceSize.width} ${y1} C ${gutterX} ${y1}, ${gutterX} ${y2}, ${target.x >= 700 ? target.x : target.x + targetSize.width} ${y2}`
        : `M ${x1} ${y1} C ${x1 + (leftToRight ? 72 : -72)} ${y1}, ${x2 + (leftToRight ? -72 : 72)} ${y2}, ${x2} ${y2}`;
      return `<path class="topic-graph-edge degree-${edge.degree}" data-edge-source="${edge.source}" data-edge-target="${edge.target}" d="${path}"/>`;
    }).join('');
    const cards = visible.map((node) => {
      const topic = data.topics[node.topic]; const isRoot = node.degree === 0;
      const style = `--x:${node.x}px;--y:${node.y}px`;
      const classes = `topic-graph-card degree-${node.degree}${isRoot ? ' is-root' : ''}`;
      const attrs = `data-topic-index="${node.topic}" data-topic-slug="${esc(topic.slug)}" data-degree="${node.degree}" style="${style}"`;
      const content = `${isRoot ? `<p class="topic-graph-area">${esc(topic.area || 'Research area')}</p>` : ''}<h2>${esc(topic.title)}</h2>${isRoot ? `<p class="topic-graph-overview">${esc(topic.overview || 'Explore the papers and related concepts for this topic.')}</p>` : ''}<p class="topic-graph-count">${count(topic.paperCount)} papers</p>`;
      return isRoot ? `<article class="${classes}" ${attrs}>${content}<a data-graph-read-topic href="#/topic/${encodeURIComponent(topic.slug)}">Read topic <span aria-hidden="true">→</span></a></article>` : `<a class="${classes}" ${attrs} href="#/map?topic=${encodeURIComponent(topic.slug)}&depth=${twoHop.checked ? '2' : '1'}">${content}<span class="topic-graph-action">Explore connections <span aria-hidden="true">→</span></span></a>`;
    }).join('');
    const shownDirect = visible.filter((node) => node.degree === 1).length;
    const shownSecond = visible.filter((node) => node.degree === 2).length;
    const directCaption = neighborhood.directNeighborTotal > shownDirect ? `${shownDirect} of ${neighborhood.directNeighborTotal} direct neighbours` : `${shownDirect} direct neighbours`;
    const secondCaption = neighborhood.secondDegreeTotal > shownSecond ? `${shownSecond} of ${neighborhood.secondDegreeTotal} second-degree neighbours` : `${shownSecond} second-degree neighbours`;
    const subset = `<p class="topic-graph-subset">${directCaption}${twoHop.checked ? ` · ${secondCaption}` : ''}</p>`;
    graphRoot.innerHTML = `<svg class="topic-graph-edges" viewBox="0 0 ${data.bounds.width} ${data.bounds.height}" aria-hidden="true">${svgEdges}</svg><div class="topic-graph-column focus">Focus topic</div><div class="topic-graph-column direct">Direct neighbours</div>${twoHop.checked ? '<div class="topic-graph-column second">Second-degree neighbours</div>' : ''}${subset}${cards}`;
    graphRoot.querySelectorAll<HTMLElement>('.topic-graph-card').forEach((card) => {
      const index = Number(card.dataset.topicIndex);
      const emphasize = (on: boolean) => graphRoot.querySelectorAll<SVGPathElement>('.topic-graph-edge').forEach((edge) => edge.classList.toggle('is-related', on && (Number(edge.dataset.edgeSource) === index || Number(edge.dataset.edgeTarget) === index)));
      card.addEventListener('pointerenter', () => emphasize(true), { signal: abort.signal });
      card.addEventListener('pointerleave', () => emphasize(false), { signal: abort.signal });
      card.addEventListener('focusin', () => emphasize(true), { signal: abort.signal });
      card.addEventListener('focusout', () => emphasize(false), { signal: abort.signal });
    });
    topicPicker.value = root.slug;
  }
  topicPicker.addEventListener('change', () => { const index = topicIndex.get(topicPicker.value); if (index !== undefined) routeTo(index); }, { signal: abort.signal });
  twoHop.checked = depth === 2;
  twoHop.addEventListener('change', () => routeTo(rootIndex), { signal: abort.signal });
  render();
  return () => abort.abort();
}
