import { renderRichText, plainText } from './rich-text';
import './topic-picker.css';

export interface PickerTopic { slug: string; title: string; area?: string }

const esc = (value: string) => value.replace(/[&<>'"]/g, (char) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', "'": '&#39;', '"': '&quot;' })[char]!);

/** A local, accessible topic picker. Searching changes no route until a topic is chosen. */
export function mountTopicPicker(root: HTMLElement, topics: PickerTopic[], selectedSlug: string, selectTopic: (slug: string) => void, signal: AbortSignal) {
  const trigger = root.querySelector<HTMLButtonElement>('#map-topic');
  const popover = root.querySelector<HTMLElement>('#map-topic-popover');
  const input = root.querySelector<HTMLInputElement>('#map-topic-search');
  const list = root.querySelector<HTMLElement>('#map-topic-listbox');
  const count = root.querySelector<HTMLElement>('[data-topic-picker-count]');
  const clear = root.querySelector<HTMLButtonElement>('[data-topic-picker-clear]');
  const label = root.querySelector<HTMLElement>('[data-topic-picker-label]');
  if (!trigger || !popover || !input || !list || !count || !clear || !label) throw new Error('The topic picker could not mount');
  // Listbox options are driven by aria-activedescendant; neither the listbox
  // nor its 256 options should become an extra Tab stop.
  list.tabIndex = -1;

  let query = '';
  let active = -1;
  let open = false;
  let pointerWithin = false;
  const selected = topics.find((topic) => topic.slug === selectedSlug) || topics[0];
  if (!selected) return;
  label.innerHTML = renderRichText(selected.title);
  input.setAttribute('aria-expanded', 'false');

  const matches = () => {
    const tokens = query.toLocaleLowerCase().trim().split(/\s+/).filter(Boolean);
    return topics.filter((topic) => {
      const haystack = plainText(`${topic.title} ${topic.area || ''}`).toLocaleLowerCase();
      return tokens.every((token) => haystack.includes(token));
    });
  };
  const optionId = (topic: PickerTopic) => `map-topic-option-${topic.slug.replace(/[^a-z0-9_-]/gi, '-')}`;
  const render = () => {
    const results = matches();
    if (active >= results.length) active = results.length - 1;
    if (active < 0 && results.length) active = Math.max(0, results.findIndex((topic) => topic.slug === selected.slug));
    count.textContent = results.length === 1 ? '1 topic' : `${results.length} topics`;
    clear.hidden = !query;
    if (active >= 0) input.setAttribute('aria-activedescendant', optionId(results[active])); else input.removeAttribute('aria-activedescendant');
    list.innerHTML = results.length ? results.map((topic, index) => `<button type="button" role="option" tabindex="-1" id="${optionId(topic)}" data-topic-picker-option="${esc(topic.slug)}" aria-selected="${topic.slug === selected.slug}" data-active="${index === active}"><span>${renderRichText(topic.title)}</span>${topic.area ? `<small>${renderRichText(topic.area)}</small>` : ''}</button>`).join('') : '<p class="topic-picker-empty" data-topic-picker-empty>No matching topics.</p>';
    const activeOption = list.querySelector<HTMLElement>('[data-active="true"]');
    activeOption?.scrollIntoView({ block: 'nearest' });
  };
  const close = (focusTrigger = false, resetQuery = false) => {
    open = false; popover.hidden = true; trigger.setAttribute('aria-expanded', 'false'); input.setAttribute('aria-expanded', 'false');
    if (resetQuery) { query = ''; input.value = ''; active = -1; render(); }
    if (focusTrigger) trigger.focus();
  };
  const show = () => {
    open = true; popover.hidden = false; trigger.setAttribute('aria-expanded', 'true'); input.setAttribute('aria-expanded', 'true');
    input.focus(); render();
  };
  const choose = (topic: PickerTopic | undefined) => {
    if (!topic) return;
    close(true); selectTopic(topic.slug);
  };
  const move = (next: number) => {
    const results = matches(); if (!results.length) return;
    active = (next + results.length) % results.length; render();
  };

  render();
  // The trigger is an opener: Escape or an outside interaction closes. Always
  // opening avoids a stale internal state turning a retrying click into close.
  trigger.addEventListener('click', () => show(), { signal });
  trigger.addEventListener('keydown', (event) => {
    if (event.key === 'ArrowDown' || event.key === 'ArrowUp' || event.key === 'Enter' || event.key === ' ') { event.preventDefault(); show(); if (event.key === 'ArrowUp') move(matches().length - 1); }
  }, { signal });
  input.addEventListener('input', () => { query = input.value; active = -1; render(); }, { signal });
  input.addEventListener('keydown', (event) => {
    const results = matches();
    if (event.key === 'ArrowDown') { event.preventDefault(); move(active + 1); }
    else if (event.key === 'ArrowUp') { event.preventDefault(); move(active - 1); }
    else if (event.key === 'Home' && !event.ctrlKey && !event.metaKey) { event.preventDefault(); active = 0; render(); }
    else if (event.key === 'End' && !event.ctrlKey && !event.metaKey) { event.preventDefault(); active = results.length - 1; render(); }
    else if (event.key === 'Enter') { event.preventDefault(); choose(results[active]); }
    else if (event.key === 'Escape') { event.preventDefault(); close(true, true); }
  }, { signal });
  clear.addEventListener('click', () => { query = ''; input.value = ''; active = -1; input.focus(); render(); }, { signal });
  list.addEventListener('click', (event) => {
    const button = (event.target as HTMLElement).closest<HTMLElement>('[data-topic-picker-option]');
    choose(topics.find((topic) => topic.slug === button?.dataset.topicPickerOption));
  }, { signal });
  root.addEventListener('pointerdown', () => { pointerWithin = true; }, { signal });
  document.addEventListener('pointerup', () => { pointerWithin = false; }, { signal });
  document.addEventListener('pointercancel', () => { pointerWithin = false; }, { signal });
  root.addEventListener('click', () => { pointerWithin = false; }, { signal });
  root.addEventListener('keydown', () => { pointerWithin = false; }, { capture: true, signal });
  document.addEventListener('pointerdown', (event) => { if (open && !root.contains(event.target as Node)) close(); }, { signal });
  // relatedTarget is the destination of this particular focus transition. It
  // avoids a stale deferred callback closing a picker that was reopened later.
  root.addEventListener('focusout', (event) => {
    if (open && !pointerWithin && !root.contains(event.relatedTarget as Node | null)) close();
  }, { signal });
}
