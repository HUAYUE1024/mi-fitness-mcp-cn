import argparse
import base64
import getpass
import hashlib
import json
import os
import random
import struct
from datetime import datetime
from urllib.parse import urlencode

import httpx


LOGIN_PREFIX = b"&&&START&&&"
DEFAULT_KEYS = [
    "weight",
    "steps",
    "step",
    "activity",
    "sleep",
    "heart_rate",
    "heartrate",
    "hr",
    "sport",
    "workout",
    "calories",
]
DEFAULT_REGIONS = ["cn", "de", "i2", "ru", "sg", "us"]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="probe_mifitness")
    parser.add_argument("--username")
    parser.add_argument("--password")
    parser.add_argument("--token")
    parser.add_argument("--user-id")
    parser.add_argument("--pass-token")
    parser.add_argument("--start-date", default="2025-04-01")
    parser.add_argument("--end-date", default="2025-05-31")
    parser.add_argument("--regions", nargs="*", default=DEFAULT_REGIONS)
    parser.add_argument("--keys", nargs="*", default=DEFAULT_KEYS)
    return parser.parse_args()


def md5_upper(text: str) -> str:
    return hashlib.md5(text.encode()).hexdigest().upper()


def read_login_payload(text: str) -> dict:
    body = text.encode()
    if not body.startswith(LOGIN_PREFIX):
        raise RuntimeError("unexpected Xiaomi login response")
    return json.loads(body[len(LOGIN_PREFIX):].decode())


def rc4_crypt(key: bytes, payload: bytes) -> bytes:
    s = list(range(256))
    j = 0
    key_len = len(key)
    for i in range(256):
        j = (j + s[i] + key[i % key_len]) % 256
        s[i], s[j] = s[j], s[i]
    i = 0
    j = 0

    def next_byte() -> int:
        nonlocal i, j
        i = (i + 1) % 256
        j = (j + s[i]) % 256
        s[i], s[j] = s[j], s[i]
        return s[(s[i] + s[j]) % 256]

    for _ in range(1024):
        next_byte()

    out = bytearray()
    for b in payload:
        out.append(b ^ next_byte())
    return bytes(out)


