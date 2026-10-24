import { describe, expect, it } from 'vitest';
import { filterTopicPapers, normalizeTopicPaperState, topicRoute, type TopicPaper } from './topic-papers';

const papers: TopicPaper[] = [
  { slug: 'first', title: 'Zebra learning', authors: ['Ada Author'], year: 2020, venue: 'ICML', sourceUrl: 'https://example.test/first', sourceLinkLabel: 'Official source', tldr: 'A compact overview.' },
  { slug: 'second', title: 'Alpha methods', authors: ['Béla Researcher'], year: 2023, venue: 'NeurIPS', sourceUrl: 'https://example.test/second', sourceLinkLabel: 'Official source', tldr: 'Studies L1 & L2.' },
  { slug: 'third', title: 'Causal models', authors: ['Cara'], year: 2022, venue: 'ICML', sourceUrl: 'https://example.test/third', sourceLinkLabel: 'Official source' },
];

describe('topic paper state', () => {
  it('normalizes invalid filters and finite positive page values', () => {
    expect(normalizeTopicPaperState('tab=papers&year=2099&venue=Unknown&sort=sideways&page=1.5', papers)).toEqual({ q: '', year: '', venue: '', sort: 'newest', page: 1 });
    expect(normalizeTopicPaperState('year=2023&venue=NeurIPS&sort=oldest&page=3', papers)).toMatchObject({ year: '2023', venue: 'NeurIPS', sort: 'oldest', page: 3 });
  });

  it('filters title, authors, and summaries, then sorts deterministically', () => {
    expect(filterTopicPapers(papers, { q: 'béla', year: '', venue: '', sort: 'newest', page: 1 }).map((paper) => paper.slug)).toEqual(['second']);
    expect(filterTopicPapers(papers, { q: 'l1 & l2', year: '', venue: '', sort: 'newest', page: 1 }).map((paper) => paper.slug)).toEqual(['second']);
    expect(filterTopicPapers(papers, { q: '', year: '', venue: '', sort: 'title', page: 1 }).map((paper) => paper.slug)).toEqual(['second', 'third', 'first']);
  });

  it('serializes special query characters as a round-trippable canonical route', () => {
    const href = topicRoute('valid-topic', 'papers', { q: 'L1 & L2 + 50%? κ', year: '', venue: '', sort: 'newest', page: 1 });
    const query = href.split('?')[1];
    expect(new URLSearchParams(query).get('q')).toBe('L1 & L2 + 50%? κ');
    expect(href).toBe('#/topic/valid-topic?tab=papers&q=L1+%26+L2+%2B+50%25%3F+%CE%BA');
  });
});
