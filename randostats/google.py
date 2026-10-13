"""Sign in with Google, as an alternative to the password for a server beyond loopback.

OpenID Connect's authorization code flow (OIDC Core 1.0, section 3.1) with PKCE
(RFC 7636, S256), in the standard library alone. ``start`` sends the browser to
Google with a fresh state, nonce and code challenge, and puts all three (with the
verifier and the page to come back to) in a short-lived cookie this process signs.
Google sends the browser back to ``api/auth/google/callback`` with a code; ``check``
holds the cookie's signature, its expiry and the state against what came back, and
``redeem`` trades the code, the verifier and the client secret for an ID token at
Google's token endpoint and reads the claims.

**The ID token's signature is not checked, and that is the specification's choice,
not a shortcut.** OIDC Core 3.1.3.7, item 6: "If the ID Token is received via direct
communication between the Client and the Token Endpoint (which it is in this flow),
the TLS server validation MAY be used to validate the issuer in place of checking the
token signature." Here the token comes from ``TOKEN_URL``, a constant reached over
HTTPS with the default SSL context (the certificate chain and the host name are
verified, and a redirect is not followed), in exchange for a code only this client's
secret and this sign-in's PKCE verifier redeem. That is what keeps this stdlib-only:
no JWT library, no RSA. It holds only for a token this module fetched itself; one
handed in by a browser, a query string or anything else would need its signature
checked, and nothing here takes one from anywhere else. Every claim is still checked:
``iss``, ``aud``, ``azp``, ``exp``, ``iat``, ``nonce`` and ``sub``, and the email is
kept only when ``email_verified`` is true.

Who may come in is ``RANDOSTATS_GOOGLE_ALLOWED``: Google account ids (``sub``) and
verified email addresses. A Google account that is not on it is turned away like a
wrong password. Nothing about the account is stored: the session it opens lives in
this process (auth.py), with the email for "Signed in as", and ends like any other.

No address here is configurable. The tests hand ``GoogleSignIn`` a ``fetch`` that
answers the token endpoint itself; nothing points this at another host.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import http.client
import json
import math
import re
import secrets
import ssl
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from urllib.parse import quote, urlencode, urlsplit

from .auth import local_path
from .security import allowed_hostname

AUTHORIZE_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
ISSUERS = frozenset({"https://accounts.google.com", "accounts.google.com"})
CALLBACK = "/api/auth/google/callback"
START = "/api/auth/google/start"

# The environment, read once at start-up. The site's own address is configured,
# never taken from a request's Host or X-Forwarded-* headers: it is what Google
# sends the browser back to, so a request must not be able to choose it.
APP_URL = "RANDOSTATS_APP_URL"
CLIENT_ID = "RANDOSTATS_GOOGLE_CLIENT_ID"
CLIENT_SECRET = "RANDOSTATS_GOOGLE_CLIENT_SECRET"
ALLOWED = "RANDOSTATS_GOOGLE_ALLOWED"
# Any of these set means sign-in with Google was asked for. APP_URL alone does not.
TOUCHED = (CLIENT_ID, CLIENT_SECRET, ALLOWED)

# The sign-in in progress: state, nonce, verifier and the page to come back to.
# __Host-: Secure, Path=/ and no Domain, so neither a sibling subdomain nor a
# plain-http answer can set it (browsers count http://localhost as secure).
FLOW_COOKIE = "__Host-randostats-oauth"
FLOW_SECONDS = 600
# Clock skew allowed on exp and iat.
SKEW_SECONDS = 60
TIMEOUT_SECONDS = 10.0
# Google's answer is a few kilobytes; more is not an answer.
RESPONSE_CAP = 64 * 1024
# The return path travels in the cookie, percent-encoded. A browser drops a cookie
# over 4096 bytes, so the encoded path is held to this, which keeps the cookie
# under 1,700; a longer one comes back to the site's root instead.
MAX_RETURN = 1024

# Google's sub: up to 255 case-sensitive ASCII characters, in practice digits.
SUBJECT = re.compile(r"[A-Za-z0-9_-]{1,255}")
# An address with no space or control character in it. It is only ever shown as
# text and compared, never used to send anything.
EMAIL = re.compile(r"[^\s@\x00-\x1f\x7f]{1,64}@[^\s@\x00-\x1f\x7f]{1,255}")
CLIENT_ID_SHAPE = re.compile(r"[A-Za-z0-9._-]{1,200}\.apps\.googleusercontent\.com")
CLIENT_SECRET_SHAPE = re.compile(r"[\x21-\x7e]{10,200}")
B64URL = re.compile(r"[A-Za-z0-9_-]+")
LOCAL_NAMES = frozenset({"localhost", "127.0.0.1", "::1"})

# (url, form body, timeout in seconds) -> (status, response body)
Fetch = Callable[[str, bytes, float], tuple[int, bytes]]


class Refused(Exception):
    """Why a sign-in was turned away: one of this module's own words, never text
    from Google, so it can be logged."""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


# -- configuration ---------------------------------------------------------------

@dataclass(frozen=True)
class GoogleConfig:
    app_url: str  # scheme://host[:port]/ - the site's root, with the slash
    client_id: str
    client_secret: str = field(repr=False)
    subs: frozenset = frozenset()
    emails: frozenset = frozenset()

    @property
    def redirect_uri(self) -> str:
        return self.app_url + CALLBACK[1:]

    @property
    def hostname(self) -> str:
        return urlsplit(self.app_url).hostname or ""

    @property
    def secure(self) -> bool:
        return self.app_url.startswith("https:")

    def allows(self, identity: Identity) -> bool:
        """On the list by account id, or by an email Google says is verified."""
        if identity.sub in self.subs:
            return True
        return identity.email is not None and identity.email.lower() in self.emails


def parse_app_url(text: str) -> str | None:
    """The site's root as ``scheme://host[:port]/``, or None when ``text`` is not one.

    https, or http for this machine's own names (Google takes an http redirect URI
    for localhost alone). No path: every address the front end writes is
    root-absolute, so randostats runs at the root of its own origin. No user,
    query or fragment either.
    """
    # What comes back is rebuilt from the parsed parts, so nothing the parser drops
    # (a tab, a newline) can reach the redirect URI; the host is held to an exact
    # name or address (security.allowed_hostname) like every name this server serves.
    try:
        parts = urlsplit(text)
        port = parts.port
        host = allowed_hostname(parts.hostname or "")
    except ValueError:
        return None
    if parts.username is not None or parts.password is not None or parts.query or parts.fragment:
        return None
    if parts.path not in ("", "/") or "?" in text or "#" in text:
        return None
    if parts.scheme == "http":
        if host not in LOCAL_NAMES:
            return None
    elif parts.scheme != "https":
        return None
    shown = f"[{host}]" if ":" in host else host
    return f"{parts.scheme}://{shown}{f':{port}' if port is not None else ''}/"


def read_config(environ: Mapping[str, str]) -> GoogleConfig | None:
    """Sign-in with Google as the environment sets it up, or None when it does not ask for it.

    Raises ValueError naming every variable that is missing or malformed (never a
    value) when it asks for it and does not hold up: a half set-up sign-in refuses
    to start rather than run with a gate that opens for nobody, or with none.
    """
    raw = {name: (environ.get(name) or "").strip() for name in (APP_URL, *TOUCHED)}
    if not any(raw[name] for name in TOUCHED):
        return None
    missing = [name for name in (APP_URL, *TOUCHED) if not raw[name]]
    malformed = []
    app_url = parse_app_url(raw[APP_URL]) if raw[APP_URL] else None
    if raw[APP_URL] and app_url is None:
        malformed.append(f"{APP_URL} (the site's root over https, or http on localhost; no path)")
    if raw[CLIENT_ID] and not CLIENT_ID_SHAPE.fullmatch(raw[CLIENT_ID]):
        malformed.append(f"{CLIENT_ID} (…apps.googleusercontent.com)")
    if raw[CLIENT_SECRET] and not CLIENT_SECRET_SHAPE.fullmatch(raw[CLIENT_SECRET]):
        malformed.append(CLIENT_SECRET)
    subs, emails, bad = set(), set(), 0
    for entry in re.split(r"[\s,]+", raw[ALLOWED]):
        if not entry:
            continue
        if "@" in entry:
            if EMAIL.fullmatch(entry):
                emails.add(entry.lower())
                continue
        elif SUBJECT.fullmatch(entry):
            subs.add(entry)
            continue
        bad += 1
    if raw[ALLOWED] and bad:
        malformed.append(f"{ALLOWED} ({bad} entr{'y is' if bad == 1 else 'ies are'} neither a Google "
                         "account id nor an email address)")
    elif raw[ALLOWED] and not subs and not emails:
        malformed.append(f"{ALLOWED} (it lists nobody)")
    if missing or malformed:
        problems = [f"missing {name}" for name in missing] + [f"malformed {what}" for what in malformed]
        raise ValueError("sign-in with Google is not fully set up: " + "; ".join(problems))
    assert app_url is not None
    return GoogleConfig(app_url, raw[CLIENT_ID], raw[CLIENT_SECRET], frozenset(subs), frozenset(emails))


# -- the flow --------------------------------------------------------------------

@dataclass(frozen=True)
class Identity:
    sub: str
    email: str | None = None


@dataclass(frozen=True)
class Flow:
    """What the flow cookie carried, once its signature, expiry and state held."""

    nonce: str
    verifier: str
    back: str


def b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def from_b64url(text: str) -> bytes | None:
    if not B64URL.fullmatch(text):
        return None
    try:
        return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))
    except ValueError:
        return None


def read_cookie(header: str | None, name: str) -> str | None:
    """The value of cookie ``name`` in a Cookie header, its name read exactly.

    A cookie name is trimmed of spaces and tabs only (RFC 6265's whitespace).
    Starlette's parser trims with ``str.strip()``, which also takes off U+00A0 and
    the other Unicode spaces, so it would read a cookie named ``\\xa0__Host-…``,
    which a browser treats as another name entirely and lets a sibling subdomain or
    a plain-http answer set, as the ``__Host-`` one it may not. Hygiene: the flow
    cookie is signed, and a planted one opens the site only to an account already on
    the list; this keeps the prefix meaning what it says.
    """
    for part in (header or "").split(";"):
        key, eq, value = part.partition("=")
        if eq and key.strip(" \t") == name:
            return value.strip(" \t")
    return None


def return_path(value: object) -> str:
    """Where to send the browser once it is signed in, percent-encoded for the cookie
    and the Location header: a path here (auth.local_path), else the site's root."""
    back = quote(local_path(value), safe="/?#[]@!$&'()*+,;=:%-._~")
    return back if len(back) <= MAX_RETURN else "/"


def post_form(url: str, body: bytes, timeout: float) -> tuple[int, bytes]:
    """POST a form over HTTPS with the default SSL context, following no redirect.

    The default context verifies the certificate chain against the system's roots
    and the host name: the TLS server validation OIDC Core 3.1.3.7 item 6 rests on.
    """
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *args, **kwargs):
            return None

    opener = urllib.request.build_opener(NoRedirect, urllib.request.HTTPSHandler(context=ssl.create_default_context()))
    request = urllib.request.Request(url, data=body, method="POST", headers={
        "Content-Type": "application/x-www-form-urlencoded", "Accept": "application/json"})
    try:
        with opener.open(request, timeout=timeout) as response:
            return response.status, response.read(RESPONSE_CAP + 1)
    except urllib.error.HTTPError as error:
        error.close()
        return error.code, b""


