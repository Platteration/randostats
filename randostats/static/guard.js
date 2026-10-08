/* randostats - the safety net. Loaded first on every page that has a script, as
 * its own file (the policy is script-src 'self': nothing inline runs), and
 * depending on nothing, so that when one of the page's scripts fails to load or
 * throws before the page has started, the visitor reads a short note with a
 * Reload button instead of finding controls that do nothing. A page that has
 * started says so with RandoGuard.started(). With JavaScript off altogether, the
 * page's own <noscript> note says it instead.
 *
 * Written as ES5 on purpose: a browser too old for the app's own scripts fails
 * to parse them, and this is what tells its user so.
 */
(function () {
  "use strict";

  var started = false;
  var failed = false;
  var FAILED = "This page did not start: part of it did not load, or stopped with an error. " +
    "Reload to try again. If it keeps happening, check that randostats is still running.";
  var LATER = "Something went wrong on this page. If a control stops responding, reload.";

  function show(text, dismissible) {
    if (!document.body) {
      document.addEventListener("DOMContentLoaded", function () { show(text, dismissible); });
      return;
    }
    var note = document.getElementById("guard-note");
    if (!note) {
      note = document.createElement("div");
      note.id = "guard-note";
      note.className = "guard-note";
      note.setAttribute("role", "alert");
      note.appendChild(document.createElement("span"));
      var reload = document.createElement("button");
      reload.type = "button";
      reload.textContent = "Reload";
      reload.addEventListener("click", function () { location.reload(); });
      note.appendChild(reload);
      document.body.insertBefore(note, document.body.firstChild);
    }
    note.firstChild.textContent = text;
    var close = document.getElementById("guard-dismiss");
    if (dismissible && !close) {
      close = document.createElement("button");
      close.type = "button";
      close.id = "guard-dismiss";
      close.textContent = "Dismiss";
      close.addEventListener("click", function () { note.parentNode.removeChild(note); });
      note.appendChild(close);
    } else if (!dismissible && close) {
      close.parentNode.removeChild(close);
    }
  }

  function fail() {
    failed = true;
    show(FAILED, false);
  }

  // The capture phase sees a script or stylesheet that failed to load, which
  // does not bubble, as well as an error thrown anywhere on the page.
  window.addEventListener("error", function (event) {
    var target = event.target;
    if (target && target !== window && target.tagName) {
      var tag = target.tagName.toLowerCase();
      if (tag === "script" || (tag === "link" && target.rel === "stylesheet")) fail();
      return;
    }
    if (!started) fail();
    else if (!failed) show(LATER, true);
  }, true);

  window.addEventListener("unhandledrejection", function () {
    if (started && !failed) show(LATER, true);
  });

  // Every script in the page has run by now. One that never said it started
  // threw on the way, or never arrived.
  window.addEventListener("load", function () {
    if (!started) fail();
  });

  window.RandoGuard = { started: function () { started = true; } };
}());
