import katex from 'katex';
import { decodeHTML } from 'entities';
import 'katex/dist/katex.min.css';
import './rich-text.css';

// Source text is never inserted as HTML. Only these attribute-free tags and
// KaTeX's untrusted-input output cross the innerHTML boundary.
const INLINE_TAGS = new Set(['em', 'i', 'strong', 'b', 'sub', 'sup', 'code', 'u', 's', 'small']);
const TEXT_COMMANDS: Record<string, string> = {
  emph: 'em', textit: 'em', textsl: 'em', textbf: 'strong', texttt: 'code',
  underline: 'u', textrm: 'span', textnormal: 'span', textup: 'span',
  textsf: 'span', textsc: 'small', text: 'span', mbox: 'span',
};
const LEGACY_COMMANDS: Record<string, string> = {
  em: 'em', it: 'em', sl: 'em', bf: 'strong', tt: 'code', rm: 'span', sf: 'span', sc: 'small',
};
const PROSE_COMMANDS: Record<string, string> = {
  eg: 'e.g.', ie: 'i.e.', texttimes: '×', textemdash: '—', textendash: '–', textmu: 'µ',
};
const MATH_STYLES = ['boldsymbol', 'mathfrak', 'mathcal', 'mathbb', 'mathbf', 'mathit', 'mathrm', 'mathsf', 'mathtt', 'sqrt'];
const FUSED_PREFIXES = [...Object.keys(TEXT_COMMANDS), ...MATH_STYLES].sort((a, b) => b.length - a.length);
const MATH_SYMBOLS = new Set(('alpha beta gamma delta epsilon varepsilon zeta eta theta vartheta iota kappa lambda mu nu xi pi varpi rho varrho sigma varsigma tau upsilon phi varphi chi psi omega Gamma Delta Theta Lambda Xi Pi Sigma Upsilon Phi Psi Omega times cdot pm mp leq geq neq approx sim in notin subset subseteq supset supseteq to rightarrow leftarrow infty partial nabla ell ldots cdots').split(' '));

function fusedCommand(name: string): { command: string; letters: string } | undefined {
  // Tokenize the complete control word first: regex prefix backtracking could
  // otherwise turn valid \\textit into \\text{it} during an unrelated repair.
  if (FUSED_PREFIXES.includes(name)) return;
  for (const command of FUSED_PREFIXES) {
    if (!name.startsWith(command)) continue;
    const letters = name.slice(command.length);
    if (!letters || (command === 'sqrt' && letters.length !== 1)) continue;
    // Ordinary KaTeX controls like textcolor/textcircled must stay intact.
    if (command === 'text' && !/^[A-Z]/.test(letters)) continue;
    return { command, letters };
  }
}
const escapeHTML = (value: string): string => value.replace(/[&<>"']/g, char => ({
  '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
})[char]!);

function escapedAt(value: string, index: number): boolean {
  let backslashes = 0;
  while (index > 0 && value[--index] === '\\') backslashes++;
  return backslashes % 2 === 1;
}

function groupAt(value: string, start: number): { body: string; end: number } | undefined {
  if (value[start] !== '{') return;
  let depth = 1;
  for (let i = start + 1; i < value.length; i++) {
    if (escapedAt(value, i)) continue;
    if (value[i] === '{') depth++;
    if (value[i] === '}' && --depth === 0) return { body: value.slice(start + 1, i), end: i + 1 };
  }
}

type MathToken = { body: string; source: string; end: number; display: boolean };

function htmlTagAt(value: string, start: number): { name: string; closing: boolean; source: string } | undefined {
  const head = /^<(\/?)([A-Za-z][\w:-]*)(?=[\s/>])/.exec(value.slice(start));
  if (!head) return;
  let quote = '';
  for (let i = start + head[0].length; i < Math.min(value.length, start + 4096); i++) {
    const char = value[i];
    if (quote) { if (char === quote) quote = ''; }
    else if (char === '"' || char === "'") quote = char;
    else if (char === '<') return;
    else if (char === '>') return { name: head[2].toLowerCase(), closing: !!head[1], source: value.slice(start, i + 1) };
  }
}

