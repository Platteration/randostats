/* Shared UI-state helpers for randostats views. Plain JS, no build step. */
(() => {
  const escapeHtml = (value) => value == null ? "" : String(value).replace(/[&<>"']/g, (ch) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[ch]));

  const api = async (path, opts) => {
    const response = await fetch(path, opts);
    if (!response.ok) {
      const body = await response.json().catch(() => ({}));
      throw new Error(body.detail || response.statusText || `HTTP ${response.status}`);
    }
    return response.json();
  };

  const clear = (container) => {
    if (container) container.replaceChildren();
  };

  const state = (container, kind, title, detail = "") => {
    if (!container) return;
    clear(container);
    const box = document.createElement("div");
    box.className = `view-state view-state-${kind}`;
    if (kind === "error") box.setAttribute("role", "alert");
    else box.setAttribute("role", "status");

    const heading = document.createElement("strong");
    heading.textContent = title;
    box.appendChild(heading);

    if (detail) {
      const copy = document.createElement("span");
      copy.textContent = detail;
      box.appendChild(copy);
    }
    container.appendChild(box);
  };

  const loading = (container, detail = "Loading this view…") => state(container, "loading", "Working", detail);
  const empty = (container, title = "Nothing here yet", detail = "Import some messages to get started.") => state(container, "empty", title, detail);
  const error = (container, err, title = "Could not load this view") => state(container, "error", title, `${err?.message || err}. Try again; the rest of the app is still available.`);

  window.RandoUI = Object.freeze({ api, clear, empty, error, escapeHtml, loading, state });
})();
