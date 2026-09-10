/* Counterpoint, phone edition.
 *
 * Deliberately standalone rather than sharing app.js: that file is one closed
 * IIFE with no exports, and the genuinely reusable part is about twenty lines.
 * The escaping discipline is what matters, and tests/test_security.py walks
 * this file too — every value reaching innerHTML goes through esc().
 */
(() => {
  const $ = (sel) => document.querySelector(sel);
  const $$ = (sel) => [...document.querySelectorAll(sel)];
  const fmt = (n) => Number(n).toLocaleString();

  // Message text, contact names and fact statements are all somebody else's
  // words. Nothing reaches the DOM as markup.
  const esc = (s) => s == null ? "" : String(s).replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

  const say = (text) => { $("#live").textContent = text; };

  const state = { deck: [], index: 0, shareFile: null, busy: false };

  async function api(path, opts) {
    const r = await fetch(path, opts);
    if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || r.statusText);
    return r.json();
  }

  // ---------------------------------------------------------------- the deck

  /* One flat list of answers. Where Claude picked a fact, that one leads. */
  function buildDeck(payload) {
    const groups = new Map();
    for (const r of payload.results || []) {
      if (!groups.has(r.claim.key)) groups.set(r.claim.key, []);
      groups.get(r.claim.key).push(r);
    }
    const deck = [];
    for (const [key, results] of groups) {
      const llm = payload.llm && payload.llm[key];
      const ordered = llm ? [...results].sort((a, b) => (b.fact.id === llm.fact_id) - (a.fact.id === llm.fact_id)) : results;
      ordered.forEach((r, i) => {
        const chosen = llm && i === 0;
        deck.push({
          claim: r.claim.raw,
          kind: r.claim.kind,
          punch: chosen ? llm.punchline : r.lines[0],
          statement: r.fact.statement,
          source: r.fact.source,
          year: r.fact.year,
          gap: r.gap,
          problem: chosen ? llm.logic_gap : r.fallacy,
          byClaude: Boolean(chosen),
        });
      });
    }
    return deck;
  }

  const gapLabel = (card) =>
    card.kind === "ratio" ? "nearest match"
      : card.gap === 0 ? "identical"
        : card.gap === 1 ? "1 point off" : `${card.gap} points off`;

  /* Several voices quote the fact inside the punchline. Printing it again
     underneath just pads the card out. */
  const repeatsTheFact = (card) =>
    Boolean(card.statement) && card.punch.includes(card.statement.slice(0, 40));

  function showCard(i) {
    if (!state.deck.length) return;
    state.index = (i + state.deck.length) % state.deck.length;
    const card = state.deck[state.index];
    $("#intro").hidden = true;

    // Built outside the template: a condition in there reads, to the escaping
    // lint, exactly like an unescaped value, and the lint is worth keeping strict.
    const badge = card.byClaude ? ' <span class="muted">· Claude</span>' : "";
    const fact = repeatsTheFact(card) ? "" : `<div class="fact">${esc(card.statement)}.</div>`;

    $("#cards").innerHTML = `<article class="card">
      <div class="said">They said<b>“${esc(card.claim)}”</b></div>
      <p class="punch">${esc(card.punch)}${badge}</p>
      ${fact}
      <div class="src">${esc(card.source)}, ${esc(card.year)} · ${esc(gapLabel(card))}</div>
      <details class="gap"><summary><b>The actual problem</b></summary><p>${esc(card.problem)}</p></details>
      <div class="actions">
        <button class="ghost" data-act="share">Share</button>
        <button class="ghost" data-act="copy">Copy</button>
      </div>
    </article>`;

    $("#deck-nav").hidden = state.deck.length < 2;
    $("#deck-count").textContent = `${state.index + 1} of ${state.deck.length}`;
    say(`${card.punch}. ${card.source}, ${card.year}.`);

    $$("#cards [data-act]").forEach((b) => b.addEventListener("click", () => {
      if (b.dataset.act === "share") shareCard(card);
      else copyCard(card);
    }));

    // Rendered now, not when Share is tapped: navigator.share has to be called
    // straight out of the gesture or iOS rejects it.
    state.shareFile = null;
    renderShareImage(card);
  }

  function showNotice(title, body, cls = "") {
    $("#intro").hidden = true;
    $("#deck-nav").hidden = true;
    $("#cards").innerHTML = `<article class="card empty-state ${esc(cls)}">
      <p class="punch">${esc(title)}</p><div class="fact">${esc(body)}</div></article>`;
    say(`${title}. ${body}`);
  }

  // ------------------------------------------------------------------ asking

  async function counter(text) {
    const claim = text.trim();
    if (!claim || state.busy) return;
    state.busy = true;
    $("#go").disabled = true;
    try {
      // No session: on a phone, dedupe would make a repeated claim return
      // nothing at all, which reads as the app being broken.
      const payload = await api("/api/counterpoint", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ text: claim, session: null, per_claim: 3, llm: true }),
      });
      state.deck = buildDeck(payload);
      if (!state.deck.length) {
        showNotice("No number in there.", "Try “70% of people…”, “1 in 5…”, “most people…”, or “3 times more likely”.");
      } else {
        showCard(0);
      }
    } catch (err) {
      if (!navigator.onLine) showNotice("You're offline.", "The answers come from the app on your machine. Reconnect and try again.");
      else showNotice("That didn't work.", err.message);
    } finally {
      state.busy = false;
      $("#go").disabled = false;
    }
  }

  async function surprise() {
    try {
      const pair = await api("/api/counterpoint/random");
      state.deck = [{
        claim: "two unrelated numbers", kind: "percent",
        punch: pair.line,
        statement: pair.a ? pair.a.statement : "",
        source: pair.a ? pair.a.source : "", year: pair.a ? pair.a.year : "",
        gap: pair.gap == null ? 0 : pair.gap,
        problem: "Two numbers being close is not a relationship. It is arithmetic.",
        byClaude: false,
      }];
      showCard(0);
    } catch (err) {
      showNotice("That didn't work.", err.message);
    }
  }

  // ------------------------------------------------------------ share images

  const CARD_W = 1080, CARD_H = 1350;

  /* app.js only ever needed one-line labels. A typed claim has to wrap: cutting
     it off would throw away the joke. */
  function wrapText(text, maxChars) {
    const lines = [];
    let line = "";
    for (const word of String(text).split(/\s+/)) {
      const candidate = line ? `${line} ${word}` : word;
      if (candidate.length > maxChars && line) { lines.push(line); line = word; }
      else line = candidate;
    }
    if (line) lines.push(line);
    return lines;
  }

  /* Average glyph width as a fraction of font size, measured against what the
     renderer actually produced. Bold is appreciably wider, and guessing low
     here is what pushes a punchline off the edge of the card. */
  const charWidth = (size, weight) => size * (weight >= 600 ? 0.60 : 0.53);

  const fitsIn = (text, size, weight, width, maxLines) =>
    wrapText(text, Math.floor(width / charWidth(size, weight))).length <= maxLines;

  /* Returns the markup and how far down the page it reached, so the next
     block can start below it instead of at a guessed offset. */
  function block(text, { x, y, size, weight = 400, fill, width, maxLines, lead }) {
    const step = lead || size * 1.24;
    const lines = wrapText(text, Math.floor(width / charWidth(size, weight))).slice(0, maxLines);
    const markup = lines.map((l, i) =>
      `<text x="${x}" y="${y + i * step}" font-family="system-ui, sans-serif" ` +
      `font-size="${size}" font-weight="${weight}" fill="${fill}">${esc(l)}</text>`).join("");
    return { markup, bottom: y + (lines.length - 1) * step };
  }

  /* Every colour is a literal. The SVG is rendered inside a detached Image,
     where no stylesheet applies and a CSS variable resolves to nothing. */
  function comebackSVG(card) {
    const ink = "#ffffff", dim = "#c3c2b7", mute = "#898781", warm = "#eb6834";
    const pad = 84, width = CARD_W - pad * 2;
    const parts = [
      `<rect width="${CARD_W}" height="${CARD_H}" fill="#0f0f0e"/>`,
      `<rect width="${CARD_W}" height="10" fill="${warm}"/>`,
      `<text x="${pad}" y="100" font-family="system-ui, sans-serif" font-size="22" letter-spacing="3" fill="${mute}">THEY SAID</text>`,
    ];

    const claim = block(`“${card.claim}”`, { x: pad, y: 158, size: 34, fill: dim, width, maxLines: 2 });
    parts.push(claim.markup);

    // Step the punchline down until the whole of it fits. Truncating it would
    // cut the joke in half, which is the one thing this card must never do, and
    // there is a page of room below. Biggest size that fits entire, wins.
    const LINE_BUDGET = 9;
    const punchSize = [76, 68, 60, 52, 46, 40, 34].find((s) => fitsIn(card.punch, s, 600, width, LINE_BUDGET)) || 30;
    const punch = block(card.punch, { x: pad, y: claim.bottom + 130, size: punchSize, weight: 600, fill: ink, width, maxLines: LINE_BUDGET });
    parts.push(punch.markup);

    let y = punch.bottom + 90;
    if (card.statement && !repeatsTheFact(card)) {
      const fact = block(`${card.statement}.`, { x: pad, y, size: 30, fill: dim, width, maxLines: 3 });
      parts.push(fact.markup);
      y = fact.bottom + 56;
    }
    if (card.source) {
      parts.push(`<text x="${pad}" y="${y}" font-family="system-ui, sans-serif" font-size="24" fill="${mute}">` +
        `${esc(card.source)}, ${esc(card.year)} · ${esc(gapLabel(card))}</text>`);
    }

    // The mark stays pinned to the bottom whatever happened above it.
    parts.push(`<rect x="${pad}" y="1240" width="44" height="44" rx="12" fill="#3987e5"/>`,
      `<text x="${pad + 22}" y="1271" font-family="system-ui, sans-serif" font-size="24" font-weight="700" fill="#fff" text-anchor="middle">%</text>`,
      `<text x="${pad + 60}" y="1271" font-family="system-ui, sans-serif" font-size="26" fill="${dim}">randostats</text>`);
    return `<svg xmlns="http://www.w3.org/2000/svg" width="${CARD_W}" height="${CARD_H}" viewBox="0 0 ${CARD_W} ${CARD_H}">${parts.join("")}</svg>`;
  }

  function canvasToBlob(canvas) {
    return new Promise((resolve) => {
      let settled = false;
      const done = (b) => { if (!settled) { settled = true; resolve(b); } };
      const timer = setTimeout(() => done(dataUrlToBlob(canvas)), 4000);
      try {
        canvas.toBlob((b) => { clearTimeout(timer); done(b || dataUrlToBlob(canvas)); }, "image/png");
      } catch { clearTimeout(timer); done(dataUrlToBlob(canvas)); }
    });
  }

  /* Decoded by hand: fetch("data:…") trips connect-src 'self' in some browsers,
     and a CSP violation here would be a mystifying way to lose a share. */
  function dataUrlToBlob(canvas) {
    try {
      const binary = atob(canvas.toDataURL("image/png").split(",")[1]);
      const bytes = new Uint8Array(binary.length);
      for (let i = 0; i < binary.length; i++) bytes[i] = binary.charCodeAt(i);
      return new Blob([bytes], { type: "image/png" });
    } catch { return null; }
  }

  function renderShareImage(card) {
    const svg = comebackSVG(card);
    const url = URL.createObjectURL(new Blob([svg], { type: "image/svg+xml;charset=utf-8" }));
    const img = new Image();
    img.onload = async () => {
      // 2x is 5.8 megapixels. 3x would be 13, where older phones start
      // handing back a blank canvas.
      const scale = 2;
      const canvas = document.createElement("canvas");
      canvas.width = CARD_W * scale;
      canvas.height = CARD_H * scale;
      const ctx = canvas.getContext("2d");
      ctx.scale(scale, scale);
      ctx.drawImage(img, 0, 0, CARD_W, CARD_H);
      URL.revokeObjectURL(url);
      const blob = await canvasToBlob(canvas);
      if (blob) state.shareFile = new File([blob], "counterpoint.png", { type: "image/png" });
    };
    img.onerror = () => URL.revokeObjectURL(url);
    img.src = url;
  }

  function shareCard(card) {
    const file = state.shareFile;
    // Called with no await in front of it, or iOS treats it as untrusted.
    if (file && navigator.canShare && navigator.canShare({ files: [file] })) {
      navigator.share({ files: [file] }).catch(() => {});
      return;
    }
    if (file) {
      const url = URL.createObjectURL(file);
      const a = document.createElement("a");
      a.href = url;
      a.download = "counterpoint.png";
      a.click();
      setTimeout(() => URL.revokeObjectURL(url), 5000);
      return;
    }
    copyCard(card);
    hint("Couldn't build the image, so the text is on your clipboard instead.");
  }

  function copyCard(card) {
    const text = `${card.punch}\n${card.statement ? card.statement + ". " : ""}${card.source}, ${card.year}`;
    if (navigator.clipboard) navigator.clipboard.writeText(text).then(() => hint("Copied."), () => hint("Could not copy."));
    else hint("Copying isn't available in this browser.");
  }

  let hintTimer;
  function hint(text) {
    const node = $("#hint");
    node.textContent = text;
    node.hidden = false;
    clearTimeout(hintTimer);
    hintTimer = setTimeout(() => { node.hidden = true; }, 6000);
  }

  // ------------------------------------------------------- packs and voices

  async function loadSettings(config) {
    const cfg = config || await api("/api/counterpoint/packs");
    $("#packs").innerHTML = cfg.packs.map((p) =>
      `<button class="chip ${p.enabled ? "on" : ""} ${p.locked ? "locked" : ""}" data-pack="${esc(p.id)}"
        ${p.always_on || p.locked ? "disabled" : ""}>${esc(p.name)}</button>`).join("");
    $("#voices").innerHTML = cfg.voices.map((v) =>
      `<button class="chip ${v.id === cfg.voice ? "on" : ""}" data-voice="${esc(v.id)}">${esc(v.name)}</button>`).join("");
    $("#fact-count").textContent = `${fmt(cfg.facts)} facts loaded`;

    const post = (body) => api("/api/counterpoint/packs", {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
    }).then(loadSettings).catch((err) => hint(err.message));

    $$("#packs .chip:not([disabled])").forEach((b) => b.addEventListener("click", () => {
      const on = $$("#packs .chip.on").map((c) => c.dataset.pack);
      post({ packs: b.classList.contains("on") ? on.filter((x) => x !== b.dataset.pack) : [...on, b.dataset.pack] });
    }));
    $$("#voices .chip").forEach((b) => b.addEventListener("click", () => post({ voice: b.dataset.voice })));
  }

  const openSheet = () => { $("#sheet").hidden = false; $("#sheet-scrim").hidden = false; $("#settings-close").focus(); };
  const closeSheet = () => { $("#sheet").hidden = true; $("#sheet-scrim").hidden = true; };

  // ---------------------------------------------------- speech, if it works

  /* Feature detection is not enough. Inside an installed PWA on iOS the API is
     present, start() resolves, and no result ever arrives. So the mic stays
     hidden until a probe actually returns a transcript, and the verdict sticks. */
  const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
  const SPEECH_KEY = "counterpoint.speech";

  /* The mic never appears on the compose bar until a probe has actually
     returned a transcript. Until then the only mention of it is one line
     inside the settings sheet, well off the thumb path. */
  function offerSpeech() {
    const verdict = localStorage.getItem(SPEECH_KEY);
    if (verdict === "yes") { $("#mic").hidden = false; $("#speech-offer").hidden = true; return; }
    $("#mic").hidden = true;
    $("#speech-offer").hidden = !SR || verdict === "no";
  }

  function listen(probing) {
    const button = $("#mic");
    let heard = false;
    let rec;
    try { rec = new SR(); } catch { return failSpeech(); }
    rec.interimResults = true;
    rec.lang = navigator.language || "en-US";

    const watchdog = setTimeout(() => { try { rec.stop(); } catch { /* already stopped */ } if (!heard) failSpeech(); }, 6000);
    const stop = () => { clearTimeout(watchdog); button.classList.remove("listening"); };

    rec.onresult = (e) => {
      heard = true;
      localStorage.setItem(SPEECH_KEY, "yes");
      offerSpeech();
      if (probing) { closeSheet(); hint("Hands-free works here. The mic is next to the box."); }
      // Fills the box; never submits. One rule: text goes in, the button fires it.
      $("#claim").value = [...e.results].map((r) => r[0].transcript).join(" ").trim();
    };
    rec.onerror = () => { stop(); if (!heard) failSpeech(); };
    rec.onend = () => { stop(); if (!heard) failSpeech(); };

    try {
      rec.start();
      button.classList.add("listening");
      if (probing) hint("Listening… say the statistic.");
    } catch { stop(); failSpeech(); }
  }

  /* The installed-iOS-PWA signature: start() resolves, end fires at once, no
     result, no error. Nothing to feature-detect, so record the verdict. */
  function failSpeech() {
    localStorage.setItem(SPEECH_KEY, "no");
    $("#mic").hidden = true;
    $("#speech-offer").hidden = true;
    hint("Hands-free doesn't work here, whatever the browser claims. Use the dictation key on your keyboard instead — it's better anyway.");
  }

  // ------------------------------------------------------------------- wiring

  $("#go").addEventListener("click", () => counter($("#claim").value));
  $("#random").addEventListener("click", () => surprise());
  $("#next").addEventListener("click", () => showCard(state.index + 1));
  $("#prev").addEventListener("click", () => showCard(state.index - 1));
  $("#mic").addEventListener("click", () => listen(false));
  $("#speech-try").addEventListener("click", () => listen(true));
  $("#settings-open").addEventListener("click", () => openSheet());
  $("#settings-close").addEventListener("click", () => closeSheet());
  $("#sheet-scrim").addEventListener("click", () => closeSheet());
  document.addEventListener("keydown", (e) => { if (e.key === "Escape") closeSheet(); });
  $$("#examples .chip").forEach((b) => b.addEventListener("click", () => {
    $("#claim").value = b.textContent;
    counter(b.textContent);
  }));

  // The textarea grows with a dictated sentence instead of scrolling it away.
  const claim = $("#claim");
  claim.addEventListener("input", () => {
    claim.style.height = "auto";
    claim.style.height = Math.min(claim.scrollHeight, window.innerHeight * 0.3) + "px";
  });

  // Swipe between answers; the buttons stay for everyone who would rather tap.
  let startX = null;
  $("#stage").addEventListener("pointerdown", (e) => { startX = e.clientX; });
  $("#stage").addEventListener("pointerup", (e) => {
    if (startX === null || state.deck.length < 2) return;
    const dx = e.clientX - startX;
    startX = null;
    if (Math.abs(dx) > 60) showCard(state.index + (dx < 0 ? 1 : -1));
  });

  loadSettings().catch(() => { /* the sheet simply stays empty */ });
  offerSpeech();

  if ("serviceWorker" in navigator && window.isSecureContext) {
    navigator.serviceWorker.register("/sw.js", { scope: "/m" }).catch(() => { /* offline is a bonus, not a requirement */ });
  }
})();
