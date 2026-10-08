/* The sign-in page. Submitted by script, not by the form itself: the policy is
 * form-action 'none', which keeps the import form, and anything injected, from
 * posting anywhere natively. The server checks ?next= and answers with where to
 * go (auth.local_path), so a link to /login cannot send anyone off the site. */
(() => {
  const form = document.getElementById("sign-in");
  const password = document.getElementById("password");
  const result = document.getElementById("sign-in-result");
  const next = new URLSearchParams(location.search).get("next") || "/";
  password.focus();

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
