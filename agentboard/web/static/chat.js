/* Lightweight transcript rendering: typed parts + minimal markdown.
 * Keeps prose, headings, dividers, code and tool calls visually distinct
 * instead of flattening a turn into one plain-text blob. */
(function (global) {
  function esc(s) {
    const d = document.createElement('div');
    d.textContent = s == null ? '' : String(s);
    return d.innerHTML;
  }

  // Inline spans applied to already-escaped text.
  function inline(s) {
    return s
      .replace(/`([^`]+)`/g, '<code class="md-code">$1</code>')
      .replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>')
      .replace(/(^|[\s(])\*([^*\s][^*]*?)\*(?=[\s).,!?]|$)/g, '$1<em>$2</em>');
  }

  // Tiny, dependency-free syntax highlighter. Operates on already-escaped text
  // (entities for < > &, literal quotes). One left-to-right pass tags comments /
  // strings / numbers (each char consumed once, so a // inside a string stays a
  // string); keywords are then tagged only in the not-yet-tagged spans.
  const HL_KW = {};
  HL_KW.python = /\b(?:def|class|return|if|elif|else|for|while|import|from|as|with|try|except|finally|raise|yield|lambda|and|or|not|in|is|pass|break|continue|global|nonlocal|async|await|assert|del|print)\b/g;
  HL_KW.javascript = /\b(?:function|const|let|var|return|if|else|for|while|import|from|export|default|class|extends|new|try|catch|finally|throw|async|await|of|in|typeof|instanceof|delete|void|yield|switch|case|break|continue|this)\b/g;
  HL_KW.bash = /\b(?:if|then|else|elif|fi|for|in|do|done|while|until|case|esac|function|return|export|local|source|echo|cd|set|unset|sudo)\b/g;
  HL_KW.py = HL_KW.python;
  HL_KW.js = HL_KW.ts = HL_KW.tsx = HL_KW.jsx = HL_KW.typescript = HL_KW.javascript;
  HL_KW.sh = HL_KW.shell = HL_KW.zsh = HL_KW.console = HL_KW.bash;

  function highlightCode(s, lang) {
    const lk = (lang || '').toLowerCase();
    const hashComments = !(lk === 'js' || lk === 'ts' || lk === 'javascript' ||
                           lk === 'typescript' || lk === 'json' || lk === 'jsx' || lk === 'tsx');
    const comment = '\\/\\*[\\s\\S]*?\\*\\/|\\/\\/[^\\n]*' + (hashComments ? '|#[^\\n]*' : '');
    const re = new RegExp(
      '(?<com>' + comment + ')|(?<str>`[^`]*`|"[^"\\n]*"|\'[^\'\\n]*\')|(?<num>\\b\\d+(?:\\.\\d+)?\\b)',
      'g');
    let out = '', last = 0, m;
    while ((m = re.exec(s))) {
      out += s.slice(last, m.index);
      const g = m.groups;
      const cls = g.com ? 'com' : g.str ? 'str' : 'num';
      out += '<span class="hl-' + cls + '">' + m[0] + '</span>';
      last = re.lastIndex;
    }
    out += s.slice(last);
    const kw = HL_KW[lk];
    if (kw) {
      out = out.replace(/(<span[^>]*>[\s\S]*?<\/span>)|([^<]+)/g, (full, span, text) =>
        span ? span : text.replace(kw, k => '<span class="hl-kw">' + k + '</span>'));
    }
    return out;
  }

  // Minimal block markdown → HTML. Escapes everything first.
  function markdown(src) {
    const lines = esc(src == null ? '' : String(src)).split('\n');
    const out = [];
    let para = [];
    let inCode = false, code = [], codeLang = '';
    const flushPara = () => {
      if (para.length) { out.push('<p>' + inline(para.join('<br>')) + '</p>'); para = []; }
    };
    const emitCode = () => out.push('<pre class="md-pre"><code class="hl">'
        + highlightCode(code.join('\n'), codeLang) + '</code></pre>');
    for (const raw of lines) {
      const line = raw;
      const fence = line.match(/^\s*```([\w+#-]*)/);
      if (fence) {
        if (inCode) { emitCode(); code = []; inCode = false; }
        else { flushPara(); inCode = true; codeLang = fence[1] || ''; }
        continue;
      }
      if (inCode) { code.push(line); continue; }
      if (/^\s*([-*_])\s*\1\s*\1[\s\1]*$/.test(line.replace(/&[^;]+;/g, ''))) { flushPara(); out.push('<hr class="md-hr">'); continue; }
      const h = line.match(/^\s*(#{1,4})\s+(.*)$/);
      if (h) { flushPara(); out.push('<div class="md-h">' + inline(h[2]) + '</div>'); continue; }
      const li = line.match(/^\s*[-*]\s+(.*)$/);
      if (li) { flushPara(); out.push('<div class="md-li">• ' + inline(li[1]) + '</div>'); continue; }
      const nli = line.match(/^\s*(\d+)\.\s+(.*)$/);
      if (nli) { flushPara(); out.push('<div class="md-li">' + nli[1] + '. ' + inline(nli[2]) + '</div>'); continue; }
      if (line.trim() === '') { flushPara(); continue; }
      para.push(line);
    }
    if (inCode && code.length) emitCode();
    flushPara();
    return out.join('');
  }

  // Render one message's parts (prose / tool calls).
  function renderParts(m) {
    const parts = (m.parts && m.parts.length) ? m.parts : [{ type: 'text', text: m.text }];
    return parts.map(p => {
      if (p.type === 'tool') {
        const brief = p.brief ? ` <span class="tool-brief">${esc(p.brief)}</span>` : '';
        return `<div class="tool-chip">🛠 <span class="tool-name">${esc(p.name || 'tool')}</span>${brief}</div>`;
      }
      return `<div class="md">${markdown(p.text || '')}</div>`;
    }).join('');
  }

  // Render a whole transcript as a clean flow: consecutive same-role turns are
  // merged into one group with a single muted role label; the human's turns get
  // a subtle tinted block, the agent's read as plain prose.
  function renderTranscript(msgs) {
    if (!msgs || !msgs.length) return '';
    const groups = [];
    for (const m of msgs) {
      const last = groups[groups.length - 1];
      if (last && last.role === m.role) last.msgs.push(m);
      else groups.push({ role: m.role, msgs: [m] });
    }
    return groups.map(g => {
      const who = g.role === 'user' ? 'You' : 'Agent';
      // Each constituent message is its own block so a long agent run (dozens of
      // text/tool steps) stays spaced out and readable, not crammed together.
      const body = g.msgs.map(m => `<div class="turn-msg">${renderParts(m)}</div>`).join('');
      return `<div class="turn turn-${g.role === 'user' ? 'user' : 'agent'}">`
           + `<div class="turn-role">${who}</div><div class="turn-body">${body}</div></div>`;
    }).join('');
  }

  global.AB = global.AB || {};
  global.AB.esc = esc;
  global.AB.markdown = markdown;
  global.AB.renderParts = renderParts;
  global.AB.renderTranscript = renderTranscript;
})(window);
