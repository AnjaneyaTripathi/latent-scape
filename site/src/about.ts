export interface AboutManifest {
  snapshot?: string;
  totalPapers?: number;
  totalTopics?: number;
}

const escapeHtml = (value: string) => value.replace(/[&<>'"]/g, (character) => ({
  '&': '&amp;', '<': '&lt;', '>': '&gt;', "'": '&#39;', '"': '&quot;',
}[character]!));

const formatCount = (value: number | undefined, fallback: string) => {
  if (typeof value !== 'number' || !Number.isSafeInteger(value) || value < 0) return fallback;
  return new Intl.NumberFormat().format(value);
};

const formatSnapshot = (value: string | undefined) => {
  if (!value || !/^\d{4}-\d{2}-\d{2}$/.test(value)) return 'a fixed date';
  const year = Number(value.slice(0, 4));
  const month = Number(value.slice(5, 7));
  const day = Number(value.slice(8, 10));
  return new Intl.DateTimeFormat('en', { year: 'numeric', month: 'long', day: 'numeric', timeZone: 'UTC' })
    .format(new Date(Date.UTC(year, month - 1, day)));
};

/** Reader-facing context; all operational provenance stays outside the site. */
export function renderAbout(manifest: AboutManifest): string {
  const papers = formatCount(manifest.totalPapers, 'the available');
  const topics = formatCount(manifest.totalTopics, 'curated');
  const snapshot = escapeHtml(formatSnapshot(manifest.snapshot));
  return `<section class="page-top about"><p class="kicker">ABOUT LATENTSCAPE</p><h1>Research is a landscape.</h1><p class="lede">Latentscape is a guide to the ideas that connect machine-learning research. It brings NeurIPS and ICML papers into a place where you can begin with a question, follow a concept, and decide what to read next.</p><div class="about-grid"><article><h2>Find an idea</h2><p>Search in plain language to surface both research topics and papers. Use the knowledge map or topic library when you want to browse the field instead.</p></article><article><h2>Understand a paper</h2><p>Topic pages provide context and related work. Paper pages preserve the source abstract when it is available and add a clearly labelled AI-generated explanation as a reading aid.</p></article><article><h2>Read the original</h2><p>Paper pages link to original sources or publication records. Follow the available link for methods, evidence, and the full technical record.</p></article></div><section class="prose about-context"><h2>A fixed research snapshot</h2><p>This edition covers ${papers} NeurIPS and ICML papers, organized into ${topics} topics, from a snapshot dated ${snapshot}. It is a guide to a fixed collection, not a live feed.</p><h2>Reading with care</h2><p>AI-generated explanations are based on the available source abstract and do not replace the original paper. Some historical records do not have enough source text for an explanation, and coverage of the early ICML 1980 and 1985 workshops is incomplete. Records with missing source text are marked accordingly.</p><p>You can revisit loaded pages offline, and search works offline after it has finished loading. Source and publication links require an internet connection.</p></section></section>`;
}