function mathAt(value: string, start: number): MathToken | undefined {
  const environment = /^\\begin\{(equation\*?|align\*?|gather\*?)\}/.exec(value.slice(start));
  const opener = environment?.[0] ?? (value.startsWith('$$', start) ? '$$'
    : value.startsWith('\\(', start) ? '\\('
      : value.startsWith('\\[', start) ? '\\[' : value[start] === '$' ? '$' : '');
  if (!opener) return;
  const closer = environment ? `\\end{${environment[1]}}`
    : opener === '\\(' ? '\\)' : opener === '\\[' ? '\\]' : opener;
  const bodyStart = start + opener.length;
  let end = bodyStart;
  while ((end = value.indexOf(closer, end)) !== -1) {
    if (escapedAt(value, end)) { end += closer.length; continue; }
    const body = value.slice(bodyStart, end);
    if (!body.trim()) return;
    if (opener === '$') {
      // Dollar math has tight boundaries. Numeric dollar amounts followed by
      // prose are currency, even when a later price supplies a second dollar.
      if (/\n\s*\n/.test(body)) return;
      if (/^\d[\d,.]*(?:\s+[A-Za-z]{2,}|[A-Za-z]+\s+[A-Za-z]{2,})/.test(body.trim())) return;
      if (/^\d[\d,.]*\s*[-–/,]\s*$/.test(body) && /\d/.test(value[end + 1] ?? '')) return;
      if (/^[A-Za-z]{2,}\s+[A-Za-z]{2,}\b/.test(body.trim())) return;
    }
    let tex = body;
    if (environment && /^(align|gather)/.test(environment[1])) {
      tex = `\\begin{${environment[1].startsWith('align') ? 'aligned' : 'gathered'}}${body}\\end{${environment[1].startsWith('align') ? 'aligned' : 'gathered'}}`;
    }
    return { body: tex, source: value.slice(start, end + closer.length), end: end + closer.length,
      display: opener !== '$' && opener !== '\\(' };
  }
}

// Convert generated MathML to a useful text label without a browser DOM or
// duplicating the hidden accessibility/visual copies in KaTeX's HTML output.
function mathLabel(markup: string): string {
  const stack: { tag: string; parts: string[] }[] = [{ tag: '', parts: [] }];
  const source = markup.replace(/<annotation\b[^>]*>[\s\S]*?<\/annotation>/g, '');
  for (const token of source.match(/<[^>]*>|[^<]+/g) ?? []) {
    if (token.startsWith('</')) {
      if (stack.length === 1) continue;
      const node = stack.pop()!;
      const [a = '', b = '', c = ''] = node.parts;
      const content = node.tag === 'msup' ? `${a}^${b.length > 1 ? `(${b})` : b}`
        : node.tag === 'msub' ? `${a}_${b.length > 1 ? `(${b})` : b}`
          : node.tag === 'msubsup' ? `${a}_${b}^${c}`
            : node.tag === 'mfrac' ? `(${a})/(${b})`
              : node.tag === 'msqrt' ? `√(${node.parts.join('')})` : node.parts.join('');
      stack[stack.length - 1].parts.push(content);
    } else if (token.startsWith('<')) {
      if (!token.endsWith('/>')) stack.push({ tag: /^<([\w-]+)/.exec(token)?.[1] ?? '', parts: [] });
    } else stack[stack.length - 1].parts.push(decodeHTML(token));
  }
  return stack[0].parts.join('');
}

