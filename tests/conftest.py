import base64
import json
from functools import partial
from urllib.parse import parse_qs

import httpx
import pytest
from cryptography.hazmat.primitives.asymmetric import padding, rsa

import omini_mercusys.collect as collect_module
from omini_mercusys.client import Client
from omini_mercusys.crypto import aes_decrypt, aes_encrypt


def rsa_decrypt(key, hex_blocks: str) -> bytes:
    size = key.key_size // 8
    raw = bytes.fromhex(hex_blocks)
    return b"".join(
        key.decrypt(raw[i : i + size], padding.PKCS1v15()) for i in range(0, len(raw), size)
    )


class FakeHalo:
    """Speaks the unit's protocol: RSA-encrypted password, AES requests
    signed with the session key, answers encrypted with the client's key."""

    def __init__(self, fixtures):
        self.password = "secret"
        self.pw_key = rsa.generate_private_key(public_exponent=65537, key_size=1024)
        self.sign_key = rsa.generate_private_key(public_exponent=65537, key_size=1024)
        self.seq = 4242
        self.stok = ""
        self.answers = fixtures
        self.calls = []
        self.logins = 0
        self.aes = ("", "")
        self.unlisted = []  # clients only the full list shows

    @staticmethod
    def pub(key):
        n = key.public_key().public_numbers()
        return [format(n.n, "X"), format(n.e, "06X")]

    def handler(self, request: httpx.Request) -> httpx.Response:
        form = request.url.params.get("form")
        if request.headers.get("content-type") != "application/json":
            return httpx.Response(200, json={"error_code": 1, "msg": "no such callback"})
        path = request.url.raw_path.decode().split("?")[0]
        self.calls.append(form)
        if form == "keys":
            return httpx.Response(
                200, json={"result": {"password": self.pub(self.pw_key)}, "error_code": 0}
            )
        if form == "auth":
            return httpx.Response(
                200,
                json={"result": {"key": self.pub(self.sign_key), "seq": self.seq}, "error_code": 0},
            )
        body = parse_qs(request.content.decode())
        sign = rsa_decrypt(self.sign_key, body["sign"][0]).decode()
        fields = dict(x.split("=", 1) for x in sign.split("&"))
        # Only the login carries the AES key; later requests reuse it.
        if form == "login":
            assert "k" in fields and "i" in fields, "the login must carry the AES key"
            self.aes = fields["k"], fields["i"]
        else:
            assert "k" not in fields, "only the login carries the AES key"
        key, iv = self.aes
        assert int(fields["s"]) == self.seq + len(body["data"][0]), "bad signature length"
        payload = json.loads(aes_decrypt(key, iv, base64.b64decode(body["data"][0])))

        def answer(obj):
            data = base64.b64encode(aes_encrypt(key, iv, json.dumps(obj).encode())).decode()
            return httpx.Response(200, json={"data": data}, headers=headers)

        headers = {}
        if form == "login":
            self.logins += 1
            pw = rsa_decrypt(self.pw_key, payload["params"]["password"]).decode()
            if pw != self.password:
                return answer({"error_code": -5002, "result": {"attemptsAllowed": 9}})
            self.stok = f"stok{self.logins}"
            headers = {"set-cookie": f"sysauth=cookie{self.logins}; path=/"}
            return answer({"error_code": 0, "result": {"stok": self.stok}})
        if (
            path != f"/cgi-bin/luci/;stok={self.stok}/admin/{path.rsplit('/', 1)[-1]}"
            or not self.stok
        ):
            return httpx.Response(403)
        assert payload["operation"] == "read", "only reads are sent"
        if form == "client_list":
            unit = payload["params"]["device_mac"].replace("-", "").replace(":", "").lower()
            if unit == "default":  # everyone, plus those the units' lists leave out
                everyone = [
                    c
                    for k, v in self.answers.items()
                    if k.startswith("client_list_")
                    for c in v["client_list"]
                ] + self.unlisted
                return answer({"error_code": 0, "result": {"client_list": everyone}})
            return answer({"error_code": 0, "result": self.answers[f"client_list_{unit}"]})
        if form not in self.answers:
            return answer({"error_code": -1, "msg": "no such callback"})
        return answer({"error_code": 0, "result": self.answers[form]})


@pytest.fixture
def fixtures():
    from pathlib import Path

    d = Path(__file__).parent / "fixtures"
    return {f.stem: json.loads(f.read_text()) for f in d.glob("*.json")}


@pytest.fixture
def halo(monkeypatch, fixtures):
    fake = FakeHalo(fixtures)
    monkeypatch.setattr(
        collect_module, "Client", partial(Client, transport=httpx.MockTransport(fake.handler))
    )
    return fake


@pytest.fixture
def cfg(tmp_path):
    from omini_sdk import Config

    return Config({"host": "192.168.1.121", "password": "secret"}, tmp_path)
