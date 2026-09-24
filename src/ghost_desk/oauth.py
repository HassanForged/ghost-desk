"""Device-code login shared by every subscription adapter.

Adapters pass constants only. Tokens are written to the data directory at mode 0600.
They are not printed, logged, or returned in error text.
"""

from __future__ import annotations

import base64
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass


@dataclass
class OAuthSpec:
    provider: str
    client_id: str
    scope: str
    device_url: str
    token_url: str
    flow: str = "rfc8628"
    poll_url: str = ""
    verification_url: str = ""
    redirect_uri: str = ""


@dataclass
class TokenSet:
    access_token: str
    refresh_token: str
    expires_at: float
    account_id: str = ""

    def expired(self, now: float | None = None) -> bool:
        return (now if now is not None else time.time()) >= self.expires_at

    def __repr__(self) -> str:
        return "TokenSet(stored)"


class OAuthError(RuntimeError):
    pass


def secure_write(path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
    if os.name == "nt":
        fd = os.open(str(path), flags)
    else:
        fd = os.open(str(path), flags, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(text)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    if os.name == "nt":
        _restrict_windows(path)


def _restrict_windows(path) -> None:
    user = os.environ.get("USERNAME") or ""
    if not user:
        return
    import subprocess

    subprocess.run(
        ["icacls", str(path), "/inheritance:r", "/grant:r", f"{user}:(R,W)"],
        capture_output=True,
        check=False,
    )


def is_private(path) -> bool:
    """True when the token file is owner-only. Group and world are not granted."""
    if not path.is_file():
        return False
    if os.name != "nt":
        return (os.stat(path).st_mode & 0o077) == 0
    import subprocess

    done = subprocess.run(["icacls", str(path)], capture_output=True, text=True)
    if done.returncode != 0:
        return False
    lowered = (done.stdout or "").lower()
    if "everyone:" in lowered or "builtin\\users:" in lowered:
        return False
    return True


def _token_state(tokens: TokenSet, auth_mode: str) -> dict:
    return {
        "auth_mode": auth_mode or "oauth_device_code",
        "last_refresh": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "tokens": {
            "access_token": tokens.access_token,
            "refresh_token": tokens.refresh_token,
            "expires_at": tokens.expires_at,
            "expires_in": 3600,
            "token_type": "Bearer",
            "account_id": tokens.account_id,
        },
    }


def save_tokens(path, tokens: TokenSet, provider: str = "", auth_mode: str = "oauth_device_code") -> None:
    """Hermes-shaped store: version, active provider, per-provider token state. Mode 0600."""
    slug = provider or "oauth"
    providers: dict = {}
    if path.is_file():
        try:
            current = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            current = {}
        if isinstance(current, dict) and isinstance(current.get("providers"), dict):
            providers = dict(current["providers"])
        elif isinstance(current, dict) and (current.get("access_token") or current.get("key")):
            old = str(current.get("provider") or "imported")
            picked = _from_state(current)
            if picked is not None:
                providers[old] = _token_state(picked, "oauth_device_code")
    providers[slug] = _token_state(tokens, auth_mode)
    payload = {"version": 1, "active_provider": slug, "providers": providers}
    secure_write(path, json.dumps(payload) + "\n")


def _from_state(state: dict | None) -> TokenSet | None:
    if not isinstance(state, dict):
        return None
    nested = state.get("tokens")
    tokens = nested if isinstance(nested, dict) else state
    access = str(tokens.get("access_token") or tokens.get("key") or "")
    if not access:
        return None
    try:
        expires = float(tokens.get("expires_at") or 0)
    except (TypeError, ValueError):
        expires = 0
    if expires > 10_000_000_000:
        expires = expires / 1000
    return TokenSet(
        access,
        str(tokens.get("refresh_token") or ""),
        expires,
        str(tokens.get("account_id") or ""),
    )


def load_tokens(path, provider: str | None = None) -> TokenSet | None:
    if not path.is_file():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(raw, dict):
        return None
    providers = raw.get("providers")
    if isinstance(providers, dict):
        key = provider or str(raw.get("active_provider") or "")
        return _from_state(providers.get(key) if isinstance(providers.get(key), dict) else None)
    saved = str(raw.get("provider") or "")
    if provider and saved and saved != provider:
        return None
    return _from_state(raw)


def read_external_tokens(path) -> TokenSet | None:
    """Read a Grok CLI auth.json. Accepts the issuer map, a flat token, or a nested tokens object."""
    if path is None or not path.is_file():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(raw, dict):
        return None
    found: list[dict] = [raw]
    for value in raw.values():
        if isinstance(value, dict):
            found.append(value)
            nested = value.get("tokens")
            if isinstance(nested, dict):
                found.append(nested)
    for item in found:
        picked = _from_state(item)
        if picked is not None and picked.refresh_token:
            return picked
    return None


def read_claude_setup_token(path) -> str:
    """Claude Code credential file. Returns the access token, or an empty string."""
    if path is None or not path.is_file():
        return ""
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return ""
    if not isinstance(raw, dict):
        return ""
    block = raw.get("claudeAiOauth") or raw.get("claudeAiOAuth") or {}
    if isinstance(block, dict):
        token = str(block.get("accessToken") or block.get("access_token") or "")
        if token:
            return token
    return str(raw.get("access_token") or raw.get("CLAUDE_CODE_OAUTH_TOKEN") or "")


def account_id_from_id_token(id_token: str) -> str:
    """Read the ChatGPT account id out of a JWT payload. The signature is not checked."""
    if not id_token or id_token.count(".") < 2:
        return ""
    payload = id_token.split(".")[1]
    pad = "=" * (-len(payload) % 4)
    try:
        data = json.loads(base64.urlsafe_b64decode(payload + pad))
    except (ValueError, json.JSONDecodeError):
        return ""
    if not isinstance(data, dict):
        return ""
    auth = data.get("https://api.openai.com/auth")
    if isinstance(auth, dict):
        for key in ("chatgpt_account_id", "account_id"):
            if auth.get(key):
                return str(auth[key])
    for key in ("chatgpt_account_id", "account_id"):
        if data.get(key):
            return str(data[key])
    return ""


def _post(url: str, fields: dict, *, json_body: bool = False, http=None) -> dict:
    if http is not None:
        result = http("POST", url, fields)
        return result if isinstance(result, dict) else {}
    if json_body:
        data = json.dumps(fields).encode()
        content_type = "application/json"
    else:
        data = urllib.parse.urlencode(fields).encode()
        content_type = "application/x-www-form-urlencoded"
    request = urllib.request.Request(
        url,
        data=data,
        headers={
            "Content-Type": content_type,
            "Accept": "application/json",
            "User-Agent": "ghost-desk",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return _body(response.read().decode(), response.status)
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode(errors="replace")
        body = _body(raw, exc.code)
        if "error" not in body:
            body["error"] = f"http {exc.code}"
        return body
    except urllib.error.URLError as exc:
        return {"status": 0, "error": "network"}


def _body(raw: str, status: int) -> dict:
    try:
        loaded = json.loads(raw) if raw else {}
    except json.JSONDecodeError:
        loaded = {}
    if not isinstance(loaded, dict):
        loaded = {}
    loaded["status"] = status
    return loaded


def _show_code(output, url: str, code: str, browser) -> None:
    output("Open this URL in your LOCAL browser and enter the code below.")
    output(f"URL: {url}")
    output(f"Code: {code}")
    output("Code expires in 15 minutes. Never share it.")
    output("Waiting for authorization...")
    if browser is None or not url:
        return
    try:
        browser(url)
    except Exception:
        return


def device_login(
    spec: OAuthSpec,
    *,
    output,
    http=None,
    sleep=None,
    clock=None,
    max_polls: int | None = None,
    browser=None,
) -> TokenSet:
    """Print the code once, poll until approved, stop on timeout, denial, or 403."""
    if spec.flow == "openai_device":
        return _openai_device(
            spec,
            output=output,
            http=http,
            sleep=sleep,
            clock=clock,
            max_polls=180 if max_polls is None else max_polls,
            browser=browser,
        )
    return _rfc_device(
        spec,
        output=output,
        http=http,
        sleep=sleep,
        clock=clock,
        max_polls=90 if max_polls is None else max_polls,
        browser=browser,
    )


def _rfc_device(spec, *, output, http, sleep, clock, max_polls: int, browser) -> TokenSet:
    sleep = sleep or time.sleep
    clock = clock or time.time
    started = _post(
        spec.device_url,
        {"client_id": spec.client_id, "scope": spec.scope},
        http=http,
    )
    if int(started.get("status") or 200) == 403 or started.get("error"):
        raise OAuthError(str(started.get("error") or "login returned 403"))
    code = str(started.get("user_code") or "")
    url = str(started.get("verification_uri") or started.get("verification_url") or "")
    device_code = str(started.get("device_code") or "")
    if not device_code or not url:
        raise OAuthError("device login did not return a code")
    _show_code(output, url, code, browser)
    interval = max(1.0, float(started.get("interval") or 5))
    deadline = clock() + float(started.get("expires_in") or 300)
    for _ in range(max_polls):
        if clock() >= deadline:
            break
        sleep(interval)
        token = _post(
            spec.token_url,
            {
                "client_id": spec.client_id,
                "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
                "device_code": device_code,
            },
            http=http,
        )
        if token.get("access_token"):
            return _tokens(token, clock)
        error = str(token.get("error") or "")
        if error == "slow_down":
            interval += 5
            continue
        if error == "authorization_pending":
            continue
        if int(token.get("status") or 200) == 403:
            raise OAuthError("login returned 403")
        raise OAuthError(error or "login was not approved")
    raise OAuthError("login timed out")


def _openai_device(spec, *, output, http, sleep, clock, max_polls: int, browser) -> TokenSet:
    """Codex device grant: the poll returns a server-minted PKCE verifier, then the token endpoint."""
    sleep = sleep or time.sleep
    clock = clock or time.time
    started = _post(spec.device_url, {"client_id": spec.client_id}, json_body=True, http=http)
    status = int(started.get("status") or 200)
    if status == 403 or started.get("error"):
        raise OAuthError(str(started.get("error") or "login returned 403"))
    if status >= 400:
        raise OAuthError("device login did not return a code")
    device_auth_id = str(started.get("device_auth_id") or "")
    code = str(started.get("user_code") or started.get("usercode") or "")
    url = spec.verification_url or "https://auth.openai.com/codex/device"
    if not device_auth_id or not code:
        raise OAuthError("device login did not return a code")
    _show_code(output, url, code, browser)
    interval = max(1.0, float(started.get("interval") or 5))
    deadline = clock() + 15 * 60
    poll_url = spec.poll_url or spec.device_url
    grant: dict | None = None
    for _ in range(max_polls):
        if clock() >= deadline:
            break
        sleep(interval)
        polled = _post(
            poll_url,
            {"device_auth_id": device_auth_id, "user_code": code},
            json_body=True,
            http=http,
        )
        polled_status = int(polled.get("status") or 200)
        if polled_status in {403, 404} and not polled.get("authorization_code"):
            continue
        if polled.get("authorization_code") and polled.get("code_verifier"):
            grant = polled
            break
        if polled_status >= 400 or polled.get("error"):
            raise OAuthError(str(polled.get("error") or "login was not approved"))
    if grant is None:
        raise OAuthError("login timed out")
    token = _post(
        spec.token_url,
        {
            "grant_type": "authorization_code",
            "client_id": spec.client_id,
            "code": grant["authorization_code"],
            "code_verifier": grant["code_verifier"],
            "redirect_uri": spec.redirect_uri,
        },
        http=http,
    )
    if not token.get("access_token"):
        if int(token.get("status") or 200) == 403:
            raise OAuthError("login returned 403")
        raise OAuthError(str(token.get("error") or "login was not approved"))
    return _tokens(token, clock)


def _tokens(token: dict, clock) -> TokenSet:
    expires = clock() + float(token.get("expires_in") or 3600) - 60
    account = str(token.get("account_id") or "") or account_id_from_id_token(str(token.get("id_token") or ""))
    return TokenSet(
        str(token["access_token"]),
        str(token.get("refresh_token") or ""),
        expires,
        account,
    )


def refresh_token(spec: OAuthSpec, refresh: str, *, http=None, clock=None, account_id: str = "") -> TokenSet:
    clock = clock or time.time
    token = _post(
        spec.token_url,
        {
            "client_id": spec.client_id,
            "grant_type": "refresh_token",
            "refresh_token": refresh,
        },
        http=http,
    )
    if not token.get("access_token"):
        if int(token.get("status") or 200) == 403:
            raise OAuthError("login returned 403")
        raise OAuthError(str(token.get("error") or "refresh failed"))
    fresh = _tokens(token, clock)
    if not fresh.account_id:
        fresh.account_id = account_id
    return fresh
