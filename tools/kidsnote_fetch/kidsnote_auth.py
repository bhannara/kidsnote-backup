"""Kidsnote login helper — stdlib only.

Why this exists: kidsnote's `sessionid` cookie expires 14 days after login
(Set-Cookie header, verified 2026-09-14), so a cookie pasted into a repo
secret goes stale and every cron run fails until someone re-extracts it.
The web login page is a Next.js SPA, but underneath it calls a plain JSON
API that a headless client can drive, so each run can log in fresh.

Login flow, mirrored from the SPA's login chunk (2026-09-14):
  1. GET  /api/v1/second-factors/<username>/  -> {is_enabled, second_factors}
     With 2-step verification on, a phone/email code is required, which
     can't be automated -> fall back to KIDSNOTE_SESSION_COOKIE.
  2. POST /api/web/login/ {username, password, remember_me}
     -> 200 + Set-Cookie sessionid. No CSRF token needed. Logging in does
     not invalidate the account's other sessions (browser / app).

Stdlib-only on purpose: the workflow runs this before `pip install`, so a
failed login ends the run in seconds without installing anything.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from http.cookiejar import CookieJar

KIDSNOTE_BASE = "https://www.kidsnote.com"
DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/128.0 Safari/537.36"
)

# Shown in the Actions log / failure annotation. Plain Korean for parents.
HINTS = {
    "invalid_credentials": "키즈노트 아이디 또는 비밀번호가 틀렸습니다. "
                           "KIDSNOTE_USERNAME / KIDSNOTE_PASSWORD 시크릿을 확인해 주세요.",
    "blocked": "로그인 실패가 반복돼 키즈노트 계정이 잠겼습니다. 키즈노트에서 비밀번호를 "
               "재설정한 뒤 KIDSNOTE_PASSWORD 시크릿을 새 비밀번호로 바꿔 주세요.",
    "2fa_enabled": "키즈노트 계정에 2단계 인증이 켜져 있어 자동 로그인을 할 수 없습니다. "
                   "2단계 인증을 끄거나 KIDSNOTE_SESSION_COOKIE 시크릿을 사용해 주세요.",
    "session_expired": "KIDSNOTE_SESSION_COOKIE가 만료됐습니다(로그인 후 14일). "
                       "KIDSNOTE_USERNAME / KIDSNOTE_PASSWORD 시크릿을 등록하면 더 이상 만료 걱정이 없습니다.",
    "missing": "키즈노트 로그인 정보가 없습니다. "
               "KIDSNOTE_USERNAME / KIDSNOTE_PASSWORD 시크릿을 등록해 주세요.",
    "network": "키즈노트 서버에 접속하지 못했습니다. 일시적인 문제라면 다음 실행에서 자동으로 다시 시도합니다.",
    "unexpected": "키즈노트 로그인 응답이 예상과 다릅니다. 키즈노트 로그인 방식이 바뀌었을 수 있습니다.",
}


class AuthError(RuntimeError):
    """Login failed. ``reason`` is one of the HINTS keys."""

    def __init__(self, reason: str, message: str) -> None:
        super().__init__(message)
        self.reason = reason


def _opener(user_agent: str, jar: CookieJar | None = None) -> urllib.request.OpenerDirector:
    handlers = [urllib.request.HTTPCookieProcessor(jar)] if jar is not None else []
    opener = urllib.request.build_opener(*handlers)
    opener.addheaders = [
        ("User-Agent", user_agent),
        ("Accept", "application/json, text/plain, */*"),
        ("Accept-Language", "ko"),
    ]
    return opener


def _request(
    opener: urllib.request.OpenerDirector,
    method: str,
    path: str,
    *,
    body: dict | None = None,
    cookie: str = "",
    timeout: float = 20,
) -> tuple[int, bytes]:
    data = None if body is None else json.dumps(body).encode("utf-8")
    req = urllib.request.Request(KIDSNOTE_BASE + path, data=data, method=method)
    if data is not None:
        req.add_header("Content-Type", "application/json")
        req.add_header("Origin", KIDSNOTE_BASE)
        req.add_header("Referer", f"{KIDSNOTE_BASE}/kr/login")
    if cookie:
        req.add_header("Cookie", f"sessionid={cookie}")
    try:
        with opener.open(req, timeout=timeout) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()
    except OSError as e:  # URLError, timeouts, DNS
        raise AuthError("network", f"kidsnote.com unreachable: {e}") from e


def _json(raw: bytes) -> dict:
    try:
        data = json.loads(raw.decode("utf-8") or "{}")
    except (UnicodeDecodeError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def login(username: str, password: str, user_agent: str = DEFAULT_USER_AGENT) -> str:
    """Log in with id/password and return the new ``sessionid`` value."""
    jar = CookieJar()
    opener = _opener(user_agent, jar)

    quoted = urllib.parse.quote(username, safe="")
    status, raw = _request(opener, "GET", f"/api/v1/second-factors/{quoted}/")
    if status == 200:
        info = _json(raw)
        if info.get("is_enabled") and info.get("second_factors"):
            raise AuthError("2fa_enabled", "2-step verification is enabled on this account")
    # Any other status is not fatal: the SPA only uses this call to decide
    # whether to show the 2FA form, and the login call below reports errors.

    status, raw = _request(
        opener, "POST", "/api/web/login/",
        body={"username": username, "password": password, "remember_me": True},
    )
    if status != 200:
        if _json(raw).get("err_code") == "blocked":
            raise AuthError("blocked", f"HTTP {status}: account blocked")
        if status in (400, 401, 403, 404):
            raise AuthError("invalid_credentials", f"HTTP {status} on /api/web/login/")
        raise AuthError("unexpected", f"HTTP {status} on /api/web/login/")

    sessionid = next((c.value for c in jar if c.name == "sessionid"), "")
    if not sessionid:
        raise AuthError("unexpected", "login returned 200 but set no sessionid cookie")
    return sessionid


def session_is_valid(sessionid: str, user_agent: str = DEFAULT_USER_AGENT) -> bool:
    """True if ``sessionid`` can read /api/v1/me/children/."""
    status, _ = _request(_opener(user_agent), "GET", "/api/v1/me/children/", cookie=sessionid)
    if status == 200:
        return True
    if status in (401, 403):
        return False
    raise AuthError("unexpected", f"HTTP {status} on /api/v1/me/children/")


def resolve_session(
    username: str,
    password: str,
    cookie: str,
    user_agent: str = DEFAULT_USER_AGENT,
) -> tuple[str, str]:
    """Return ``(sessionid, source)`` where source is ``login`` or ``cookie``.

    Prefers a fresh login; falls back to the KIDSNOTE_SESSION_COOKIE value.
    Raises AuthError (the login error first, since it is the actionable one).
    """
    login_error: AuthError | None = None
    if username and password:
        try:
            sessionid = login(username, password, user_agent)
            if session_is_valid(sessionid, user_agent):
                return sessionid, "login"
            login_error = AuthError("unexpected", "fresh login session was rejected")
        except AuthError as e:
            login_error = e
    if cookie:
        try:
            if session_is_valid(cookie, user_agent):
                return cookie, "cookie"
            cookie_error = AuthError("session_expired", "KIDSNOTE_SESSION_COOKIE is expired")
        except AuthError as e:
            cookie_error = e
        raise login_error or cookie_error
    raise login_error or AuthError("missing", "no kidsnote credentials configured")


def _github_append(var: str, line: str) -> None:
    path = os.environ.get(var)
    if not path:
        raise SystemExit(f"--github needs ${var}; run it inside GitHub Actions")
    with open(path, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Log in to kidsnote and verify the session.")
    ap.add_argument(
        "--github", action="store_true",
        help="GitHub Actions mode: export the session to $GITHUB_ENV (masked) and "
             "write ok/reason/hint to $GITHUB_OUTPUT instead of exiting non-zero.",
    )
    args = ap.parse_args(argv)

    try:
        sessionid, source = resolve_session(
            os.environ.get("KIDSNOTE_USERNAME", "").strip(),
            os.environ.get("KIDSNOTE_PASSWORD", ""),
            os.environ.get("KIDSNOTE_SESSION_COOKIE", "").strip(),
        )
    except AuthError as e:
        reason, detail = e.reason, str(e)
    except Exception as e:  # surface as a login failure so it is reported once, not crash-looped
        reason, detail = "unexpected", f"{type(e).__name__}: {e}"
    else:
        print(f"Kidsnote login OK ({'fresh login' if source == 'login' else 'KIDSNOTE_SESSION_COOKIE'})")
        if args.github:
            print(f"::add-mask::{sessionid}")
            _github_append("GITHUB_ENV", f"KIDSNOTE_SESSION_COOKIE={sessionid}")
            _github_append("GITHUB_OUTPUT", "ok=true")
        return 0

    hint = HINTS.get(reason, HINTS["unexpected"])
    print(f"Kidsnote login FAILED [{reason}] {detail}\n{hint}")
    if args.github:
        _github_append("GITHUB_OUTPUT", "ok=false")
        _github_append("GITHUB_OUTPUT", f"reason={reason}")
        _github_append("GITHUB_OUTPUT", f"hint={hint}")
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
