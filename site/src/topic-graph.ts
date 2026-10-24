import { mountTopicPicker } from './topic-picker';
import { renderRichText } from './rich-text';

export interface GraphTopic { slug: string; title: string; area: string; paperCount: number; overview?: string; relatedTopics: number[] }
interface GraphNode { topic: number; degree: 0 | 1; x: number; y: number }
interface GraphEdge { source: number; target: number; degree: 1 }
interface Neighborhood { rootTopic: number; bounds: { width: number; height: number }; nodes: GraphNode[]; edges: GraphEdge[]; directNeighborTotal: number }
export interface TopicGraphData { version: 3; layoutKind: 'focused-semantic-neighborhoods'; defaultTopicSlug: string; cardDimensions: { root: { width: number; height: number }; neighbor: { width: number; height: number } }; topics: GraphTopic[]; neighborhoods: Neighborhood[] }

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

export function mountTopicGraph(container: HTMLElement, data: TopicGraphData, slug?: string) {
  const abort = new AbortController();
  const topicIndex = new Map(data.topics.map((topic, index) => [topic.slug, index]));
  let rootIndex = topicIndex.get(slug || '') ?? topicIndex.get(data.defaultTopicSlug) ?? 0;
  const picker = container.querySelector<HTMLElement>('[data-topic-picker]');
  const graph = container.querySelector<HTMLElement>('#topic-graph');
  if (!picker || !graph) throw new Error('The knowledge map could not mount');
  const graphRoot = graph;

  function routeTo(nextIndex: number) {
    const next = data.topics[nextIndex];
    if (!next) return;
    location.hash = `#/map?topic=${encodeURIComponent(next.slug)}`;
  }
  function render() {
    const neighborhood = data.neighborhoods.find((item) => item.rootTopic === rootIndex) || data.neighborhoods[0];
    if (!neighborhood) return;
    rootIndex = neighborhood.rootTopic;
    const visible = neighborhood.nodes;
    const visibleIndices = new Set(visible.map((node) => node.topic));
    const nodeByIndex = new Map(visible.map((node) => [node.topic, node]));
    const edges = neighborhood.edges.filter((edge) => visibleIndices.has(edge.source) && visibleIndices.has(edge.target));
    const svgEdges = edges.map((edge) => {
      const source = nodeByIndex.get(edge.source)!; const target = nodeByIndex.get(edge.target)!;
      const dimensions = (node: GraphNode) => node.degree === 0 ? data.cardDimensions.root : data.cardDimensions.neighbor;
      const sourceSize = dimensions(source); const targetSize = dimensions(target);
      const leftToRight = source.x <= target.x;
      const x1 = leftToRight ? source.x + sourceSize.width : source.x; const y1 = source.y + sourceSize.height / 2;
      const x2 = leftToRight ? target.x : target.x + targetSize.width; const y2 = target.y + targetSize.height / 2;
      const path = `M ${x1} ${y1} C ${x1 + (leftToRight ? 46 : -46)} ${y1}, ${x2 + (leftToRight ? -46 : 46)} ${y2}, ${x2} ${y2}`;
      return `<path class="topic-graph-edge degree-${edge.degree}" data-edge-source="${edge.source}" data-edge-target="${edge.target}" d="${path}"/>`;
    }).join('');
    const cards = visible.map((node) => {
      const topic = data.topics[node.topic]; const isRoot = node.degree === 0;
      const style = `--x:${node.x}px;--y:${node.y}px`;
      const classes = `topic-graph-card degree-${node.degree}${isRoot ? ' is-root' : ''}`;
      const attrs = `data-topic-index="${node.topic}" data-topic-slug="${esc(topic.slug)}" data-degree="${node.degree}" style="${style}"`;
      const content = `${isRoot ? `<p class="topic-graph-area">${renderRichText(topic.area || 'Research area')}</p>` : ''}<h2>${renderRichText(topic.title)}</h2>${isRoot ? `<p class="topic-graph-overview">${renderRichText(topic.overview || 'Explore the papers and related concepts for this topic.')}</p>` : ''}<p class="topic-graph-count">${count(topic.paperCount)} papers</p>`;
      return isRoot ? `<article class="${classes}" ${attrs}>${content}<a data-graph-read-topic href="#/topic/${encodeURIComponent(topic.slug)}">Read topic <span aria-hidden="true">→</span></a></article>` : `<a class="${classes}" ${attrs} href="#/map?topic=${encodeURIComponent(topic.slug)}">${content}<span class="topic-graph-action">Explore connections <span aria-hidden="true">→</span></span></a>`;
    }).join('');
    const dimensions = (node: GraphNode) => node.degree === 0 ? data.cardDimensions.root : data.cardDimensions.neighbor;
    const activeHeight = Math.max(480, ...visible.map((node) => node.y + dimensions(node).height + 60));
    const subset = `<p class="topic-graph-subset">${neighborhood.directNeighborTotal} direct neighbours</p>`;
    graphRoot.style.setProperty('--graph-height', `${activeHeight}px`);
    graphRoot.innerHTML = `<svg class="topic-graph-edges" viewBox="0 0 ${neighborhood.bounds.width} ${activeHeight}" aria-hidden="true">${svgEdges}</svg><div class="topic-graph-column focus">Focus topic</div><div class="topic-graph-column direct">Direct neighbours <span>${neighborhood.directNeighborTotal}</span></div>${subset}${cards}`;
    graphRoot.querySelectorAll<HTMLElement>('.topic-graph-card').forEach((card) => {
      const index = Number(card.dataset.topicIndex);
      const emphasize = (on: boolean) => graphRoot.querySelectorAll<SVGPathElement>('.topic-graph-edge').forEach((edge) => edge.classList.toggle('is-related', on && (Number(edge.dataset.edgeSource) === index || Number(edge.dataset.edgeTarget) === index)));
      card.addEventListener('pointerenter', () => emphasize(true), { signal: abort.signal });
      card.addEventListener('pointerleave', () => emphasize(false), { signal: abort.signal });
      card.addEventListener('focusin', () => emphasize(true), { signal: abort.signal });
      card.addEventListener('focusout', () => emphasize(false), { signal: abort.signal });
    });
  }
  mountTopicPicker(picker, data.topics, data.topics[rootIndex]?.slug || data.defaultTopicSlug, (nextSlug) => {
    const index = topicIndex.get(nextSlug); if (index !== undefined) routeTo(index);
  }, abort.signal);
  render();
  return () => abort.abort();
}
