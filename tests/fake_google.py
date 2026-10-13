"""A stand-in for Google's side of "Sign in with Google", for the tests.

Two halves, as Google has two: ``authorize`` is the consent screen (it turns the
address the server sent the browser to into the address Google sends it back to),
and ``FakeGoogle`` is the token endpoint the server calls. The tests hand a
``FakeGoogle`` to ``create_app(fetch=...)``; the browser walk's server gets one the
same way (e2e/google_server.py). Nothing points the app at another host: google.py's
addresses are constants, and this answers the one the server calls.

The code Google hands back is the grant itself, written out, so the consent screen
(in a unit test, or answering the browser at the protocol in the walk) and the token
endpoint (in the server) share nothing but this file. The token endpoint checks what
Google checks: the client and its secret, the redirect address, the PKCE verifier
against the challenge, and a code used once.
"""

from __future__ import annotations

import base64
import hashlib
import json
import time
from urllib.parse import parse_qs, urlencode, urlsplit

from randostats import google

CLIENT_ID = "randostats-test.apps.googleusercontent.com"
CLIENT_SECRET = "test-client-secret-0123"

OWNER = {"sub": "100000000000000000001", "email": "Owner@Example.com", "email_verified": True}
STRANGER = {"sub": "100000000000000000002", "email": "stranger@example.com", "email_verified": True}


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def config(app_url: str = "http://localhost/", subs=(OWNER["sub"],), emails=()) -> google.GoogleConfig:
    """A sign-in set up for the tests' client."""
    return google.GoogleConfig(app_url, CLIENT_ID, CLIENT_SECRET, frozenset(subs),
                               frozenset(e.lower() for e in emails))


def authorize(location: str, account: dict, claims: dict | None = None, client_id: str = CLIENT_ID) -> str:
    """Where Google sends the browser once ``account`` agrees. ``claims`` are written
    into the ID token over the ones Google would give, to stage a bad one. Raises on
    a request Google would refuse to show."""
    url = urlsplit(location)
    params = {k: v[0] for k, v in parse_qs(url.query).items()}
    if f"{url.scheme}://{url.netloc}{url.path}" != google.AUTHORIZE_URL:
        raise ValueError(f"not Google's authorize address: {location}")
    if params.get("response_type") != "code":
        raise ValueError("response_type is not code")
    if params.get("client_id") != client_id:
        raise ValueError("an unknown client")
    if "openid" not in params.get("scope", "").split():
        raise ValueError("the scope has no openid")
    if params.get("code_challenge_method") != "S256" or not params.get("code_challenge"):
        raise ValueError("no S256 code challenge")
    for name in ("redirect_uri", "state", "nonce"):
        if not params.get(name):
            raise ValueError(f"no {name}")
    grant = {"challenge": params["code_challenge"], "redirect_uri": params["redirect_uri"],
             "nonce": params["nonce"], "account": account, "claims": claims or {}}
    code = _b64(json.dumps(grant).encode())
    return params["redirect_uri"] + "?" + urlencode({"code": code, "state": params["state"]})


class FakeGoogle:
    """Google's token endpoint, as ``google.Fetch``: (url, body, timeout) -> (status, body).

    ``answer``, when set, is returned instead (status, body), to stage an endpoint
    that fails; ``raises`` is raised instead, to stage one that cannot be reached.
    """

    def __init__(self, client_id: str = CLIENT_ID, client_secret: str = CLIENT_SECRET, now=time.time):
        self.client_id, self.client_secret, self.now = client_id, client_secret, now
        self.calls: list[dict] = []
        self.used: set[str] = set()
        self.answer: tuple[int, bytes] | None = None
        self.raises: BaseException | None = None

    def __call__(self, url: str, body: bytes, timeout: float) -> tuple[int, bytes]:
        form = {k: v[0] for k, v in parse_qs(body.decode("ascii")).items()}
        self.calls.append({"url": url, "form": form, "timeout": timeout})
        if self.raises is not None:
            raise self.raises
        if self.answer is not None:
            return self.answer
        if url != google.TOKEN_URL:
            return 404, b"{}"
        refused = (400, json.dumps({"error": "invalid_grant"}).encode())
        if form.get("grant_type") != "authorization_code" or form.get("client_id") != self.client_id:
            return 401, json.dumps({"error": "invalid_client"}).encode()
        if form.get("client_secret") != self.client_secret:
            return 401, json.dumps({"error": "invalid_client"}).encode()
        code = form.get("code", "")
        if code in self.used:
            return refused
        try:
            grant = json.loads(_unb64(code))
        except ValueError:
            return refused
        self.used.add(code)
        if form.get("redirect_uri") != grant["redirect_uri"]:
            return refused
        verifier = form.get("code_verifier", "")
        if _b64(hashlib.sha256(verifier.encode("ascii")).digest()) != grant["challenge"]:
            return refused
        now = int(self.now())
        account = grant["account"]
        claims = {"iss": "https://accounts.google.com", "azp": self.client_id, "aud": self.client_id,
                  "sub": account["sub"], "iat": now, "exp": now + 3600, "nonce": grant["nonce"]}
        if "email" in account:
            claims["email"] = account["email"]
            claims["email_verified"] = account.get("email_verified", False)
        if "hd" in account:  # a Google Workspace account: its domain
            claims["hd"] = account["hd"]
        for key, value in grant["claims"].items():
            if value is None:
                claims.pop(key, None)
            else:
                claims[key] = value
        header = {"alg": "RS256", "kid": "fake-google-1", "typ": "JWT"}
        # The signature is not read (OIDC Core 3.1.3.7 item 6; google.py says why),
        # so this one is a placeholder of the right shape.
        id_token = ".".join([_b64(json.dumps(header).encode()), _b64(json.dumps(claims).encode()),
                             _b64(b"not a real signature")])
        return 200, json.dumps({"access_token": "ya29.fake", "expires_in": 3599, "scope": "openid email",
                                "token_type": "Bearer", "id_token": id_token}).encode()