def _seconds(value: object) -> float | None:
    """A JSON number as seconds, or None for anything else (true is not a number)."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        seconds = float(value)
    except OverflowError:
        return None
    return seconds if math.isfinite(seconds) else None


def _same(a: str, b: str) -> bool:
    return hmac.compare_digest(a.encode("utf-8", "surrogatepass"), b.encode("utf-8", "surrogatepass"))


def check_claims(claims: object, *, client_id: str, nonce: str, now: float) -> Identity:
    """Who the ID token names, once every claim holds (OIDC Core 3.1.3.7)."""
    if not isinstance(claims, dict):
        raise Refused("format")
    if not isinstance(claims.get("iss"), str) or claims["iss"] not in ISSUERS:
        raise Refused("iss")
    # The client id itself: an audience list, even one naming it, is not.
    if claims.get("aud") != client_id:
        raise Refused("aud")
    if "azp" in claims and claims["azp"] != client_id:
        raise Refused("azp")
    exp = _seconds(claims.get("exp"))
    if exp is None or now >= exp + SKEW_SECONDS:
        raise Refused("exp")
    iat = _seconds(claims.get("iat"))
    if iat is None or iat > now + SKEW_SECONDS:
        raise Refused("iat")
    if not isinstance(claims.get("nonce"), str) or not _same(claims["nonce"], nonce):
        raise Refused("nonce")
    sub = claims.get("sub")
    if not isinstance(sub, str) or not SUBJECT.fullmatch(sub):
        raise Refused("sub")
    email = claims.get("email")
    if claims.get("email_verified") is True and isinstance(email, str) and EMAIL.fullmatch(email):
        return Identity(sub, email)
    return Identity(sub)


def read_id_token(id_token: str) -> object:
    """The claims of a compact JWS, unverified: see the module's docstring for why
    that is right for a token this module fetched from Google's endpoint itself."""
    parts = id_token.split(".")
    if len(parts) != 3:
        raise Refused("format")
    payload = from_b64url(parts[1])
    try:
        return json.loads(payload.decode("utf-8")) if payload is not None else None
    except (UnicodeDecodeError, ValueError):
        raise Refused("format") from None


