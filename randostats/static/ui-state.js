/* Shared browser helpers for randostats views. Plain JS, no build step. */
(() => {
  const $ = (selector, root = document) => root.querySelector(selector);
  const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];
  const fmt = (value) => Number(value || 0).toLocaleString();
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

  const make = (tag, className = "", text = null) => {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text != null) node.textContent = text;
    return node;
  };

  const openTab = (name) => $(`.tabs button[data-tab="${name}"]`)?.click();

  const clear = (container) => {
    if (container) container.replaceChildren();
  };

  const state = (container, kind, title, detail = "") => {
    if (!container) return;
    clear(container);
    const box = make("div", `view-state view-state-${kind}`);
    box.setAttribute("role", kind === "error" ? "alert" : "status");
    box.appendChild(make("strong", "", title));
    if (detail) box.appendChild(make("span", "", detail));
    container.appendChild(box);
  };

  const loading = (container, detail = "Loading this view…") => state(container, "loading", "Working", detail);
  const empty = (container, title = "Nothing here yet", detail = "Import some messages to get started.") => state(container, "empty", title, detail);
  const error = (container, err, title = "Could not load this view") => state(container, "error", title, `${err?.message || err}. Try again; the rest of the app is still available.`);

  const core = Object.freeze({ $, $$, api, clear, empty, error, escapeHtml, fmt, loading, make, openTab, state });
  window.RandoCore = core;
})();
