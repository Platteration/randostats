/* The sign-in page. Submitted by script, not by the form itself: the policy is
 * form-action 'none', which keeps the import form, and anything injected, from
 * posting anywhere natively. The server checks ?next= and answers with where to
 * go (auth.local_path), so a link to /login cannot send anyone off the site.
 *
 * Sign in with Google is a link, a top-level navigation to the server's start
 * route, which sends the browser on to Google; Google sends it back to the
 * server, and the server back here with ?signin=failed when it did not work out.
 * The page draws it only when /api/config says it is set up: a 404, no answer or
 * an answer that is not JSON means it is not, and the page stays as it was. */
(() => {
  const form = document.getElementById("sign-in");
  const password = document.getElementById("password");
  const result = document.getElementById("sign-in-result");
  const google = document.getElementById("google-sign-in");
  const googleStart = document.getElementById("google-start");
  const params = new URLSearchParams(location.search);
  const next = params.get("next") || "/";
  password.focus();

  // The server checks this too (auth.local_path), and comes back to the site's
  // root for anything that is not a path here.
  googleStart.href = `/api/auth/google/start?return=${encodeURIComponent(next)}`;
  // What the server sent the browser back to say: a sign-in with Google that did not
  // work out, or one started under another of this server's names, which it sends to
  // this, the address Google comes back to, to start again from here.
  const signin = params.get("signin");
  if (signin === "failed" || signin === "here") {
    result.textContent = signin === "failed" ? "Sign-in didn't complete."
      : "Sign in with Google from this address: it is the one Google sends you back to.";
    params.delete("signin");
    const rest = params.toString();
    history.replaceState(null, "", location.pathname + (rest ? `?${rest}` : "") + location.hash);
  }
  fetch("/api/config")
    .then((response) => (response.ok ? response.json() : null))
    .then((config) => {
      const ways = config && typeof config === "object" ? config.auth : null;
      if (!ways || typeof ways !== "object") return;
      if (ways.google === true) google.hidden = false;
      // Only a server that says it has no password loses the form.
      if (ways.password === false && ways.google === true) {
        form.hidden = true;
        googleStart.focus();
      }
    })
    .catch(() => { /* no answer: the page stays as it is */ });

  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const button = form.querySelector("button");
    button.disabled = true;
    result.textContent = "Checking…";
    try {
      const response = await fetch("/api/login", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ password: password.value, next }),
      });
      const body = await response.json().catch(() => ({}));
      if (response.ok) {
        result.textContent = "Signed in.";
        location.replace(body.next || "/");
        return;
      }
      result.textContent = body.detail || `Could not sign in (HTTP ${response.status}).`;
      password.select();
    } catch (err) {
      result.textContent = `The server did not answer: ${err.message}`;
    } finally {
      button.disabled = false;
    }
  });

  if (window.RandoGuard) window.RandoGuard.started();
})();