class GoogleSignIn:
    """One configured sign-in: the flow cookie's key, and how Google's token endpoint is reached."""

    def __init__(self, config: GoogleConfig, fetch: Fetch | None = None, clock=time.time):
        self.config = config
        # Drawn when the process starts, like the session key in auth.py: a restart
        # ends a sign-in in progress as it ends the sessions.
        self._key = secrets.token_bytes(32)
        self._fetch = fetch or post_form
        self._clock = clock

    def _mac(self, payload: str) -> str:
        return b64url(hmac.new(self._key, b"randostats.oauth." + payload.encode("ascii"), hashlib.sha256).digest())

    def start(self, return_to: object) -> tuple[str, str]:
        """(the address to send the browser to, the flow cookie's value)."""
        state, nonce, verifier = secrets.token_urlsafe(32), secrets.token_urlsafe(32), secrets.token_urlsafe(32)
        challenge = b64url(hashlib.sha256(verifier.encode("ascii")).digest())
        payload = b64url(json.dumps({"s": state, "n": nonce, "v": verifier, "r": return_path(return_to),
                                     "e": int(self._clock()) + FLOW_SECONDS}, separators=(",", ":")).encode("ascii"))
        location = AUTHORIZE_URL + "?" + urlencode({
            "response_type": "code",
            "client_id": self.config.client_id,
            "redirect_uri": self.config.redirect_uri,
            "scope": "openid email",
            "state": state,
            "nonce": nonce,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
        })
        return location, f"{payload}.{self._mac(payload)}"

    def check(self, cookie: str | None, params: Mapping[str, str]) -> Flow:
        """The sign-in Google is answering, if the cookie and the state say it is ours.

        Google's ``error`` is refused before anything else and its text goes nowhere.
        The cookie is held to this process's signature and its own expiry, and the
        state Google hands back to the one it carries, in constant time: the state is
        what stops another site from finishing a sign-in it started in this browser.
        """
        if "error" in params:
            raise Refused("error")
        if not cookie:
            raise Refused("cookie")
        payload, dot, mac = cookie.partition(".")
        if not dot or not payload.isascii() or not _same(mac, self._mac(payload)):
            raise Refused("cookie")
        # Signed by this process, so it is the JSON start() wrote: nothing to check
        # but whether it has run out.
        flow = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
        if self._clock() >= flow["e"]:
            raise Refused("expired")
        state = params.get("state")
        if not isinstance(state, str) or not _same(state, flow["s"]):
            raise Refused("state")
        return Flow(nonce=flow["n"], verifier=flow["v"], back=flow["r"])

    def redeem(self, flow: Flow, code: str | None) -> Identity:
        """Trade the code for an ID token at Google's token endpoint, and read who it names."""
        if not code:  # none, or empty: nothing to ask Google
            raise Refused("code")
        body = urlencode({
            "code": code,
            "client_id": self.config.client_id,
            "client_secret": self.config.client_secret,
            "redirect_uri": self.config.redirect_uri,
            "grant_type": "authorization_code",
            "code_verifier": flow.verifier,
        }).encode("ascii")
        try:
            status, answer = self._fetch(TOKEN_URL, body, TIMEOUT_SECONDS)
        except (OSError, ValueError, http.client.HTTPException):  # unreachable, timed out, TLS refused
            raise Refused("token") from None
        if status != 200 or len(answer) > RESPONSE_CAP:
            raise Refused("token")
        try:
            data = json.loads(answer.decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            raise Refused("token") from None
        id_token = data.get("id_token") if isinstance(data, dict) else None
        if not isinstance(id_token, str):
            raise Refused("token")
        return check_claims(read_id_token(id_token), client_id=self.config.client_id,
                            nonce=flow.nonce, now=self._clock())
