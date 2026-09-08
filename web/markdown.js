(function () {
  'use strict';
  function inline(text) {
    const nodes = [];
    const pattern = /(`[^`\n]+`)|(\*\*[^*\n]+\*\*)|(\*[^*\n]+\*)/g;
    let last = 0, match;
    while ((match = pattern.exec(text)) !== null) {
      if (match.index > last) nodes.push(document.createTextNode(text.slice(last, match.index)));
      const t = match[0];
      if (t.startsWith('`')) {
        const el = document.createElement('code');
        el.textContent = t.slice(1, -1);
        nodes.push(el);
      } else if (t.startsWith('**')) {
        const el = document.createElement('strong');
        el.textContent = t.slice(2, -2);
        nodes.push(el);
      } else {
        const el = document.createElement('em');
        el.textContent = t.slice(1, -1);
        nodes.push(el);
      }
      last = pattern.lastIndex;
    }
    if (last < text.length) nodes.push(document.createTextNode(text.slice(last)));
    return nodes;
  }

  function render(text) {
    const root = document.createElement('div');
    root.className = 'md';
    const lines = (text || '').split('\n');
    let i = 0, p = [];
    const flush = () => {
      if (!p.length) return;
      const el = document.createElement('p');
      inline(p.join(' ')).forEach(n => el.appendChild(n));
      root.appendChild(el);
      p = [];
    };
    while (i < lines.length) {
      const line = lines[i].trim();
      if (line.startsWith('```')) {
        flush();
        const buf = [];
        i++;
        while (i < lines.length && !lines[i].trim().startsWith('```')) buf.push(lines[i++]);
        i++;
        const pre = document.createElement('pre');
        const code = document.createElement('code');
        code.textContent = buf.join('\n');
        pre.appendChild(code);
        root.appendChild(pre);
        continue;
      }
      if (!line) { flush(); i++; continue; }
      const h = line.match(/^(#{1,6})\s+(.*)$/);
      if (h) {
        flush();
        const el = document.createElement('h' + Math.min(Math.max(h[1].length, 3), 5));
        inline(h[2]).forEach(n => el.appendChild(n));
        root.appendChild(el);
        i++; continue;
      }
      const listMatch = line.match(/^[-*+]\s+(.*)$/) || line.match(/^\d+[.)]\s+(.*)$/);
      if (listMatch) {
        flush();
        const ol = Boolean(line.match(/^\d/));
        const list = document.createElement(ol ? 'ol' : 'ul');
        while (i < lines.length) {
          const item = lines[i].trim();
          const m = (ol ? item.match(/^\d+[.)]\s+(.*)$/) : item.match(/^[-*+]\s+(.*)$/));
          if (!m) break;
          const li = document.createElement('li');
          const parts = [m[1]];
          i++;
          while (i < lines.length && lines[i].trim() && /^\s{2,}/.test(lines[i]) && !/^\s*([-*+]|\d+[.)])\s+/.test(lines[i])) {
            parts.push(lines[i].trim());
            i++;
          }
          inline(parts.join(' ')).forEach(n => li.appendChild(n));
          list.appendChild(li);
        }
        root.appendChild(list);
        continue;
      }
      p.push(line);
      i++;
    }
    flush();
    return root;
  }
  window.MD = { render: render };
})();
