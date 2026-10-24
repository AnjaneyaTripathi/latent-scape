import { describe, expect, it } from 'vitest';
import { plainText, renderRichText } from './rich-text';

describe('scientific rich text', () => {
  it('renders inline and display mathematics using accessible local KaTeX markup', () => {
    for (const source of ['$\\mathcal{O}(d^2)$', '$$x^2$$', '\\(x_1\\)', '\\[\\frac{a}{b}\\]', '\\begin{equation*}x^2\\end{equation*}']) {
      const html = renderRichText(source);
      expect(html).toContain('class="katex"');
      expect(html).toContain('<math');
      expect(html).not.toContain('<div');
      expect(html).not.toContain('rich-math-fallback');
    }
    expect(plainText('$\\mathcal{O}(d^2)$')).toBe('O(d^2)');
    expect(plainText('$\\frac{a}{b}$')).toBe('(a)/(b)');
  });

  it('preserves numeric percentage units instead of treating them as TeX comments', () => {
    for (const source of ['$39%$', '$75.1%$', '$\\textbf{+8.26%}$']) {
      expect(plainText(source)).toContain('%');
      expect(renderRichText(source)).not.toContain('rich-math-fallback');
    }
    expect(plainText('$39%$')).toBe('39%');
    expect(plainText('$75.1\\%$')).toBe('75.1%');
  });

  it('repairs fused known typography only after parsing fails and preserves custom macros', () => {
    for (const source of ['$\\textttFlashSchNet$', '$\\textttMoE$', '$\\mathcalO(d^2)$', '$\\mathbbR$', '$\\boldsymbolK$', '$\\sqrtT$', '$\\textitIChing$', '$\\textrmFlow$', '$\\mathcalVista\\mathcalDPO$', '$\\textDT^\\text2$']) {
      expect(renderRichText(source)).toContain('class="katex"');
      expect(renderRichText(source)).not.toContain('rich-math-fallback');
    }
    expect(plainText('$\\textttFlashSchNet$')).toBe('FlashSchNet');
    expect(plainText('$\\mathcalO(d^2)$')).toBe('O(d^2)');
    expect(renderRichText('$\\sqrt{T}$')).toContain('>\\sqrt{T}</annotation>');
    expect(plainText('$\\textit{foo}+\\sqrtT$')).toBe('foo+√(T)');
    expect(plainText('$\\textcolor{red}{x}+\\sqrtT$')).toBe('x+√(T)');
    expect(plainText('$\\texttt{foo}+\\sqrtT$')).toBe('foo+√(T)');
    expect(plainText('$\\textnormal{foo}+\\sqrtT$')).toBe('foo+√(T)');
    expect(renderRichText('$\\textcolor{red}{x}+\\sqrtT$')).toContain('\\textcolor{red}{x}+\\sqrt{T}</annotation>');
    for (const source of ['$\\ourmethod$', '$\\eps$', '$\\sqrtCustom$']) {
      expect(renderRichText(source)).toContain('rich-math-fallback');
      expect(renderRichText(source)).toContain('title="Original notation (could not be typeset)"');
      expect(plainText(source)).toBe(source);
    }
  });

  it('formats only recognized standalone math controls and fused text styling', () => {
    expect(plainText('n\\times n and \\epsilon in \\mathbb{R}')).toBe('n× n and ϵ in R');
    expect(renderRichText('\\emphafresh')).toBe('<em>afresh</em>');
    expect(renderRichText('\\textttMetaDistill')).toBe('<code>MetaDistill</code>');
    expect(renderRichText('\\textcolor{red}{x}')).toBe('\\textcolor{red}{x}');
    expect(renderRichText('\\unknown{custom}')).toBe('\\unknown{custom}');
    expect(renderRichText('\\mathbb')).toBe('\\mathbb');
  });

  it('allows known HTML formatting but never source attributes', () => {
    expect(renderRichText('<em class="a" onclick="evil()">fast <b>work</b></em> and x<sub>1</sub><sup>2</sup>'))
      .toBe('<em>fast <b>work</b></em> and x<sub>1</sub><sup>2</sup>');
    expect(renderRichText('&lt;em&gt;encoded&lt;/em&gt; &amp; A &quot;quote&quot; &#x27;x&#x27;'))
      .toBe('<em>encoded</em> &amp; A &quot;quote&quot; &#39;x&#39;');
    expect(plainText('A &amp; B <em>strong</em>')).toBe('A & B strong');
    expect(renderRichText('<em title="a > b">x</em>')).toBe('<em>x</em>');
  });

  it('preserves prose comparisons, unknown model tokens, currency, and escaped dollars', () => {
    for (const source of ['A < B and C > D', 'Use <projektor> or <trk>', '$400 to acquire assets worth $6.3 billion', '$50 bug fixes to $32000 implementations', '$2500 desktops and a $250M pod', 'Just Add $100 More', 'Prices $5 and $10.', '$0.031/$20', '$5, $10', '$5–$10']) {
      expect(plainText(source)).toBe(source);
      expect(renderRichText(source)).not.toContain('class="katex"');
    }
    expect(renderRichText('Cost \\$5, success 10\\%.')).toBe('Cost $5, success 10%.');
    for (const source of ['$2$ samples, $2n$ steps', '$2xy$', '$2ab$']) expect(renderRichText(source)).toContain('class="katex"');
    for (const source of ['2176$\\times$3840 and $x$', '$>$20%', '$-$50.23%', '$\\sim$50%', '$ \\mathcal{C} $ and <b>prose</b>']) {
      expect(renderRichText(source)).toContain('class="katex"');
      expect(renderRichText(source)).not.toContain('rich-math-fallback');
    }
    expect(renderRichText('$ \\mathcal{C} $ and <b>prose</b>')).toContain(' and <b>prose</b>');
  });

  it('formats nested text-mode TeX and legacy groups outside math', () => {
    expect(renderRichText('A \\emph{nested \\textbf{result}} and {\\em classic}.'))
      .toBe('A <em>nested <strong>result</strong></em> and <em>classic</em>.');
    expect(renderRichText('\\underline{safe} \\url{https://example.com/a} \\cite{a,b}'))
      .toBe('<u>safe</u> <code>https://example.com/a</code> [a,b]');
    expect(renderRichText('\\href{javascript:evil()}{\\textit{label}}')).toBe('<em>label</em>');
    expect(plainText('\\texttt{MetaDistill} and {\\bf strong}')).toBe('MetaDistill and strong');
    expect(renderRichText('\\emph{unclosed')).toBe('\\emph{unclosed');
    expect(renderRichText('\\constructor{x} \\toString{x}')).toBe('\\constructor{x} \\toString{x}');
  });

  it('formats common TeX author accents without confusing prose commands', () => {
    expect(plainText('Chlo{\\"e} Brown; Jes\\\'us; Hél\\`ene; Tomá\\v S.; Gu\\dhrún; Gia\\-cin\\-to'))
      .toBe('Chloë Brown; Jesús; Hélène; TomáŠ.; Guðrún; Giacinto');
    expect(plainText('\\underline{x} \\url{u} \\cite{c}')).toBe('x u [c]');
  });

  it('supports restrained Markdown emphasis and code without touching word underscores', () => {
    expect(renderRichText('A **bold** and *italic* `code <x>` model_name.'))
      .toBe('A <strong>bold</strong> and <em>italic</em> <code>code &lt;x&gt;</code> model_name.');
    expect(renderRichText('a*b*c and 2*3*4')).toBe('a*b*c and 2*3*4');
  });

  it('escapes unsupported and malformed math rather than emitting unsafe error text', () => {
    for (const source of ['$\\doesnotexist{<script>alert(1)</script>}$', '$\\frac{1}{$']) {
      const html = renderRichText(source);
      expect(html).toContain('rich-math-fallback');
      expect(html).not.toContain('<script>');
    }
    expect(renderRichText('Unmatched $x and \\(y')).toBe('Unmatched $x and \\(y');
  });

  it('escapes source HTML attacks including encoded and SVG payloads', () => {
    for (const source of [
      '<script>alert(1)</script>', '<img src=x onerror=alert(1)>', '<svg onload=alert(1)>x</svg>',
      '&lt;img src=x onerror=alert(1)&gt;', '<a href="javascript:alert(1)">x</a>',
      '<iframe src="https://example.com"></iframe>', '<style>body{display:none}</style>',
      '<em style="position:fixed" onmouseover="alert(1)">x</em>',
    ]) {
      const html = renderRichText(source);
      expect(html).not.toMatch(/<(?:script|img|svg|a\s|iframe|style)\b/i);
      expect(html).not.toMatch(/<[^>]+\s(?:on\w+|style|href|src)=/i);
    }
  });

  it('disallows TeX links, external assets and source-controlled HTML attributes', () => {
    for (const source of [
      '$\\href{javascript:alert(1)}{x}$', '$\\includegraphics{https://example.com/evil.png}$',
      '$\\htmlClass{evil}{x}$', '$\\htmlStyle{position:fixed}{x}$', '$\\htmlId{evil}{x}$',
    ]) {
      const html = renderRichText(source);
      expect(html).not.toMatch(/<(?:a|img)\b/);
      expect(html).not.toContain('class="evil"');
      expect(html).not.toContain('id="evil"');
      expect(html).not.toContain('style="position:fixed');
    }
  });

  it('bounds macro expansion and isolates macro definitions across expressions', () => {
    expect(renderRichText('$\\def\\loop{\\loop}\\loop$')).toContain('rich-math-fallback');
    renderRichText('$\\gdef\\privateMacro{X}\\privateMacro$');
    expect(renderRichText('$\\privateMacro$')).toContain('rich-math-fallback');
    expect(renderRichText(`$${'x'.repeat(8193)}$`)).not.toContain('class="katex"');
    expect(renderRichText('<em ' + ' '.repeat(10000) + '!')).toContain('&lt;em');
  });
});
