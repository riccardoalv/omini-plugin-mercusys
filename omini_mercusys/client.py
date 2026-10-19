"""Client for the local web interface (LuCI JSON API behind /cgi-bin/luci),
on the SDK's HTTP client: timeouts, retries and the SDK's errors. The
requests themselves are encrypted as the web interface does (crypto.py).

Signing in takes four requests and makes the unit drop other admin sessions,
so the session (AES key, signing key, ``stok`` and cookie) is kept in the
plugin's state folder (the SDK's ``Session``) and reused until the unit
rejects it. After a refused password the plugin waits 15 minutes before
signing in again (``LoginBackoff``): the unit locks the login past a number
of failed attempts.

Only ``read`` operations are sent, besides the login itself.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

import httpx
from omini_sdk import PluginError, log
from omini_sdk.http import Client as HttpClient
from omini_sdk.http import LoginBackoff, NotFound, Unauthorized, Unavailable
from omini_sdk.http import Session as SavedSession

from omini_mercusys.crypto import Session, rsa_encrypt

# As the web interface sends every request (even the urlencoded sign=&data=
# body): the unit picks how to read a request by this header.
JSON = {"Content-Type": "application/json"}

# Pause before asking a slow unit again.
RETRY_PAUSE_S = 2.0
# Every request is a POST, reads too: the SDK asks again after a refused
# connection (even the login: nothing reached the unit), and the plugin asks
# a read again this many times in all after a timeout or a cut answer (a
# read changes nothing; the login is never sent twice).
READ_TRIES = 3


class SessionExpired(Exception):
    pass


class LoginRefused(Unauthorized):
    """The unit refused the credentials: it counts the attempt, so the
    plugin waits before signing in again with the same ones."""


class Client(HttpClient):
    def __init__(
        self,
        host: str,
        username: str,
        password: str,
        verify_tls: bool = False,
        state_dir: Path | None = None,
        timeout: float = 30,
        transport: httpx.BaseTransport | None = None,
    ):
        super().__init__(
            host,
            name="the unit",
            verify_tls=verify_tls,
            timeout=timeout,
            backoff=RETRY_PAUSE_S,
            headers={"Accept": "*/*"},
            user_agent="omini-plugin-mercusys",
            follow_redirects=True,
            transport=transport,
        )
        self.http.headers["Referer"] = self.base + "/webpages/index.html"
        self.state_dir = state_dir
        self.saved = SavedSession(state_dir, self.base, username, password)
        self.backoff_login = LoginBackoff(state_dir, self.base, username, password, minutes=15)
        # Other credentials (or address) start over: load() is empty.
        self.session = Session(username, password, self.saved.load().get("session"))

    # --- state ------------------------------------------------------------------

    def _save(self) -> None:
        self.saved.save({"session": self.session.values()})

    def keep(self, name: str, data: Any) -> None:
        """The last answer of each call, to help adapt the parsers to other
        models and firmware (local only, in the plugin's state folder)."""
        if not self.state_dir:
            return
        try:
            d = self.state_dir / "pages"
            d.mkdir(parents=True, exist_ok=True)
            (d / f"{name}.json").write_text(json.dumps(data, indent=1, ensure_ascii=False))
        except OSError:
            pass

    # --- HTTP -------------------------------------------------------------------

    def _post(self, path: str, form: str, body: str) -> dict[str, Any]:
        tries = 1 if form == "login" else READ_TRIES
        for attempt in range(tries):
            try:
                r = self.send("POST", path, params={"form": form}, content=body, headers=JSON)
                break
            except Unavailable as e:
                # A unit busy for a moment: a read that timed out or was cut
                # short (an incomplete chunked body) is asked again.
                slow = isinstance(
                    e.__cause__,
                    (httpx.TimeoutException, httpx.RemoteProtocolError, httpx.ReadError),
                )
                if slow and attempt + 1 < tries:
                    log.info("%s did not answer %s, trying again", self.base, form)
                    time.sleep(RETRY_PAUSE_S)
                    continue
                raise
        if r.status_code in (401, 403):
            raise SessionExpired(str(r.status_code))
        # Named by its form, never by its path (it holds the session's stok).
        self.check(r, "POST", form)
        try:
            return r.json()
        except ValueError as e:
            raise PluginError(f"unexpected answer from {form}") from e

    def login(self) -> None:
        self.backoff_login.check()
        try:
            self._login()
        except LoginRefused as e:
            # Only a refused password: a unit out of reach, busy, of another
            # kind or with another admin signed in is asked again next time.
            self.backoff_login.refused(str(e))
            raise
        self.backoff_login.clear()

    def _login(self) -> None:
        s = self.session
        keys = self._post("/cgi-bin/luci/;stok=/login", "keys", '{"operation":"read"}')
        auth = self._post("/cgi-bin/luci/;stok=/login", "auth", '{"operation":"read"}')
        try:
            n, e = (int(x, 16) for x in keys["result"]["password"])
            s.sign_n, s.sign_e = (int(x, 16) for x in auth["result"]["key"])
            s.seq = int(auth["result"]["seq"])
        except (KeyError, TypeError, ValueError) as err:
            raise PluginError("this does not look like a Mercusys Halo or Deco unit") from err
        payload = {
            "params": {"password": rsa_encrypt(n, e, s.password.encode())},
            "operation": "login",
        }
        self.http.cookies.clear()
        try:
            answer = self._post(
                "/cgi-bin/luci/;stok=/login", "login", s.encode(payload, login=True)
            )
        except SessionExpired as err:
            raise PluginError(
                "the unit refused the login: another device may be signed in as admin"
            ) from err
        data = s.decode(answer.get("data"))
        self.keep(
            "login",
            {k: v for k, v in data.items() if k != "result"}
            | {"result": {k: v for k, v in (data.get("result") or {}).items() if k != "stok"}},
        )
        code = data.get("error_code")
        if code == -5002:
            left = (data.get("result") or {}).get("attemptsAllowed", "?")
            raise LoginRefused(
                f"wrong password ({left} attempts left before the unit locks)", resource="login"
            )
        if code not in (0, None):
            raise LoginRefused(f"the unit refused the login (error {code})", resource="login")
        if not (data.get("result") or {}).get("stok"):
            raise PluginError("the unit accepted the login but gave no session")
        s.stok = data["result"]["stok"]
        s.cookie = self.http.cookies.get("sysauth") or ""
        self._save()

    def read(self, path: str, form: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        """A ``read`` operation, signing in when needed (and once more if the
        saved session was rejected)."""
        payload: dict[str, Any] = {"operation": "read"}
        if params:
            payload["params"] = params
        for again in (True, False):
            if not self.session.stok:
                self.login()
            if self.session.cookie:
                self.http.cookies.set("sysauth", self.session.cookie)
            why: dict[str, Any] = {}
            try:
                answer = self._post(
                    f"/cgi-bin/luci/;stok={self.session.stok}{path}",
                    form,
                    self.session.encode(payload),
                )
                why = {k: v for k, v in answer.items() if k != "data"}
                data = self.session.decode(answer.get("data"))
            except SessionExpired as e:
                data, why = {}, {"http": str(e)}
            except ValueError as e:
                data, why = {}, {"undecodable": str(e)[:80]}
            code = data.get("error_code", data.get("errorcode"))
            if data and code in (0, None):
                return data.get("result") or {}
            if not again:
                # Refused after a fresh login: this unit does not offer it.
                raise NotFound(
                    f"the unit refused to read {form} (error {code})", method="read", resource=form
                )
            # Why the saved session was refused (kept to understand the unit).
            self.keep(
                "session_refused", {"form": form, "code": code, **why, "msg": data.get("msg")}
            )
            log.info("session expired, signing in again")
            self.session.stok = ""
        raise AssertionError("unreachable")
