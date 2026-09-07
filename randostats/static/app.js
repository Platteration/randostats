/* randostats front end: plain JS, hand-drawn SVG charts, no build step. */
(() => {
  const $ = (s, el = document) => el.querySelector(s);
  const $$ = (s, el = document) => [...el.querySelectorAll(s)];
  const NS = "http://www.w3.org/2000/svg";
  const fmt = (n) => n.toLocaleString();
  const tip = $("#tooltip");
  const state = { contacts: [], tables: new Set(), llm: false, session: null, hues: {} };

  // A contact keeps the same hue everywhere, assigned once from overall volume so
  // filtering a chart never repaints the survivors. Past eight people it's gray.
  const assignHues = (rows) => {
    state.hues = {};
    rows.forEach((r, i) => { state.hues[r.contact] = i < 8 ? `var(--c${i + 1})` : "var(--muted)"; });
  };
  const hueOf = (name) => state.hues[name] || "var(--muted)";
  const dot = (name) => `<i class="sw" style="background:${hueOf(name)}"></i>`;

  const api = async (path, opts) => {
    const r = await fetch(path, opts);
    if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || r.statusText);
    return r.json();
  };

  // ---------- tiny SVG helpers ----------
  const el = (tag, attrs = {}, text) => {
    const e = document.createElementNS(NS, tag);
    for (const [k, v] of Object.entries(attrs)) e.setAttribute(k, v);
    if (text != null) e.textContent = text;
    return e;
  };
  const showTip = (evt, html) => { tip.innerHTML = html; tip.hidden = false; moveTip(evt); };
  const moveTip = (evt) => { tip.style.left = (evt.clientX + 14) + "px"; tip.style.top = (evt.clientY + 14) + "px"; };
  const hideTip = () => { tip.hidden = true; };
  const hover = (node, html) => {
    node.addEventListener("mouseenter", (e) => showTip(e, html));
    node.addEventListener("mousemove", moveTip);
    node.addEventListener("mouseleave", hideTip);
  };
  const niceMax = (v) => { if (v <= 0) return 1; const p = Math.pow(10, Math.floor(Math.log10(v))); const m = v / p; const n = [1, 1.2, 1.5, 2, 2.5, 3, 4, 5, 6, 8, 10].find(c => m <= c); return n * p; };
  const roundedTop = (x, y, w, h, r) => { r = Math.min(r, w / 2, h); return `M${x},${y + h} V${y + r} Q${x},${y} ${x + r},${y} H${x + w - r} Q${x + w},${y} ${x + w},${y + r} V${y + h} Z`; };
  const roundedRight = (x, y, w, h, r) => { r = Math.min(r, h / 2, w); return `M${x},${y} H${x + w - r} Q${x + w},${y} ${x + w},${y + r} V${y + h - r} Q${x + w},${y + h} ${x + w - r},${y + h} H${x} Z`; };

  // Horizontal stacked bars (two series) with a direct total label at the tip.
  const clickable = (node, fn, row) => { if (!fn) return; node.classList.add("clickable"); node.addEventListener("click", () => fn(row)); };

  function hbars(container, rows, { key, keyLabel, series, labels, colors = ["s1", "s2"], tipFn, labelW = 150, labelSize, onClick }) {
    container.innerHTML = "";
    if (!rows.length) { container.innerHTML = '<div class="empty">Nothing to plot. Import an export on the Import tab.</div>'; return; }
    const right = 70, rowH = 30, barH = 22, top = 8, maxChars = Math.floor(labelW / 7);
    const width = Math.max(600, container.clientWidth || 600), plotW = width - labelW - right;
    const max = niceMax(Math.max(...rows.map(r => series.reduce((a, s) => a + r[s], 0))));
    const svg = el("svg", { viewBox: `0 0 ${width} ${top + rows.length * rowH + 24}`, width, role: "img" });
    const grid = el("g", { class: "grid" });
    for (let i = 0; i <= 4; i++) {
      const x = labelW + plotW * i / 4;
      grid.appendChild(el("line", { x1: x, x2: x, y1: top, y2: top + rows.length * rowH }));
      svg.appendChild(el("text", { x, y: top + rows.length * rowH + 16, "text-anchor": "middle", class: "tick" }, fmt(max * i / 4)));
    }
    svg.appendChild(grid);
    rows.forEach((r, i) => {
      const y = top + i * rowH + (rowH - barH) / 2;
      const g = el("g", { class: "slot" });
      g.appendChild(el("text", { x: labelW - 10, y: y + barH / 2 + 5, "text-anchor": "end", ...(labelSize ? { style: `font-size:${labelSize}px` } : {}) },
        r[key].length > maxChars ? r[key].slice(0, maxChars - 1) + "…" : r[key]));
      let x = labelW;
      const total = series.reduce((a, s) => a + r[s], 0);
      series.forEach((s, j) => {
        const w = plotW * r[s] / max;
        if (w <= 0) return;
        const last = j === series.length - 1 || series.slice(j + 1).every(t => r[t] === 0);
        const gap = j > 0 ? 2 : 0;
        const d = last ? roundedRight(x + gap, y, Math.max(0, w - gap), barH, 4) : `M${x + gap},${y} H${x + w} V${y + barH} H${x + gap} Z`;
        g.appendChild(el("path", { d, class: `bar gx ${colors[j]}`, style: `animation-delay:${i * 18}ms` }));
        x += w;
      });
      g.appendChild(el("text", { x: x + 6, y: y + barH / 2 + 4, class: "val" }, fmt(total)));
      const hit = el("rect", { x: 0, y: top + i * rowH, width, height: rowH, class: "hit" });
      hover(hit, tipFn ? tipFn(r) : `<b>${esc(r[key])}</b><br>${series.map((s, j) => `${labels[j]}: ${fmt(r[s])}`).join("<br>")}`);
      clickable(hit, onClick, r);
      g.appendChild(hit);
      svg.appendChild(g);
    });
    container.appendChild(svg);
    container._table = () => table(series.length > 1 ? [key, ...series, "total"] : [key, ...series], rows.map(r => ({ ...r, total: series.reduce((a, s) => a + r[s], 0) })), [keyLabel || key, ...labels, "Total"]);
  }

  // Vertical stacked columns (two series) on a categorical x axis.
  function columns(container, rows, { key, series, labels, colors = ["s1", "s2"], xLabel, onClick }) {
    container.innerHTML = "";
    if (!rows.length || !rows.some(r => series.some(s => r[s] > 0))) { container.innerHTML = '<div class="empty">No messages in this slice.</div>'; return; }
    const width = Math.max(420, container.clientWidth || 420), height = 220, left = 44, bottom = 28, top = 10, right = 8;
    const plotW = width - left - right, plotH = height - top - bottom;
    const max = niceMax(Math.max(...rows.map(r => series.reduce((a, s) => a + r[s], 0))));
    const slot = plotW / rows.length, barW = Math.min(24, slot * 0.7);
    const svg = el("svg", { viewBox: `0 0 ${width} ${height}`, width, role: "img" });
    const grid = el("g", { class: "grid" });
    for (let i = 0; i <= 4; i++) {
      const y = top + plotH - plotH * i / 4;
      grid.appendChild(el("line", { x1: left, x2: left + plotW, y1: y, y2: y }));
      svg.appendChild(el("text", { x: left - 6, y: y + 4, "text-anchor": "end", class: "tick" }, fmt(max * i / 4)));
    }
    svg.appendChild(grid);
    svg.appendChild(el("g", { class: "axis" })).appendChild(el("line", { x1: left, x2: left + plotW, y1: top + plotH, y2: top + plotH }));
    const every = Math.ceil(rows.length / 12);
    rows.forEach((r, i) => {
      const x0 = left + i * slot + (slot - barW) / 2;
      const g = el("g", { class: "slot" });
      let y = top + plotH;
      series.forEach((s, j) => {
        const h = plotH * r[s] / max;
        if (h <= 0) return;
        const last = series.slice(j + 1).every(t => r[t] === 0);
        const gap = j > 0 ? 2 : 0;
        const d = last ? roundedTop(x0, y - h, barW, Math.max(0, h - gap), 4) : `M${x0},${y - h} H${x0 + barW} V${y - gap} H${x0} Z`;
        g.appendChild(el("path", { d, class: `bar gy ${colors[j]}`, style: `animation-delay:${i * 14}ms` }));
        y -= h;
      });
      if (i % every === 0) g.appendChild(el("text", { x: x0 + barW / 2, y: height - 8, "text-anchor": "middle", class: "tick" }, xLabel ? xLabel(r) : r[key]));
      const hit = el("rect", { x: left + i * slot, y: top, width: slot, height: plotH, class: "hit" });
      hover(hit, `<b>${esc(xLabel ? xLabel(r) : r[key])}</b><br>${series.map((s, j) => `${labels[j]}: ${fmt(r[s])}`).join("<br>")}`);
      clickable(hit, onClick, r);
      g.appendChild(hit);
      svg.appendChild(g);
    });
    container.appendChild(svg);
    container._table = () => table([key, ...series], rows, [key[0].toUpperCase() + key.slice(1), ...labels]);
  }

  // Single-series line with a wash and a crosshair tooltip.
  function line(container, rows, { key, value, label, onClick }) {
    container.innerHTML = "";
    if (rows.length < 2) { container.innerHTML = '<div class="empty">One month of messages is a dot, not a line.</div>'; return; }
    const width = Math.max(600, container.clientWidth || 600), height = 220, left = 48, bottom = 28, top = 12, right = 16;
    const plotW = width - left - right, plotH = height - top - bottom;
    const max = niceMax(Math.max(...rows.map(r => r[value])));
    const X = (i) => left + plotW * i / (rows.length - 1), Y = (v) => top + plotH - plotH * v / max;
    const svg = el("svg", { viewBox: `0 0 ${width} ${height}`, width, role: "img" });
    const grid = el("g", { class: "grid" });
    for (let i = 0; i <= 4; i++) {
      const y = top + plotH - plotH * i / 4;
      grid.appendChild(el("line", { x1: left, x2: left + plotW, y1: y, y2: y }));
      svg.appendChild(el("text", { x: left - 6, y: y + 4, "text-anchor": "end", class: "tick" }, fmt(max * i / 4)));
    }
    svg.appendChild(grid);
    const pts = rows.map((r, i) => `${X(i)},${Y(r[value])}`);
    svg.appendChild(el("path", { class: "area", d: `M${X(0)},${top + plotH} L${pts.join(" L")} L${X(rows.length - 1)},${top + plotH} Z` }));
    svg.appendChild(el("path", { class: "line", d: `M${pts.join(" L")}` }));
    const every = Math.ceil(rows.length / 10);
    rows.forEach((r, i) => { const last = i === rows.length - 1; if ((i % every === 0 && (!every || i < rows.length - every / 2)) || last) svg.appendChild(el("text", { x: X(i), y: height - 8, "text-anchor": last ? "end" : i === 0 ? "start" : "middle", class: "tick" }, r[key])); });
    const lastI = rows.length - 1;
    svg.appendChild(el("circle", { cx: X(lastI), cy: Y(rows[lastI][value]), r: 4, class: "dot" }));
    svg.appendChild(el("text", { x: X(lastI) - 8, y: Y(rows[lastI][value]) - 8, "text-anchor": "end", class: "val" }, fmt(rows[lastI][value])));
    const cross = el("line", { class: "cross", y1: top, y2: top + plotH, x1: -10, x2: -10 });
    const dot = el("circle", { r: 4, class: "dot", cx: -10, cy: -10 });
    svg.appendChild(cross); svg.appendChild(dot);
    const hit = el("rect", { x: left, y: top, width: plotW, height: plotH, class: "hit" });
    hit.addEventListener("mousemove", (e) => {
      const rect = svg.getBoundingClientRect();
      const px = (e.clientX - rect.left) * width / rect.width;
      const i = Math.max(0, Math.min(lastI, Math.round((px - left) / plotW * lastI)));
      cross.setAttribute("x1", X(i)); cross.setAttribute("x2", X(i)); dot.setAttribute("cx", X(i)); dot.setAttribute("cy", Y(rows[i][value]));
      showTip(e, `<b>${esc(rows[i][key])}</b><br>${label}: ${fmt(rows[i][value])}`);
    });
    hit.addEventListener("mouseleave", () => { hideTip(); cross.setAttribute("x1", -10); cross.setAttribute("x2", -10); dot.setAttribute("cx", -10); });
    if (onClick) {
      hit.classList.add("clickable");
      hit.addEventListener("click", (e) => {
        const rect = svg.getBoundingClientRect();
        const px = (e.clientX - rect.left) * width / rect.width;
        onClick(rows[Math.max(0, Math.min(lastI, Math.round((px - left) / plotW * lastI)))]);
      });
    }
    svg.appendChild(hit);
    container.appendChild(svg);
    container._table = () => table([key, value], rows, ["Month", label]);
  }

  // 7 x 24 heatmap on the sequential blue ramp.
  function heatmap(container, grid, onClick) {
    container.innerHTML = "";
    const max = Math.max(1, ...grid.flat());
    if (max <= 1 && !grid.flat().some(v => v > 0)) { container.innerHTML = '<div class="empty">No messages in this slice.</div>'; return; }
    const days = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];
    const cell = 30, gap = 2, left = 40, top = 22, width = left + 24 * (cell + gap), height = top + 7 * (cell + gap);
    const steps = ["--seq-100", "--seq-200", "--seq-300", "--seq-400", "--seq-500", "--seq-600", "--seq-700"];
    const svg = el("svg", { viewBox: `0 0 ${width} ${height}`, width, role: "img" });
    for (let h = 0; h < 24; h += 3) svg.appendChild(el("text", { x: left + h * (cell + gap) + cell / 2, y: 14, "text-anchor": "middle", class: "tick" }, `${h}h`));
    grid.forEach((row, d) => {
      svg.appendChild(el("text", { x: left - 8, y: top + d * (cell + gap) + cell / 2 + 4, "text-anchor": "end", class: "tick" }, days[d]));
      row.forEach((v, h) => {
        const idx = v === 0 ? -1 : Math.min(steps.length - 1, Math.floor(Math.sqrt(v / max) * steps.length));
        const rect = el("rect", { x: left + h * (cell + gap), y: top + d * (cell + gap), width: cell, height: cell, rx: 3, fill: idx < 0 ? "var(--grid)" : `var(${steps[idx]})` });
        hover(rect, `<b>${esc(days[d])} ${h}:00–${h + 1}:00</b><br>${fmt(v)} messages`);
        if (v) clickable(rect, onClick, { weekday: d, hour: h, count: v, label: `${days[d]} ${h}:00` });
        svg.appendChild(rect);
      });
    });
    container.appendChild(svg);
  }

  // Two values per row (you vs them) joined by a connector. Never stack them:
  // medians don't add up, so a stacked bar would state something untrue.
  function dumbbell(container, rows, { key, a, b, labels, unit = "" }) {
    container.innerHTML = "";
    const usable = rows.filter(r => r[a] != null || r[b] != null);
    if (!usable.length) { container.innerHTML = '<div class="empty">Not enough back-and-forth to time a reply.</div>'; return; }
    const labelW = 150, right = 40, rowH = 30, top = 10, maxChars = Math.floor(labelW / 7);
    const width = Math.max(600, container.clientWidth || 600), plotW = width - labelW - right;
    const max = niceMax(Math.max(...usable.flatMap(r => [r[a] || 0, r[b] || 0])));
    const X = (v) => labelW + plotW * v / max;
    const svg = el("svg", { viewBox: `0 0 ${width} ${top + usable.length * rowH + 24}`, width, role: "img" });
    const grid = el("g", { class: "grid" });
    for (let i = 0; i <= 4; i++) {
      const x = labelW + plotW * i / 4;
      grid.appendChild(el("line", { x1: x, x2: x, y1: top, y2: top + usable.length * rowH }));
      svg.appendChild(el("text", { x, y: top + usable.length * rowH + 16, "text-anchor": "middle", class: "tick" }, fmt(Math.round(max * i / 4)) + unit));
    }
    svg.appendChild(grid);
    usable.forEach((r, i) => {
      const y = top + i * rowH + rowH / 2;
      const g = el("g", { class: "slot" });
      g.appendChild(el("text", { x: labelW - 10, y: y + 4, "text-anchor": "end" }, r[key].length > maxChars ? r[key].slice(0, maxChars - 1) + "…" : r[key]));
      if (r[a] != null && r[b] != null) g.appendChild(el("line", { x1: X(r[a]), x2: X(r[b]), y1: y, y2: y, stroke: "var(--axis)", "stroke-width": 2 }));
      if (r[a] != null) g.appendChild(el("circle", { cx: X(r[a]), cy: y, r: 5, fill: "var(--s1)", stroke: "var(--surface-1)", "stroke-width": 2 }));
      if (r[b] != null) g.appendChild(el("circle", { cx: X(r[b]), cy: y, r: 5, fill: "var(--s2)", stroke: "var(--surface-1)", "stroke-width": 2 }));
      const hit = el("rect", { x: 0, y: top + i * rowH, width, height: rowH, class: "hit" });
      hover(hit, `<b>${esc(r[key])}</b><br>${labels[0]}: ${r[a] == null ? "n/a" : fmt(r[a]) + unit}<br>${labels[1]}: ${r[b] == null ? "n/a" : fmt(r[b]) + unit}`);
      g.appendChild(hit);
      svg.appendChild(g);
    });
    container.appendChild(svg);
    container._table = () => table([key, a, b], usable, ["Person", ...labels]);
  }

  // Values with a sign. Two hues that read as opposite, a neutral zero line,
  // one shared scale either side so the arms stay comparable.
  function diverging(container, rows, { key, value, xLabel, horizontal = false, onClick, unit = "" }) {
    container.innerHTML = "";
    const usable = rows.filter(r => r[value] != null);
    if (!usable.length) { container.innerHTML = '<div class="empty">No tone words in this slice.</div>'; return; }
    const max = niceMax(Math.max(...usable.map(r => Math.abs(r[value])), 0.2));
    const pos = "var(--c1)", neg = "var(--c8)";
    const svg = el("svg", { role: "img" });
    if (horizontal) {
      const labelW = 150, right = 50, rowH = 28, barH = 18, top = 8, maxChars = Math.floor(labelW / 7);
      const width = Math.max(560, container.clientWidth || 560), plotW = width - labelW - right, mid = labelW + plotW / 2;
      svg.setAttribute("viewBox", `0 0 ${width} ${top + usable.length * rowH + 22}`); svg.setAttribute("width", width);
      const X = (v) => mid + (plotW / 2) * v / max;
      [-max, -max / 2, 0, max / 2, max].forEach(v => {
        svg.appendChild(el("line", { x1: X(v), x2: X(v), y1: top, y2: top + usable.length * rowH, stroke: v === 0 ? "var(--axis)" : "var(--grid)", "shape-rendering": "crispEdges" }));
        svg.appendChild(el("text", { x: X(v), y: top + usable.length * rowH + 16, "text-anchor": "middle", class: "tick" }, (v > 0 ? "+" : "") + v.toFixed(1)));
      });
      usable.forEach((r, i) => {
        const v = r[value], y = top + i * rowH + (rowH - barH) / 2, g = el("g", { class: "slot" });
        g.appendChild(el("text", { x: labelW - 10, y: y + barH / 2 + 4, "text-anchor": "end" }, r[key].length > maxChars ? r[key].slice(0, maxChars - 1) + "…" : r[key]));
        const x = v >= 0 ? mid : X(v), w = Math.abs(X(v) - mid);
        g.appendChild(el("path", { d: v >= 0 ? roundedRight(x, y, w, barH, 4) : `M${x + 4},${y} h${w - 4} v${barH} h${-(w - 4)} a4,4 0 0 1 -4,-4 v${-(barH - 8)} a4,4 0 0 1 4,-4 z`,
          class: "bar", fill: v >= 0 ? pos : neg, style: `animation:none` }));
        g.appendChild(el("text", { x: v >= 0 ? X(v) + 6 : X(v) - 6, y: y + barH / 2 + 4, "text-anchor": v >= 0 ? "start" : "end", class: "val" }, (v > 0 ? "+" : "") + v.toFixed(2)));
        const hit = el("rect", { x: 0, y: top + i * rowH, width, height: rowH, class: "hit" });
        hover(hit, `<b>${esc(r[key])}</b><br>Net tone: ${(v > 0 ? "+" : "") + v.toFixed(2)}${r.positive != null ? `<br>${fmt(r.positive)} warm · ${fmt(r.negative)} cold words` : ""}`);
        clickable(hit, onClick, r);
        g.appendChild(hit); svg.appendChild(g);
      });
    } else {
      const width = Math.max(600, container.clientWidth || 600), height = 220, left = 44, top = 12, bottom = 26, right = 8;
      const plotW = width - left - right, plotH = height - top - bottom, mid = top + plotH / 2;
      svg.setAttribute("viewBox", `0 0 ${width} ${height}`); svg.setAttribute("width", width);
      const Y = (v) => mid - (plotH / 2) * v / max;
      [max, max / 2, 0, -max / 2, -max].forEach(v => {
        svg.appendChild(el("line", { x1: left, x2: left + plotW, y1: Y(v), y2: Y(v), stroke: v === 0 ? "var(--axis)" : "var(--grid)", "shape-rendering": "crispEdges" }));
        svg.appendChild(el("text", { x: left - 6, y: Y(v) + 4, "text-anchor": "end", class: "tick" }, (v > 0 ? "+" : "") + v.toFixed(1)));
      });
      const slot = plotW / usable.length, barW = Math.min(24, slot * 0.7), every = Math.ceil(usable.length / 10);
      usable.forEach((r, i) => {
        const v = r[value], x0 = left + i * slot + (slot - barW) / 2, g = el("g", { class: "slot" });
        const h = Math.abs(Y(v) - mid);
        g.appendChild(el("path", { d: v >= 0 ? roundedTop(x0, Y(v), barW, h, 4) : `M${x0},${mid} h${barW} v${h - 4} a4,4 0 0 1 -4,4 h${-(barW - 8)} a4,4 0 0 1 -4,-4 z`,
          class: "bar gy", fill: v >= 0 ? pos : neg, style: v < 0 ? "transform-origin: center top" : "" }));
        if (i % every === 0 || i === usable.length - 1) g.appendChild(el("text", { x: x0 + barW / 2, y: height - 8, "text-anchor": "middle", class: "tick" }, xLabel ? xLabel(r) : r[key]));
        const hit = el("rect", { x: left + i * slot, y: top, width: slot, height: plotH, class: "hit" });
        hover(hit, `<b>${esc(xLabel ? xLabel(r) : r[key])}</b><br>Net tone: ${(v > 0 ? "+" : "") + v.toFixed(2)}<br>${fmt(r.positive)} warm · ${fmt(r.negative)} cold words`);
        clickable(hit, onClick, r);
        g.appendChild(hit); svg.appendChild(g);
      });
    }
    container.appendChild(svg);
    container._table = () => table([key, "positive", "negative", value], usable, [key[0].toUpperCase() + key.slice(1), "Warm words", "Cold words", "Net tone"]);
  }

  function sparkline(values, stroke = "var(--s1)", w = 96, h = 20) {
    const max = Math.max(1, ...values);
    const pts = values.map((v, i) => `${(i / (values.length - 1)) * w},${h - (v / max) * (h - 2) - 1}`);
    return `<svg class="spark" width="${w}" height="${h}" viewBox="0 0 ${w} ${h}"><path d="M${pts.join(" L")}" fill="none" stroke="${stroke}" stroke-width="2" stroke-linejoin="round"/></svg>`;
  }

  function table(cols, rows, labels) {
    const head = cols.map((c, i) => `<th class="${typeof rows[0]?.[c] === "number" ? "num" : ""}">${labels[i] || c}</th>`).join("");
    const body = rows.map(r => `<tr>${cols.map(c => `<td class="${typeof r[c] === "number" ? "num" : ""}">${typeof r[c] === "number" ? fmt(r[c]) : esc(r[c])}</td>`).join("")}</tr>`).join("");
    const t = document.createElement("table"); t.className = "data"; t.innerHTML = `<thead><tr>${head}</tr></thead><tbody>${body}</tbody>`; return t;
  }
  // Values reaching a tile can come from an imported file, so they are escaped here.
  const kpi = (label, value, sub = "") => `<div class="kpi"><div class="label">${esc(label)}</div><div class="value">${esc(value)}</div><div class="sub">${esc(sub)}</div></div>`;

  // Table / chart toggle buttons.
  document.addEventListener("click", (e) => {
    const btn = e.target.closest("[data-toggle-table]");
    if (!btn) return;
    const id = btn.dataset.toggleTable, c = $("#" + id);
    if (state.tables.has(id)) { state.tables.delete(id); btn.textContent = "Table"; render[id.split("-")[0]]?.(); }
    else if (c._table) { state.tables.add(id); btn.textContent = "Chart"; c.innerHTML = ""; c.appendChild(c._table()); }
  });
  const chartOrTable = (id, draw) => { const c = $("#" + id); draw(c); if (state.tables.has(id) && c._table) { c.innerHTML = ""; c.appendChild(c._table()); } };

  // ---------- drill-down drawer ----------
  const WD = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];
  const drawerState = { title: "", params: {}, offset: 0, total: 0 };

  const highlight = (text, word) => {
    const safe = esc(text);
    if (!word) return safe;
    return safe.replace(new RegExp(`(?<![A-Za-z'])(${word.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")})(?![A-Za-z'])`, "gi"), "<mark>$1</mark>");
  };

  async function loadDrawer(append = false) {
    const qs = new URLSearchParams({ ...drawerState.params, limit: 50, offset: drawerState.offset });
    const q = $("#drawer-q").value.trim();
    if (q) qs.set("q", q);
    const data = await api(`/api/messages?${qs}`);
    drawerState.total = data.total;
    const html = data.messages.map(m => `<div class="msg ${m.direction}">
      <div class="meta">${m.direction === "sent" ? "You" : esc(m.sender)}${drawerState.params.contact ? "" : " · " + esc(m.contact)} · ${m.timestamp.slice(0, 16)}</div>
      <div class="body">${highlight(m.text, drawerState.params.word)}</div></div>`).join("");
    const list = $("#drawer-list");
    if (append) list.insertAdjacentHTML("beforeend", html); else { list.innerHTML = html || '<div class="empty">No messages match.</div>'; list.scrollTop = 0; }
    $("#drawer-sub").textContent = `${fmt(data.total)} message${data.total === 1 ? "" : "s"}`;
    $("#drawer-more").hidden = drawerState.offset + data.messages.length >= data.total;
  }

  async function openDrawer(title, params) {
    drawerState.title = title; drawerState.params = params; drawerState.offset = 0;
    $("#drawer-title").textContent = title;
    $("#drawer-q").value = "";
    $("#drawer").hidden = false; $("#scrim").hidden = false;
    await loadDrawer();
    $("#drawer-q").focus();
  }
  const closeDrawer = () => { $("#drawer").hidden = true; $("#scrim").hidden = true; };
  $("#drawer-close").addEventListener("click", closeDrawer);
  $("#scrim").addEventListener("click", closeDrawer);
  document.addEventListener("keydown", (e) => { if (e.key === "Escape") closeDrawer(); });
  $("#drawer-more").addEventListener("click", async () => { drawerState.offset += 50; await loadDrawer(true); });
  let qTimer;
  $("#drawer-q").addEventListener("input", () => { clearTimeout(qTimer); qTimer = setTimeout(() => { drawerState.offset = 0; loadDrawer(); }, 250); });

  // ---------- renderers ----------
  const render = {};
  render.people = async () => {
    const [ov, rows] = await Promise.all([api("/api/stats/overview"), api("/api/stats/contacts")]);
    const lim = +$("#people-limit").value;
    $("#kpis").innerHTML = ov.total ? [
      kpi("Messages", fmt(ov.total), `${fmt(ov.sent)} sent · ${fmt(ov.received)} received`),
      kpi("People", fmt(ov.contacts)),
      kpi("Per day", ov.per_day, `over ${fmt(ov.days)} days`),
      kpi("You wrote", `${Math.round(100 * ov.sent / ov.total)}%`, "of all messages"),
      kpi("Range", `${ov.first.slice(0, 10)}`, `to ${ov.last.slice(0, 10)}`),
    ].join("") : kpi("Messages", "0", "import something to get started");
    const shown = lim ? rows.slice(0, lim) : rows;
    chartOrTable("people-chart", (c) => hbars(c, shown, {
      key: "contact", keyLabel: "Person", series: ["sent", "received"], labels: ["Sent by you", "Received"],
      tipFn: (r) => `<b>${esc(r.contact)}</b>${r.is_group ? " (group)" : ""}<br>Sent by you: ${fmt(r.sent)}<br>Received: ${fmt(r.received)}<br>${r.per_day}/day · you wrote ${Math.round(100 * r.sent_share)}%<br><i>click to read them</i>`,
      onClick: (r) => openDrawer(r.contact, { contact: r.contact }),
    }));
    const wordy = [...shown].sort((a, b) => b.avg_words_received - a.avg_words_received).slice(0, 12);
    hbars($("#people-words"), wordy, { key: "contact", series: ["avg_words_received"], labels: ["Average words per message"], colors: ["s2"],
      tipFn: (r) => `<b>${esc(r.contact)}</b><br>Their average: ${r.avg_words_received} words<br>Your average to them: ${r.avg_words_sent} words` });
  };

  render.hour = render.weekday = render.month = render.timing = async () => {
    const contact = $("#timing-contact").value;
    const t = await api("/api/stats/timing" + (contact ? `?contact=${encodeURIComponent(contact)}` : ""));
    const lat = t.reply_latency;
    $("#timing-summary").textContent = t.by_hour.length ? `Peak: ${t.peak_weekday} around ${t.peak_hour}:00 · busiest day ${t.busiest_day.date} (${t.busiest_day.count})` +
      (lat && lat.you_median_minutes != null ? ` · you reply in ~${lat.you_median_minutes} min, they take ~${lat.them_median_minutes ?? "?"} min` : "") : "";
    const scope = contact ? { contact } : {};
    chartOrTable("hour-chart", (c) => columns(c, t.by_hour, { key: "hour", series: ["sent", "received"], labels: ["Sent by you", "Received"], xLabel: (r) => `${r.hour}h`,
      onClick: (r) => openDrawer(`${contact || "Everyone"} · ${r.hour}:00–${r.hour + 1}:00`, { ...scope, hour: r.hour }) }));
    chartOrTable("weekday-chart", (c) => columns(c, t.by_weekday, { key: "weekday", series: ["sent", "received"], labels: ["Sent by you", "Received"],
      onClick: (r) => openDrawer(`${contact || "Everyone"} · ${r.weekday}`, { ...scope, weekday: WD.indexOf(r.weekday) }) }));
    heatmap($("#heatmap"), t.heatmap.length ? t.heatmap : Array.from({ length: 7 }, () => Array(24).fill(0)),
      (cell) => openDrawer(`${contact || "Everyone"} · ${cell.label}`, { ...scope, weekday: cell.weekday, hour: cell.hour }));
    chartOrTable("month-chart", (c) => line(c, t.by_month, { key: "month", value: "count", label: "Messages",
      onClick: (r) => openDrawer(`${contact || "Everyone"} · ${r.month}`, { ...scope, month: r.month }) }));
    const peaks = await api("/api/stats/timing/contacts?limit=15");
    $("#peaks").innerHTML = peaks.length ? `<table class="data"><thead><tr><th>Person</th><th class="num">Messages</th><th>Peak day</th><th>Peak hour</th><th>Hours 0–23</th></tr></thead><tbody>` +
      peaks.map(p => `<tr><td>${dot(p.contact)}${esc(p.contact)}</td><td class="num">${fmt(p.total)}</td><td>${p.peak_weekday}</td><td>${p.peak_hour}:00</td><td>${sparkline(p.by_hour, hueOf(p.contact))}</td></tr>`).join("") + "</tbody></table>" : '<div class="empty">No data.</div>';
  };

  const mins = (v) => v == null ? "n/a" : v < 60 ? `${Math.round(v)} min` : v < 1440 ? `${(v / 60).toFixed(1)} h` : `${(v / 1440).toFixed(1)} d`;
  const pct = (v) => v == null ? "n/a" : Math.round(v * 100) + "%";

  render.open = render.close = render.reply = render.convo = async () => {
    const gap = $("#convo-gap").value;
    const { summary: sum, rows } = await api(`/api/stats/conversations?gap_hours=${gap}`);
    $("#convo-kpis").innerHTML = rows.length ? [
      kpi("Conversations", fmt(sum.conversations), `${mins(sum.you_reply_median)} median reply from you`),
      kpi("You start", pct(sum.you_opened_share), "of conversations"),
      kpi("You get the last word", pct(sum.you_closed_share), "which mostly means they stopped replying"),
      kpi("They reply in", mins(sum.them_reply_median), "median, when they do"),
      kpi("Your double texts", fmt(sum.double_texts), sum.ghosted_by ? `${sum.ghosted_by.contact} leaves you hanging most` : ""),
    ].join("") : kpi("Conversations", "0", "import something first");
    const top = rows.slice(0, 15);
    chartOrTable("open-chart", (c) => hbars(c, top, { key: "contact", keyLabel: "Person", series: ["you_opened", "they_opened"], labels: ["You opened", "They opened"],
      tipFn: (r) => `<b>${esc(r.contact)}</b><br>You opened: ${fmt(r.you_opened)} (${pct(r.you_opened_share)})<br>They opened: ${fmt(r.they_opened)}<br>${r.avg_conversation} messages per conversation` }));
    chartOrTable("close-chart", (c) => hbars(c, top, { key: "contact", keyLabel: "Person", series: ["you_closed", "they_closed"], labels: ["You did", "They did"],
      tipFn: (r) => `<b>${esc(r.contact)}</b><br>You had the last word: ${fmt(r.you_closed)} (${pct(r.you_closed_share)})<br>They did: ${fmt(r.they_closed)}` }));
    chartOrTable("reply-chart", (c) => dumbbell(c, top, { key: "contact", a: "you_reply_median", b: "them_reply_median", labels: ["You answer them", "They answer you"], unit: " min" }));
    $("#convo-table").innerHTML = rows.length ? `<table class="data"><thead><tr><th>Person</th><th class="num">Conversations</th><th class="num">Your double texts</th><th class="num">Theirs</th><th class="num">Avg length</th><th class="num">Longest silence</th></tr></thead><tbody>` +
      rows.map(r => `<tr><td>${dot(r.contact)}${esc(r.contact)}</td><td class="num">${fmt(r.conversations)}</td><td class="num">${fmt(r.your_double_texts)}</td><td class="num">${fmt(r.their_double_texts)}</td><td class="num">${r.avg_conversation}</td><td class="num">${r.longest_silence_days} d</td></tr>`).join("") + "</tbody></table>"
      : '<div class="empty">Nothing to measure yet.</div>';
    await renderMembers();
  };

  async function renderMembers() {
    const groups = state.contacts.filter(c => c.is_group);
    const card = $("#members-card"), sel = $("#members-contact");
    card.hidden = !groups.length;
    if (!groups.length) return;
    if (sel.options.length !== groups.length) {
      sel.innerHTML = groups.map(g => `<option value="${esc(g.contact)}">${esc(g.contact)} (${fmt(g.total)})</option>`).join("");
    }
    const rows = await api(`/api/stats/members?contact=${encodeURIComponent(sel.value || groups[0].contact)}`);
    $("#members-table").innerHTML = `<table class="data"><thead><tr><th>Member</th><th class="num">Messages</th><th class="num">Share</th><th class="num">Avg words</th><th class="num">Peak hour</th></tr></thead><tbody>` +
      rows.map(r => `<tr><td>${esc(r.sender)}${r.is_you ? " (you)" : ""}</td><td class="num">${fmt(r.count)}</td><td class="num">${pct(r.share)}</td><td class="num">${r.avg_words}</td><td class="num">${r.peak_hour}:00</td></tr>`).join("") + "</tbody></table>";
  }

  render.spell = render.spelling = async () => {
    const dir = $("#spell-direction").value, contact = $("#spell-contact").value;
    const s = await api(`/api/stats/misspellings?direction=${dir}&limit=30` + (contact ? `&contact=${encodeURIComponent(contact)}` : ""));
    $("#spell-kpis").innerHTML = [
      kpi("Words checked", fmt(s.words_checked)),
      kpi("Misspellings", fmt(s.misspelled_total), `${s.unique} distinct words`),
      kpi("Per 1,000 words", s.rate_per_1000),
    ].join("");
    chartOrTable("spell-chart", (c) => hbars(c, s.words.map(w => ({ ...w, label: w.suggestion ? `${w.word} → ${w.suggestion}` : w.word })), {
      key: "label", keyLabel: "Word", labelW: 230, series: ["count"], labels: ["Times"],
      tipFn: (w) => `<b>${esc(w.word)}</b>${w.suggestion ? ` → ${esc(w.suggestion)}` : ""}<br>${fmt(w.count)} times<br><i>${esc(w.example)}</i>`,
      onClick: (w) => openDrawer(`“${w.word}”`, { word: w.word, direction: dir, ...(contact ? { contact } : {}) }),
    }));
    const rows = dir === "sent" ? s.by_contact : s.by_sender, k = dir === "sent" ? "contact" : "sender";
    $("#spell-by-title").textContent = dir === "sent" ? "Who you misspell things to" : "Who misspells the most";
    $("#spell-by").innerHTML = rows.length ? `<table class="data"><thead><tr><th>Person</th><th class="num">Misspellings</th><th>Favourites</th></tr></thead><tbody>` +
      rows.map(r => `<tr><td>${dot(r[k])}${esc(r[k])}</td><td class="num">${fmt(r.misspelled)}</td><td>${esc(r.top.join(", "))}</td></tr>`).join("") + "</tbody></table>" : '<div class="empty">Nothing misspelled. Suspicious.</div>';
  };

  render.words = render.emoji = render.tone = async () => {
    const dir = $("#words-direction").value;
    const scope = dir ? { direction: dir } : {};
    const [rows, em, tn] = await Promise.all([
      api(`/api/stats/words?limit=30` + (dir ? `&direction=${dir}` : "")),
      api("/api/stats/emoji?limit=24"),
      api("/api/stats/tone"),
    ]);
    chartOrTable("words-chart", (c) => hbars(c, rows, { key: "word", keyLabel: "Word", series: ["count"], labels: ["Times"],
      onClick: (w) => openDrawer(`“${w.word}”`, { word: w.word, ...scope }) }));

    $("#emoji-kpis").innerHTML = em.total ? [
      kpi("Emoji sent and received", fmt(em.total), `${em.unique} different ones`),
      kpi("Messages with emoji", pct(em.share_of_messages), "of everything"),
      kpi("Your favourite", em.yours[0] ? em.yours[0].emoji : "none", em.yours[0] ? `${fmt(em.yours[0].count)} times` : "you type in words"),
      kpi("Theirs", em.theirs[0] ? em.theirs[0].emoji : "none", em.theirs[0] ? `${fmt(em.theirs[0].count)} times` : "they type in words"),
    ].join("") : kpi("Emoji", "0", "not a single one, which is its own personality");
    chartOrTable("emoji-chart", (c) => hbars(c, em.top, { key: "emoji", keyLabel: "Emoji", labelW: 60, labelSize: 17, series: ["sent", "received"], labels: ["Sent by you", "Received"],
      onClick: (e) => openDrawer(e.emoji, { q: e.emoji }) }));
    $("#emoji-by").innerHTML = em.by_contact.length ? `<table class="data"><thead><tr><th>Person</th><th class="num">Emoji</th><th>Favourites</th></tr></thead><tbody>` +
      em.by_contact.map(r => `<tr><td>${dot(r.contact)}${esc(r.contact)}</td><td class="num">${fmt(r.count)}</td><td>${esc(r.top.join(" "))}</td></tr>`).join("") + "</tbody></table>"
      : '<div class="empty">No emoji anywhere.</div>';

    chartOrTable("tone-chart", (c) => diverging(c, tn.by_month, { key: "month", value: "net",
      onClick: (r) => openDrawer(`Tone in ${r.month}`, { month: r.month }) }));
    chartOrTable("tone-people", (c) => diverging(c, tn.by_contact.slice(0, 12), { key: "contact", value: "net", horizontal: true,
      onClick: (r) => openDrawer(r.contact, { contact: r.contact }) }));
    const toneTable = (list, heading) => `<table class="data"><thead><tr><th>${heading}</th><th class="num">Times</th></tr></thead><tbody>` +
      list.map(w => `<tr class="clickable" data-word="${esc(w.word)}"><td>${esc(w.word)}</td><td class="num">${fmt(w.count)}</td></tr>`).join("") + "</tbody></table>";
    $("#tone-words").innerHTML = tn.top_positive.length || tn.top_negative.length
      ? `<div class="grid2"><div>${toneTable(tn.top_positive.slice(0, 8), "Warm")}</div><div>${toneTable(tn.top_negative.slice(0, 8), "Cold")}</div></div>`
      : '<div class="empty">No tone words found.</div>';
    $$("#tone-words tr[data-word]").forEach(tr => tr.addEventListener("click", () => openDrawer(`“${tr.dataset.word}”`, { word: tr.dataset.word })));
  };

  // ---------- wrapped card ----------
  // Built as plain SVG (no foreignObject) so it can be rasterised to PNG.
  const W = 1080, H = 1350, PAD = 76;
  const compact = (n) => n >= 1e6 ? (n / 1e6).toFixed(1) + "M" : n >= 1e4 ? Math.round(n / 1e3) + "K" : fmt(n);

  const fitLine = (s, width, size) => {
    const max = Math.floor(width / (size * 0.52));
    return s.length <= max ? s : s.slice(0, max - 1).trimEnd() + "…";
  };

  function wrappedSVG(c, year) {
    if (!c || c.empty) return `<svg viewBox="0 0 ${W} ${H}" xmlns="http://www.w3.org/2000/svg"><rect width="${W}" height="${H}" fill="#fcfcfb"/>
      <text x="${W / 2}" y="${H / 2}" text-anchor="middle" font-family="system-ui, sans-serif" font-size="34" fill="#898781">No messages in ${esc(String(year))}.</text></svg>`;
    const F = 'system-ui, -apple-system, "Segoe UI", sans-serif';
    const ink = "#0b0b0b", ink2 = "#52514e", mute = "#898781", surf = "#fcfcfb", card = "#f2f1ec";
    const hue = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100"];
    const T = (x, y, s, size, { fill = ink, weight = 400, anchor = "start", spacing = 0 } = {}) =>
      `<text x="${x}" y="${y}" font-family='${F}' font-size="${size}" font-weight="${weight}" fill="${fill}" text-anchor="${anchor}"${spacing ? ` letter-spacing="${spacing}"` : ""}>${esc(s)}</text>`;
    const box = (x, y, w, h, r = 18, fill = card) => `<rect x="${x}" y="${y}" width="${w}" height="${h}" rx="${r}" fill="${fill}"/>`;
    const TILE_H = 132;
    const fit = (s, max = 46) => Math.max(24, Math.min(max, Math.floor((colW - 52) / (String(s).length * 0.62))));
    const tile = (x, y, w, label, value, sub, accent) =>
      box(x, y, w, TILE_H) + T(x + 26, y + 38, label.toUpperCase(), 19, { fill: mute, spacing: 1.4 }) +
      T(x + 26, y + 88, value, fit(value), { weight: 600, fill: accent || ink }) +
      (sub ? T(x + 26, y + 118, sub, 19, { fill: ink2 }) : "");

    const hours = (v) => v == null ? "n/a" : v < 60 ? `${Math.round(v)} min` : `${(v / 60).toFixed(1)} h`;
    const top = c.top_contact, colW = (W - PAD * 2 - 24) / 2;
    let y = 0;
    const parts = [`<rect width="${W}" height="${H}" fill="${surf}"/>`];
    parts.push(`<rect x="0" y="0" width="${W}" height="10" fill="${hue[0]}"/>`);
    parts.push(T(PAD, 96, "RANDOSTATS", 24, { fill: mute, spacing: 3, weight: 600 }));
    parts.push(T(W - PAD, 96, String(year), 24, { fill: mute, anchor: "end", spacing: 2 }));

    parts.push(T(PAD, 244, compact(c.total), 140, { weight: 600 }));
    parts.push(T(PAD, 292, `messages with ${c.people} ${c.people === 1 ? "person" : "people"}, ${c.per_day} a day`, 29, { fill: ink2 }));

    y = 340;
    parts.push(box(PAD, y, W - PAD * 2, 180));
    parts.push(T(PAD + 26, y + 40, "MOST OF THEM WITH", 19, { fill: mute, spacing: 1.4 }));
    if (top) {
      parts.push(T(PAD + 26, y + 104, top.contact, fit(top.contact, 50), { weight: 600 }));
      parts.push(T(PAD + 26, y + 144, `${fmt(top.total)} messages · you wrote ${Math.round(100 * top.sent / top.total)}%`, 23, { fill: ink2 }));
      const barW = W - PAD * 2 - 52, x0 = PAD + 26, yb = y + 158;
      const sw = barW * top.sent / top.total;
      parts.push(`<rect x="${x0}" y="${yb}" width="${Math.max(0, sw - 2)}" height="8" rx="4" fill="${hue[0]}"/>`);
      parts.push(`<rect x="${x0 + sw}" y="${yb}" width="${barW - sw}" height="8" rx="4" fill="${hue[1]}"/>`);
      if (c.runner_up) parts.push(T(W - PAD - 26, y + 40, `then ${c.runner_up.contact} (${compact(c.runner_up.total)})`, 21, { fill: mute, anchor: "end" }));
    }

    y = 548;
    parts.push(tile(PAD, y, colW, "Busiest hour", `${c.peak_hour}:00`, `and ${c.peak_weekday} more than any day`));
    parts.push(tile(PAD + colW + 24, y, colW, "You reply in", hours(c.reply_median), `they take ${hours(c.their_reply_median)}`));
    y += 152;
    parts.push(tile(PAD, y, colW, "After midnight", compact(c.night_messages), `${pct(c.night_share)} of everything`, hue[2]));
    parts.push(tile(PAD + colW + 24, y, colW, "Longest streak", `${fmt(c.streak.days)} d`, "in a row without a gap"));
    y += 152;
    parts.push(tile(PAD, y, colW, "Your word", c.top_word ? c.top_word.word : "none", c.top_word ? `${fmt(c.top_word.count)} times` : ""));
    parts.push(tile(PAD + colW + 24, y, colW, "Your emoji", c.top_emoji ? c.top_emoji.emoji : "none", c.top_emoji ? `${fmt(c.top_emoji.count)} times` : "you type in words"));
    y += 152;
    parts.push(tile(PAD, y, colW, "Most misspelled", c.top_typo ? c.top_typo.word : "nothing", c.top_typo ? `${fmt(c.top_typo.count)} times` : "suspicious", hue[1]));
    parts.push(tile(PAD + colW + 24, y, colW, "You started", pct(c.you_opened_share), `of ${compact(c.conversations)} conversations`));

    y = 1168;
    if (c.counterpoint) {
      parts.push(`<line x1="${PAD}" y1="${y}" x2="${W - PAD}" y2="${y}" stroke="#e1e0d9" stroke-width="2"/>`);
      parts.push(T(PAD, y + 40, `You wrote ${pct(c.sent_share)} of these messages.`, 25, { fill: ink2 }));
      parts.push(T(PAD, y + 78, fitLine(c.counterpoint.statement + ".", W - PAD * 2, 25), 25, { fill: ink, weight: 600 }));
      parts.push(T(PAD, y + 112, `${c.counterpoint.source}, ${c.counterpoint.year} · unrelated, equally true`, 20, { fill: mute }));
    }
    return `<svg viewBox="0 0 ${W} ${H}" width="${W}" height="${H}" xmlns="http://www.w3.org/2000/svg">${parts.join("")}</svg>`;
  }

  render.wrapped = async () => {
    const sel = $("#wrapped-year");
    const chosen = sel.value;
    const { years, card } = await api("/api/wrapped" + (chosen ? `?year=${chosen}` : ""));
    if (sel.options.length !== years.length) {
      sel.innerHTML = years.map(y => `<option value="${y}">${y}</option>`).join("");
      if (card.year) sel.value = card.year;
    }
    $("#wrapped-card").innerHTML = wrappedSVG(card, card.year ?? "");
  };

  $("#wrapped-year").addEventListener("change", () => render.wrapped());
  $("#wrapped-png").addEventListener("click", () => {
    const svg = $("#wrapped-card svg");
    if (!svg) return;
    const blob = new Blob([new XMLSerializer().serializeToString(svg)], { type: "image/svg+xml;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const img = new Image();
    img.onload = () => {
      const scale = 2, canvas = document.createElement("canvas");
      canvas.width = W * scale; canvas.height = H * scale;
      const ctx = canvas.getContext("2d");
      ctx.scale(scale, scale);
      ctx.drawImage(img, 0, 0, W, H);
      URL.revokeObjectURL(url);
      canvas.toBlob((png) => {
        const a = document.createElement("a");
        a.href = URL.createObjectURL(png);
        a.download = `randostats-${$("#wrapped-year").value || "wrapped"}.png`;
        a.click();
        setTimeout(() => URL.revokeObjectURL(a.href), 5000);
      }, "image/png");
    };
    img.onerror = () => { URL.revokeObjectURL(url); alert("Could not render the PNG. The card is still on screen."); };
    img.src = url;
  });

  // ---------- counterpoint ----------
  // Everything that reaches innerHTML goes through this. Message text, contact
  // names and senders all come from imported files and are not to be trusted.
  const esc = (s) => s == null ? "" : String(s).replace(/[&<>"']/g, (ch) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[ch]));
  function renderCounter(payload, { prepend = false } = {}) {
    const box = $("#counter-results");
    if (!payload.results.length && !prepend) { box.innerHTML = '<div class="empty">No number in there, so nothing to deflate. Try “70% of people…”, “1 in 5…”, “most people…”, or “3 times more likely”.</div>'; return; }
    const groups = {};
    for (const r of payload.results) (groups[r.claim.key] ||= []).push(r);
    const html = Object.entries(groups).map(([key, rs]) => {
      const llm = payload.llm?.[key];
      const chosen = llm ? rs.find(r => r.fact.id === llm.fact_id) || rs[0] : rs[0];
      const others = rs.filter(r => r !== chosen);
      return `<div class="cp ${llm ? "llm" : ""}">
        <div class="claim">They said <b>“${esc(rs[0].claim.raw)}”</b>${llm ? '<span class="badge">Claude</span>' : ""}</div>
        <div class="punch">${esc(llm ? llm.punchline : chosen.lines[0])}</div>
        <div class="fact">${esc(chosen.fact.statement)}.</div>
        <div class="src">${esc(chosen.fact.source)}, ${chosen.fact.year} · ${chosen.claim.kind === "ratio" ? "nearest match" : chosen.gap === 0 ? "identical" : chosen.gap === 1 ? "1 point off" : chosen.gap + " points off"}</div>
        ${others.map(o => `<div class="fact" style="margin-top:6px">Also: ${esc(o.lines[0])} <span class="src">(${esc(o.fact.source)}, ${o.fact.year})</span></div>`).join("")}
        <div class="gap"><b>The actual problem:</b> ${esc(llm ? llm.logic_gap : chosen.fallacy)}</div>
      </div>`;
    }).join("");
    if (prepend) box.insertAdjacentHTML("afterbegin", html); else box.innerHTML = html;
  }
  async function counter(text, { live = false } = {}) {
    if (!text.trim()) return;
    if (live && !state.session) state.session = (await api("/api/counterpoint/session", { method: "POST" })).session;
    const payload = await api("/api/counterpoint", { method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text, session: live ? state.session : null, llm: $("#counter-llm").checked }) });
    if (live) { if (payload.results.length) renderCounter(payload, { prepend: true }); } else renderCounter(payload);
  }
  $("#members-contact").addEventListener("change", () => renderMembers());
  // Packs and voices are plain JSON on disk; the UI just toggles which are loaded.
  function renderPacks(cfg) {
    $("#pack-chips").innerHTML = cfg.packs.map(p =>
      `<button class="chip ${p.enabled ? "on" : ""} ${p.always_on ? "fixed" : ""} ${p.locked ? "locked" : ""}" data-pack="${esc(p.id)}"
        title="${esc(p.description)} (${p.facts} facts)"${p.always_on || p.locked ? " disabled" : ""}>${esc(p.name)}</button>`).join("");
    $("#voice-chips").innerHTML = cfg.voices.map(v =>
      `<button class="chip ${v.id === cfg.voice ? "on" : ""}" data-voice="${esc(v.id)}" title="${esc(v.description)}">${esc(v.name)}</button>`).join("");
    $("#pack-count").textContent = `${fmt(cfg.facts)} facts loaded`;
    $$("#pack-chips .chip:not([disabled])").forEach(b => b.addEventListener("click", async () => {
      const on = $$("#pack-chips .chip.on").map(x => x.dataset.pack);
      const next = b.classList.contains("on") ? on.filter(x => x !== b.dataset.pack) : [...on, b.dataset.pack];
      renderPacks(await api("/api/counterpoint/packs", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ packs: next }) }));
      state.session = null;
    }));
    $$("#voice-chips .chip").forEach(b => b.addEventListener("click", async () => {
      renderPacks(await api("/api/counterpoint/packs", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ voice: b.dataset.voice }) }));
      state.session = null;
      if ($("#counter-text").value.trim()) counter($("#counter-text").value);
    }));
  }
  render.counter = async () => { if (!$("#pack-chips").children.length) renderPacks(await api("/api/counterpoint/packs")); };

  $("#counter-go").addEventListener("click", () => counter($("#counter-text").value));
  $("#counter-text").addEventListener("keydown", (e) => { if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) counter($("#counter-text").value); });
  $("#counter-random").addEventListener("click", async () => {
    const p = await api("/api/counterpoint/random");
    $("#counter-results").insertAdjacentHTML("afterbegin", `<div class="cp"><div class="claim">Random spurious correlation</div><div class="punch">${esc(p.line)}</div>
      <div class="src">${esc(p.a.source)}, ${p.a.year} · ${esc(p.b.source)}, ${p.b.year}</div><div class="gap"><b>The actual problem:</b> two numbers being close is not a relationship. It is arithmetic.</div></div>`);
  });

  // Live listening via the Web Speech API (Chrome, Edge, Safari).
  const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
  let rec = null, listening = false, finalText = "";
  const listenBtn = $("#counter-listen");
  if (!SR) { listenBtn.disabled = true; listenBtn.title = "Speech recognition needs Chrome, Edge, or Safari"; }
  listenBtn.addEventListener("click", () => {
    if (listening) { listening = false; rec.stop(); return; }
    rec = new SR(); rec.continuous = true; rec.interimResults = true; rec.lang = navigator.language || "en-US";
    finalText = ""; state.session = null;
    $("#transcript").hidden = false; $("#transcript").textContent = "";
    rec.onresult = (e) => {
      let interim = "";
      for (let i = e.resultIndex; i < e.results.length; i++) {
        const t = e.results[i][0].transcript;
        if (e.results[i].isFinal) { finalText += t + " "; counter(t, { live: true }); } else interim += t;
      }
      $("#transcript").textContent = finalText + interim;
    };
    rec.onend = () => { if (listening) { try { rec.start(); } catch { /* restarted too fast */ } } else { listenBtn.classList.remove("listening"); listenBtn.textContent = "🎙 Listen"; $("#listen-state").textContent = ""; } };
    rec.onerror = (e) => { $("#listen-state").textContent = e.error === "not-allowed" ? "microphone blocked" : `mic: ${e.error}`; if (e.error === "not-allowed") listening = false; };
    listening = true; listenBtn.classList.add("listening"); listenBtn.textContent = "■ Stop"; $("#listen-state").textContent = "listening… say a statistic";
    rec.start();
  });

  // ---------- import ----------
  $("#import-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const fd = new FormData(e.target);
    $("#import-result").textContent = "importing…";
    try {
      const r = await api("/api/import", { method: "POST", body: fd });
      const named = r.contacts.slice(0, 8).map(esc).join(", ") + (r.contacts.length > 8 ? "…" : "");
      $("#import-result").innerHTML = `Parsed ${fmt(r.parsed)} messages as ${esc(r.format)}, ${fmt(r.added)} new. ` +
        `${fmt(r.total)} total. Contacts: ${named}` + (r.note ? `<div class="note">${esc(r.note)}</div>` : "");
      await refresh();
    } catch (err) { $("#import-result").textContent = "Import failed: " + err.message; }
  });
  $("#load-sample").addEventListener("click", async () => {
    $("#import-result").textContent = "loading sample…";
    try {
      const blob = await (await fetch("/samples/sample_messages.json")).blob();
      const fd = new FormData(); fd.append("file", blob, "sample_messages.json"); fd.append("self_name", "Sam"); fd.append("fmt", "json");
      const r = await api("/api/import", { method: "POST", body: fd });
      $("#self-name").value = "Sam";
      $("#import-result").textContent = `Sample loaded: ${fmt(r.parsed)} messages between Sam and ${r.contacts.join(", ")}.`;
      await refresh(); switchTab("people");
    } catch (err) { $("#import-result").textContent = "Sample not available: " + err.message; }
  });
  $("#clear-all").addEventListener("click", async () => {
    if (!confirm("Delete every imported message from the local database?")) return;
    await api("/api/messages", { method: "DELETE" }); $("#import-result").textContent = "Cleared."; await refresh();
  });

  // ---------- tabs & refresh ----------
  const switchTab = (name) => {
    $$(".tabs button").forEach(b => b.classList.toggle("active", b.dataset.tab === name));
    $$(".tab").forEach(t => t.classList.toggle("active", t.id === "tab-" + name));
    location.hash = name;
    (render[name] || (() => {}))();
  };
  $$(".tabs button").forEach(b => b.addEventListener("click", () => switchTab(b.dataset.tab)));
  // Back, forward, and a pasted #hash link should all land on the right tab.
  window.addEventListener("hashchange", () => {
    const tab = location.hash.slice(1);
    const btn = tab && $(`.tabs button[data-tab="${tab}"]`);
    if (btn && !btn.classList.contains("active")) switchTab(tab);
  });
  ["people-limit", "timing-contact", "spell-direction", "spell-contact", "words-direction", "convo-gap"].forEach(id => $("#" + id).addEventListener("change", () => switchTab(location.hash.slice(1) || "people")));

  async function refresh() {
    const st = await api("/api/status");
    state.llm = st.llm; $("#llm-label").hidden = !st.llm;
    $("#status").textContent = st.messages ? `${fmt(st.messages)} messages · ${st.contacts} people` : "no messages imported";
    if (st.self_name) $("#self-name").value = st.self_name;
    const contacts = st.messages ? await api("/api/stats/contacts") : [];
    state.contacts = contacts;
    assignHues(contacts);
    for (const id of ["timing-contact", "spell-contact"]) {
      const sel = $("#" + id), cur = sel.value;
      sel.innerHTML = '<option value="">Everyone</option>' + contacts.map(c => `<option value="${esc(c.contact)}">${esc(c.contact)} (${fmt(c.total)})</option>`).join("");
      sel.value = cur;
    }
    const tab = location.hash.slice(1);
    switchTab(tab && $("#tab-" + tab) ? tab : (st.messages ? "people" : "import"));
  }
  refresh().catch(err => { $("#status").textContent = "backend unreachable: " + err.message; });
})();