function renderMath(token: MathToken, plain: boolean): string {
  const fallback = () => plain ? token.source : `<span class="rich-math-fallback" title="Original notation (could not be typeset)">${escapeHTML(token.source)}</span>`;
  if (token.body.length > 8192) return fallback();
  // Imported abstracts commonly omit the TeX escape on numeric percentages.
  // Without this narrow repair KaTeX silently treats the unit as a comment.
  const source = token.body.replace(/(\d)%/g, '$1\\%');
  const render = (tex: string): string => {
    const rendered = katex.renderToString(tex, {
      displayMode: token.display, throwOnError: true, trust: false,
      strict: 'ignore', maxSize: 20, maxExpand: 500,
      output: plain ? 'mathml' : 'htmlAndMathml',
      // A fresh object prevents a paper's macro definitions affecting another.
      macros: { '\\nicefrac': '\\frac{#1}{#2}', '\\mbox': '\\text{#1}' },
    });
    return plain ? mathLabel(rendered)
      : `<span class="rich-math${token.display ? ' rich-math-display' : ''}">${rendered}</span>`;
  };
  try {
    return render(source);
  } catch {
    // Some imported titles lost braces around typography arguments. Only after
    // parsing fails, split an explicitly known formatting prefix from its
    // literal letters. Never invent expansions for author-defined macros.
    const repaired = source.replace(/\\([A-Za-z]+)/g, (whole, name: string) => {
      const fused = fusedCommand(name);
      return fused ? `\\${fused.command}{${fused.letters}}` : whole;
    });
    if (repaired !== source) {
      try { return render(repaired); } catch { /* Preserve the original below. */ }
    }
    return fallback();
  }
}

