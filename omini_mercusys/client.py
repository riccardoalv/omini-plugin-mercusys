"""Client for the local web interface (LuCI JSON API behind /cgi-bin/luci).

Signing in takes four requests and makes the unit drop other admin sessions,
so the session (AES key, signing key, ``stok`` and cookie) is kept in the
plugin's state folder and reused until the unit rejects it.

Only ``read`` operations are sent, besides the login itself.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

import httpx
from omini_sdk import PluginError, log

from omini_mercusys.crypto import Session, rsa_encrypt

# As the web interface sends every request (even the urlencoded sign=&data=
# body): the unit picks how to read a request by this header.
JSON = {"Content-Type": "application/json"}


class SessionExpired(Exception):
    pass


class Client:
    def __init__(
        self,
        host: str,
        username: str,
        password: str,
        verify_tls: bool = False,
        state_dir: Path | None = None,
        timeout: float = 15,
        transport: httpx.BaseTransport | None = None,
    ):
        host = host.strip().rstrip("/")
        if not host.startswith(("http://", "https://")):
            host = "https://" + host
        self.base = host
        self.state_dir = state_dir
        saved = self._load()
        if saved.get("password_hash") != _fingerprint(username, password):
            saved = {}  # other credentials: start over
        self.session = Session(username, password, saved.get("session"))
        self.http = httpx.Client(
            base_url=host,
            verify=verify_tls,
            timeout=timeout,
            headers={
                "User-Agent": "omini-plugin-mercusys",
                "Referer": host + "/webpages/index.html",
            },
            transport=transport,
            follow_redirects=True,
        )

    def close(self) -> None:
        self.http.close()

    # --- state ------------------------------------------------------------------

    def _state_file(self) -> Path | None:
        return self.state_dir / "session.json" if self.state_dir else None

    def _load(self) -> dict[str, Any]:
        f = self._state_file()
        try:
            return json.loads(f.read_text()) if f and f.exists() else {}
        except (OSError, ValueError):
            return {}

    def _save(self) -> None:
        f = self._state_file()
        if not f:
            return
        try:
            f.parent.mkdir(parents=True, exist_ok=True)
            data = {
                "password_hash": _fingerprint(self.session.username, self.session.password),
                "session": self.session.values(),
            }
            f.write_text(json.dumps(data))
            os.chmod(f, 0o600)
        except OSError:
            log.debug("could not save the session")

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
        # A unit busy for a moment: reads are tried once more (never the login).
        tries = 1 if form == "login" else 2
        for attempt in range(tries):
            try:
                r = self.http.post(path, params={"form": form}, content=body, headers=JSON)
                break
            except httpx.ConnectError as e:
                raise PluginError(f"cannot connect to {self.base}: {e}") from e
            except httpx.TimeoutException as e:
                if attempt + 1 < tries:
                    log.info("%s did not answer %s in time, trying again", self.base, form)
                    continue
                raise PluginError(f"{self.base} did not answer in time") from e
            except httpx.HTTPError as e:
                raise PluginError(f"request to {self.base} failed: {e}") from e
        if r.status_code in (401, 403):
            raise SessionExpired(str(r.status_code))
        if r.status_code >= 400:
            raise PluginError(f"the unit answered {r.status_code} for {path}")
        try:
            return r.json()
        except ValueError as e:
            raise PluginError(f"unexpected answer from {path}") from e

    # After a refused login, wait before trying again: the unit counts failed
    # attempts and locks the login for a while past a limit.
    LOGIN_BACKOFF_S = 15 * 60

    def _refused_recently(self) -> str | None:
        last = self._load().get("refused")
        same = last and last.get("for") == _fingerprint(
            self.session.username, self.session.password
        )
        if same and time.time() - last.get("at", 0) < self.LOGIN_BACKOFF_S:
            return last.get("error")
        return None

    def _remember_refusal(self, error: str | None) -> None:
        f = self._state_file()
        if not f:
            return
        data = self._load()
        if error:
            # Only for these credentials: new ones are tried at once.
            data["refused"] = {
                "at": time.time(),
                "error": error,
                "for": _fingerprint(self.session.username, self.session.password),
            }
        else:
            data.pop("refused", None)
        try:
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_text(json.dumps(data))
            os.chmod(f, 0o600)
        except OSError:
            pass

    def login(self) -> None:
        recent = self._refused_recently()
        if recent:
            raise PluginError(f"{recent} (waiting a few minutes before signing in again)")
        try:
            self._login()
        except PluginError as e:
            if "connect" not in str(e) and "answer" not in str(e):
                self._remember_refusal(str(e))
            raise
        self._remember_refusal(None)

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
            raise PluginError(f"wrong password ({left} attempts left before the unit locks)")
        if code not in (0, None) or not (data.get("result") or {}).get("stok"):
            raise PluginError(f"the unit refused the login (error {code})")
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
                raise PluginError(f"the unit refused to read {form} (error {code})")
            # Why the saved session was refused (kept to understand the unit).
            self.keep(
                "session_refused", {"form": form, "code": code, **why, "msg": data.get("msg")}
            )
            log.info("session expired, signing in again")
            self.session.stok = ""
        raise AssertionError("unreachable")


def _fingerprint(username: str, password: str) -> str:
    import hashlib

    return hashlib.sha256(f"{username}\0{password}".encode()).hexdigest()