def gen_nonce() -> bytes:
    raw = bytearray(os.urandom(8))
    raw.extend(struct.pack(">I", int(datetime.now().timestamp() // 60)))
    return bytes(raw)


def gen_signed_nonce(ssecurity: bytes, nonce: bytes) -> bytes:
    return hashlib.sha256(ssecurity + nonce).digest()


def gen_signature(method: str, path: str, values: dict[str, str], signed_nonce: bytes) -> str:
    base = method + "&" + path + "&data=" + values["data"]
    if "rc4_hash__" in values:
        base += "&rc4_hash__=" + values["rc4_hash__"]
    base += "&" + base64.b64encode(signed_nonce).decode()
    return base64.b64encode(hashlib.sha1(base.encode()).digest()).decode()


class MiFitnessProbe:
    def __init__(self) -> None:
        self.client = httpx.Client(timeout=30.0, follow_redirects=False)
        self.sid = "miothealth"
        self.cookies = ""
        self.ssecurity = b""
        self.pass_token = ""
        self.user_id = 0

    def login(self, username: str, password: str) -> None:
        info = self.service_login()
        auth = self.service_login2(info, username, password)
        self.service_login3(auth["location"])

    def login_with_token(self, token: str | None = None, user_id: str | None = None, pass_token: str | None = None) -> None:
        if token:
            user_id, pass_token = token.split(":", 1)
        if not user_id or not pass_token:
            raise RuntimeError("token login requires user_id and pass_token")

        response = self.client.get(
            f"https://account.xiaomi.com/pass/serviceLogin?_json=true&sid={self.sid}",
            headers={"Cookie": f"userId={user_id}; passToken={pass_token}"},
        )
        response.raise_for_status()
        payload = read_login_payload(response.text)
        if "passToken" not in payload or "ssecurity" not in payload or "userId" not in payload or "location" not in payload:
            details = {
                "keys": sorted(payload.keys()),
                "code": payload.get("code"),
                "description": payload.get("description"),
                "desc": payload.get("desc"),
                "location": payload.get("location"),
            }
            raise RuntimeError(f"xiaomi token login failed; details={json.dumps(details, ensure_ascii=False)}")
        self.pass_token = payload["passToken"]
        self.ssecurity = base64.b64decode(payload["ssecurity"])
        self.user_id = int(payload["userId"])
        self.service_login3(payload["location"])

    def service_login(self) -> dict:
        response = self.client.get(f"https://account.xiaomi.com/pass/serviceLogin?_json=true&sid={self.sid}")
        response.raise_for_status()
        return read_login_payload(response.text)

    def service_login2(self, info: dict, username: str, password: str) -> dict:
        form = {
            "_json": "true",
            "hash": md5_upper(password),
            "sid": info["sid"],
            "callback": info["callback"],
            "_sign": info["_sign"],
            "qs": info["qs"],
            "user": username,
        }
        device_id = "".join(random.choice("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789") for _ in range(16))
        response = self.client.post(
            "https://account.xiaomi.com/pass/serviceLoginAuth2",
            headers={
                "Cookie": f"deviceId={device_id}",
                "Content-Type": "application/x-www-form-urlencoded",
            },
            content=urlencode(form),
        )
        response.raise_for_status()
        payload = read_login_payload(response.text)
        if "passToken" not in payload or "ssecurity" not in payload or "userId" not in payload:
            message = payload.get("description") or payload.get("desc") or payload.get("message")
            details = {
                "keys": sorted(payload.keys()),
                "code": payload.get("code"),
                "description": payload.get("description"),
                "desc": payload.get("desc"),
                "captchaUrl": payload.get("captchaUrl"),
                "notificationUrl": payload.get("notificationUrl"),
                "location": payload.get("location"),
            }
            raise RuntimeError(f"xiaomi login failed: {message or 'missing auth tokens'}; details={json.dumps(details, ensure_ascii=False)}")
        self.pass_token = payload["passToken"]
        self.ssecurity = base64.b64decode(payload["ssecurity"])
        self.user_id = int(payload["userId"])
        return payload

    def service_login3(self, location: str) -> None:
        response = self.client.get(location)
        response.raise_for_status()
        cookie_parts = []
        for value in response.headers.get_list("set-cookie"):
            cookie_parts.append(value.split(";", 1)[0])
        self.cookies = "; ".join(cookie_parts)

    def request(self, base_url: str, api_path: str, params: dict) -> dict:
        form = {"data": json.dumps(params, separators=(",", ":"))}
        nonce = gen_nonce()
        signed_nonce = gen_signed_nonce(self.ssecurity, nonce)
        form["rc4_hash__"] = gen_signature("POST", api_path, form, signed_nonce)

        encrypted = {}
        for key, value in form.items():
            encrypted[key] = base64.b64encode(rc4_crypt(signed_nonce, value.encode())).decode()

        encrypted["signature"] = gen_signature("POST", api_path, encrypted, signed_nonce)
        encrypted["_nonce"] = base64.b64encode(nonce).decode()

        response = self.client.post(
            base_url + api_path,
            headers={
                "Cookie": self.cookies,
                "Content-Type": "application/x-www-form-urlencoded",
            },
            content=urlencode(encrypted),
        )
        response.raise_for_status()
        plaintext = rc4_crypt(signed_nonce, base64.b64decode(response.text))
        payload = json.loads(plaintext)
        if payload.get("code") != 0:
            raise RuntimeError(payload.get("message", "unknown mi fitness error"))
        return payload.get("result", {})


def region_base(region: str) -> str:
    if region in ("", "cn"):
        return "https://hlth.io.mi.com"
    return f"https://{region}.hlth.io.mi.com"


def sample_view(items: list[dict]) -> str:
    if not items:
        return "[]"
    return json.dumps(items[:2], ensure_ascii=False)[:700]


def run_probe(args: argparse.Namespace) -> None:
    probe = MiFitnessProbe()

    if args.token or (args.user_id and args.pass_token):
        probe.login_with_token(token=args.token, user_id=args.user_id, pass_token=args.pass_token)
    else:
        username = args.username or input("Xiaomi login (email/phone): ").strip()
        password = args.password or getpass.getpass("Xiaomi password: ")
        probe.login(username, password)

    print(f"login ok: user_id={probe.user_id}")

    for region in args.regions:
        base = region_base(region)
        print(f"\n=== region={region} base={base} ===")
        for key in args.keys:
            try:
                result = probe.request(
                    base,
                    "/app/v1/data/get_fitness_data_by_time",
                    {
                        "start_time": int(datetime.fromisoformat(args.start_date).timestamp()),
                        "end_time": int(datetime.fromisoformat(args.end_date + "T23:59:59").timestamp()),
                        "key": key,
                    },
                )
                data_list = result.get("data_list", [])
                print(f"key={key} count={len(data_list)} has_more={result.get('has_more')} next_key={bool(result.get('next_key'))}")
                if data_list:
                    print(sample_view(data_list))
            except Exception as exc:
                print(f"key={key} error={exc}")


if __name__ == "__main__":
    run_probe(parse_args())