function format(value: string, plain: boolean, depth = 0): string {
  if (depth > 32) return plain ? value : escapeHTML(value);
  const output: string[] = [];
  const tags: string[] = [];
  const text = (part: string) => plain ? part : escapeHTML(part);
  const wrapped = (tag: string, body: string) => plain ? body : `<${tag}>${body}</${tag}>`;
  for (let i = 0; i < value.length;) {
    const char = value[i];
    if (char === '$' || char === '\\') {
      const math = mathAt(value, i);
      if (math) { output.push(renderMath(math, plain)); i = math.end; continue; }
    }
    if (char === '<') {
      // Quoted > characters in attributes cannot terminate a tag early.
      const tag = htmlTagAt(value, i);
      if (tag) {
        const name = tag.name;
        if (name === 'br' || name === 'p') {
          output.push(plain ? '\n' : '<br>'); i += tag.source.length; continue;
        }
        if (INLINE_TAGS.has(name)) {
          if (tag.closing) {
            const opening = tags.lastIndexOf(name);
            if (opening !== -1) {
              while (tags.length > opening) {
                const closing = tags.pop()!;
                if (!plain) output.push(`</${closing}>`);
              }
            }
          } else {
            if (!plain) output.push(`<${name}>`);
            tags.push(name);
          }
          i += tag.source.length; continue;
        }
        output.push(text(tag.source)); i += tag.source.length; continue;
      }
    }
    if (char === '\\') {
      const accent = /^\\([uvHck])(?=\s|\{)\s*(?:\{([A-Za-z])\}|([A-Za-z]))/.exec(value.slice(i))
        ?? /^\\(["'`^~=.])(?:\{([A-Za-z])\}|([A-Za-z]))/.exec(value.slice(i));
      if (accent) {
        const combining: Record<string, string> = { '"': '\u0308', "'": '\u0301', '`': '\u0300', '^': '\u0302', '~': '\u0303', '=': '\u0304', '.': '\u0307', u: '\u0306', v: '\u030c', H: '\u030b', c: '\u0327', k: '\u0328' };
        output.push(text(((accent[2] ?? accent[3]) + combining[accent[1]]).normalize('NFC')));
        i += accent[0].length; continue;
      }
      if (value.startsWith('\\dh', i)) { output.push('ð'); i += 3; continue; }
      const command = /^\\([A-Za-z]+)\s*/.exec(value.slice(i));
      if (command) {
        const name = command[1];
        if (Object.hasOwn(PROSE_COMMANDS, name)) {
          output.push(PROSE_COMMANDS[name]);
          i += name.length + 1;
          if (value.startsWith('{}', i)) i += 2;
          continue;
        }
        const group = groupAt(value, i + command[0].length);
        if (group && (Object.hasOwn(TEXT_COMMANDS, name) || ['url', 'href', 'cite', 'citep', 'citet', 'footnote'].includes(name))) {
          if (name === 'href') {
            const label = groupAt(value, group.end);
            if (label) { output.push(format(label.body, plain, depth + 1)); i = label.end; continue; }
          } else if (name === 'url') output.push(wrapped('code', text(group.body)));
          else if (name.startsWith('cite')) output.push(`[${text(group.body)}]`);
          else if (name === 'footnote') output.push(` (${format(group.body, plain, depth + 1)})`);
          else output.push(wrapped(TEXT_COMMANDS[name], format(group.body, plain, depth + 1)));
          if (name !== 'href') { i = group.end; continue; }
        }
        if (MATH_SYMBOLS.has(name) || (MATH_STYLES.includes(name) && group)) {
          const end = group && MATH_STYLES.includes(name) ? group.end : i + name.length + 1;
          const source = value.slice(i, end);
          output.push(renderMath({ body: source, source, end, display: false }, plain));
          i = end; continue;
        }
        const fused = fusedCommand(name);
        if (fused && Object.hasOwn(TEXT_COMMANDS, fused.command)) {
          output.push(wrapped(TEXT_COMMANDS[fused.command], text(fused.letters)));
          i += name.length + 1; continue;
        }
      }
      // TeX escapes in prose, including escaped currency, are literal text.
      if (/[$%&#_{}]/.test(value[i + 1] ?? '')) { output.push(text(value[i + 1])); i += 2; continue; }
      if (value[i + 1] === '-') { i += 2; continue; }
    }
    if (char === '{') {
      const accentGroup = /^\{(\\["'`^~=.uvHck]\s*[A-Za-z])\}/.exec(value.slice(i));
      if (accentGroup) { output.push(format(accentGroup[1], plain, depth + 1)); i += accentGroup[0].length; continue; }
      const legacy = /^\{\\(em|it|sl|bf|tt|rm|sf|sc)\s+/.exec(value.slice(i));
      const group = legacy ? groupAt(value, i) : undefined;
      if (legacy && group) {
        output.push(wrapped(LEGACY_COMMANDS[legacy[1]], format(value.slice(i + legacy[0].length, group.end - 1), plain, depth + 1)));
        i = group.end; continue;
      }
    }
    if (char === '`' || char === '*' || char === '_') {
      const marker = value.startsWith(char + char, i) && char !== '`' ? char + char : char;
      const end = value.indexOf(marker, i + marker.length);
      const body = end >= 0 ? value.slice(i + marker.length, end) : '';
      // Word-internal underscores and multiplication are ordinary source text.
      if (body && !/^\s|\s$/.test(body) && !body.includes('\n') &&
        (char === '`' || (!/\w/.test(value[i - 1] ?? '') && !/\w/.test(value[end + marker.length] ?? '')))) {
        output.push(wrapped(char === '`' ? 'code' : marker.length === 2 ? 'strong' : 'em',
          char === '`' ? text(body) : format(body, plain, depth + 1)));
        i = end + marker.length; continue;
      }
    }
    output.push(text(char)); i++;
  }
  if (!plain) while (tags.length) output.push(`</${tags.pop()!}>`);
  return output.join('');
}

/** Safe HTML for visible scientific text. Never changes the source record. */
export function renderRichText(value: string): string {
  return format(decodeHTML(value), false);
}

/** Plain, unescaped text for textContent, search, and native option labels.
 * Callers placing this into HTML attributes must still HTML-escape it.
 */
export function plainText(value: string): string {
  return format(decodeHTML(value), true).replace(/\s+/g, ' ').trim();
}
