/* High-level dashboard for the desktop app. Kept separate from chart internals. */
(() => {
  const core = window.RandoCore;
  if (!core) return;

  const { $, api, fmt, make, openTab } = core;
  const pct = (value) => value == null ? "—" : `${Math.round(Number(value) * 100)}%`;
  const mins = (value) => {
    if (value == null) return "—";
    const n = Number(value);
    if (n < 60) return `${Math.round(n)} min`;
    if (n < 1440) return `${(n / 60).toFixed(1)} h`;
    return `${(n / 1440).toFixed(1)} d`;
  };

  const metric = (label, value, detail = "") => {
    const card = make("div", "overview-metric");
    card.append(make("span", "overview-metric-label", label));
    card.append(make("strong", "overview-metric-value", value));
    if (detail) card.append(make("span", "overview-metric-detail", detail));
    return card;
  };

  const insight = (title, copy, target) => {
    const button = make("button", "insight-card");
    button.type = "button";
    button.append(make("span", "insight-title", title));
    button.append(make("span", "insight-copy", copy));
    if (target) {
      button.append(make("span", "insight-cue", "Open view →"));
      button.addEventListener("click", () => openTab(target));
    }
    return button;
  };

  const firstRun = (root) => {
    root.replaceChildren();
    const hero = make("div", "overview-welcome");
    hero.append(make("span", "eyebrow", "Local-first message analytics"));
    hero.append(make("h1", "", "See the shape of your conversations."));
    hero.append(make("p", "overview-lede", "Import a chat export to explore who you talk to, when you reply, the words you use, and the messages behind every number. Everything stays on this machine."));

    const actions = make("div", "overview-actions");
    const sample = make("button", "primary", "Try sample data");
    sample.type = "button";
    sample.addEventListener("click", () => {
      openTab("import");
      setTimeout(() => $("#load-sample")?.click(), 0);
    });
    const importButton = make("button", "ghost", "Import my messages");
    importButton.type = "button";
    importButton.addEventListener("click", () => openTab("import"));
    actions.append(sample, importButton);
    hero.append(actions);

    const sourceNote = make("div", "privacy-note");
    sourceNote.append(make("strong", "", "Private by design."));
    sourceNote.append(document.createTextNode(" Your exports are parsed locally into SQLite; they are not uploaded to a hosted service."));
    hero.append(sourceNote);
    root.append(hero);
  };

  async function renderOverview() {
    const root = $("#overview-content");
    if (!root) return;
    core.loading(root, "Building a quick read from your imported messages…");
    try {
      const status = await api("/api/status");
      if (!status.messages) {
        firstRun(root);
        return;
      }

      const [overview, contacts, timing, conversations] = await Promise.all([
        api("/api/stats/overview"),
        api("/api/stats/contacts"),
        api("/api/stats/timing"),
        api("/api/stats/conversations?gap_hours=6"),
      ]);

      root.replaceChildren();
      const head = make("div", "overview-heading");
      const text = make("div");
      text.append(make("span", "eyebrow", "At a glance"));
      text.append(make("h1", "", "Your messages, without the spreadsheet feeling."));
      text.append(make("p", "overview-lede", "A few useful signals first. Every deeper view is still one tap away."));
      head.append(text);
      root.append(head);

      const metrics = make("div", "overview-metrics");
      metrics.append(
        metric("Messages", fmt(overview.total), `${fmt(overview.sent)} sent · ${fmt(overview.received)} received`),
        metric("People", fmt(overview.contacts), overview.days ? `across ${fmt(overview.days)} days` : ""),
        metric("Per day", String(overview.per_day ?? "—"), overview.total ? `${Math.round(100 * overview.sent / overview.total)}% written by you` : ""),
        metric("Peak time", timing.peak_hour == null ? "—" : `${timing.peak_weekday} ${timing.peak_hour}:00`, timing.busiest_day?.date ? `busiest day: ${timing.busiest_day.date}` : "")
      );
      root.append(metrics);

      const top = contacts[0];
      const summary = conversations.summary || {};
      const insightGrid = make("div", "insight-grid");
      if (top) {
        insightGrid.append(insight(
          `You talk to ${top.contact} most`,
          `${fmt(top.total)} messages total; you wrote ${Math.round(100 * top.sent_share)}% of them.`,
          "people"
        ));
      }
      if (timing.reply_latency) {
        insightGrid.append(insight(
          "Your reply rhythm",
          `You typically reply in ${mins(timing.reply_latency.you_median_minutes)}; they take ${mins(timing.reply_latency.them_median_minutes)}.`,
          "timing"
        ));
      }
      if (summary.conversations) {
        insightGrid.append(insight(
          "Who starts the conversation?",
          `You open ${pct(summary.you_opened_share)} of conversations and get the last word ${pct(summary.you_closed_share)} of the time.`,
          "convo"
        ));
      }
      if (timing.peak_weekday != null) {
        insightGrid.append(insight(
          "When you are most active",
          `${timing.peak_weekday} around ${timing.peak_hour}:00 is your busiest recurring slot.`,
          "timing"
        ));
      }
      root.append(insightGrid);

      const shortcuts = make("div", "overview-shortcuts");
      shortcuts.append(make("h2", "", "Go deeper"));
      const row = make("div", "overview-shortcut-row");
      [
        ["People", "people"], ["Conversations", "convo"], ["Timing", "timing"],
        ["Words & tone", "words"], ["Wrapped", "wrapped"], ["Import", "import"],
      ].forEach(([label, tab]) => {
        const button = make("button", "ghost", label);
        button.type = "button";
        button.addEventListener("click", () => openTab(tab));
        row.append(button);
      });
      shortcuts.append(row);
      root.append(shortcuts);
    } catch (err) {
      core.error(root, err, "Could not build the overview");
    }
  }

  // One owner. switchTab in app.js sets location.hash on every route to a tab (click, arrow
  // keys, openTab, a pasted link, back/forward), so hashchange sees them all; a click
  // listener on top of it rendered the tab twice.
  window.addEventListener("hashchange", () => {
    if (location.hash === "#overview") renderOverview();
  });
  if (location.hash === "#overview") renderOverview();
})();
