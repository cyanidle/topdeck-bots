#!/usr/bin/env python3
"""Render an Xray-core config from a vless:// subscription link.

  TD_PROXY_SUB_URL=... python3 deploy/xray_from_sub.py --out /etc/xray/config.json
  TD_PROXY_SUB_URL=... python3 deploy/xray_from_sub.py --print   # секреты замаскированы
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import re
import ssl
import sys
import urllib.parse
import urllib.request

SOCKS_PORT = 1080
HTTP_PORT = 1081
TELEGRAM_DOMAINS = [
    "domain:telegram.org",
    "domain:telegram.me",
    "domain:t.me",
    "domain:tdesktop.com",
    "domain:telegra.ph",
    "domain:telegram-cdn.org",
    "domain:cdn-telegram.org",
    "domain:telesco.pe",
]
SCRYFALL_DOMAINS = [
    "domain:scryfall.com",
    "domain:scryfall.io",
]
TELEGRAM_CIDRS = [
    "91.105.192.0/23",
    "91.108.4.0/22",
    "91.108.8.0/22",
    "91.108.12.0/22",
    "91.108.16.0/22",
    "91.108.20.0/22",
    "91.108.56.0/22",
    "95.161.76.0/22",
    "149.154.160.0/20",
    "2001:67c:4e8::/48",
    "2001:b28:f23c::/47",
    "2001:b28:f23f::/48",
    "2a0a:f280::/32",
]


def load_env_file(path: str) -> None:
    if not os.path.exists(path):
        return
    for line in open(path, encoding="utf-8"):
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip())


def fetch_subscription(url: str) -> str:
    context = ssl.create_default_context()
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE
    req = urllib.request.Request(url, headers={"User-Agent": "topbot-xray-sync/1.0"})
    with urllib.request.urlopen(req, timeout=30, context=context) as resp:
        raw = resp.read().strip()
    padding = b"=" * (-len(raw) % 4)
    try:
        return base64.b64decode(raw + padding).decode("utf-8", "replace")
    except Exception:
        return raw.decode("utf-8", "replace")


def parse_vless(link: str) -> dict:
    parsed = urllib.parse.urlparse(link)
    if parsed.scheme != "vless":
        raise ValueError(f"unsupported scheme: {parsed.scheme}")
    query = urllib.parse.parse_qs(parsed.query)
    first = {key: values[0] for key, values in query.items()}
    extra = json.loads(first.get("extra", "{}"))
    return {
        "uuid": urllib.parse.unquote(parsed.username or ""),
        "address": parsed.hostname,
        "port": parsed.port or 443,
        "sni": first.get("sni", ""),
        "fingerprint": first.get("fp", "chrome"),
        "public_key": first.get("pbk", ""),
        "short_id": first.get("sid", ""),
        "spider_x": first.get("spx", "/"),
        "path": first.get("path", "/"),
        "mode": first.get("mode", "auto"),
        "xhttp_extra": extra or {"mode": "auto"},
        "remark": urllib.parse.unquote(parsed.fragment),
    }


def pick_link(body: str) -> str:
    for link in re.split(r"\s+", body):
        if link.startswith("vless://"):
            return link
    raise ValueError("no vless:// link in subscription")


def build_config(node: dict) -> dict:
    return {
        "log": {"loglevel": "warning"},
        "dns": {
            "servers": [
                {"address": "https://1.1.1.1/dns-query", "domains": TELEGRAM_DOMAINS, "skipFallback": True},
                "localhost",
            ],
            "queryStrategy": "UseIP",
        },
        "inbounds": [
            {
                "tag": "socks-in",
                "listen": "127.0.0.1",
                "port": SOCKS_PORT,
                "protocol": "socks",
                "settings": {"auth": "noauth", "udp": False},
                "sniffing": {"enabled": True, "destOverride": ["http", "tls"]},
            },
            {
                "tag": "http-in",
                "listen": "127.0.0.1",
                "port": HTTP_PORT,
                "protocol": "http",
                "settings": {},
                "sniffing": {"enabled": True, "destOverride": ["http", "tls"]},
            },
        ],
        "outbounds": [
            {"tag": "direct", "protocol": "freedom"},
            {
                "tag": "proxy",
                "protocol": "vless",
                "settings": {
                    "vnext": [
                        {
                            "address": node["address"],
                            "port": node["port"],
                            "users": [{"id": node["uuid"], "encryption": "none", "flow": ""}],
                        }
                    ]
                },
                "streamSettings": {
                    "network": "xhttp",
                    "security": "reality",
                    "realitySettings": {
                        "serverName": node["sni"],
                        "fingerprint": node["fingerprint"],
                        "publicKey": node["public_key"],
                        "shortId": node["short_id"],
                        "spiderX": node["spider_x"],
                    },
                    "xhttpSettings": {
                        "path": node["path"],
                        "mode": node["mode"],
                        "extra": node["xhttp_extra"],
                    },
                },
            },
            {"tag": "block", "protocol": "blackhole"},
        ],
        "routing": {
            "domainStrategy": "IPIfNonMatch",
            "rules": [
                {"type": "field", "domain": TELEGRAM_DOMAINS, "outboundTag": "proxy"},
                {"type": "field", "domain": SCRYFALL_DOMAINS, "outboundTag": "proxy"},
                {"type": "field", "ip": TELEGRAM_CIDRS, "outboundTag": "proxy"},
            ],
        },
    }


def mask(config: dict) -> dict:
    text = json.dumps(config, ensure_ascii=False, indent=4)
    for key in ("id", "publicKey", "shortId", "spiderX"):
        text = re.sub(rf'("{key}": ")[^"]+', r"\1***", text)
    return json.loads(text)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--out", default="", help="куда записать config.json")
    parser.add_argument("--print", action="store_true", help="напечатать конфиг с замаскированными секретами")
    parser.add_argument("--env-file", default=".env")
    args = parser.parse_args()

    load_env_file(args.env_file)
    sub_url = os.environ.get("TD_PROXY_SUB_URL", "")
    if not sub_url:
        print("TD_PROXY_SUB_URL is not set", file=sys.stderr)
        return 2

    node = parse_vless(pick_link(fetch_subscription(sub_url)))
    config = build_config(node)

    if args.print or not args.out:
        print(json.dumps(mask(config), ensure_ascii=False, indent=4))
    if args.out:
        with open(args.out, "w", encoding="utf-8") as handle:
            json.dump(config, handle, ensure_ascii=False, indent=4)
            handle.write("\n")
        os.chmod(args.out, 0o600)
        print(f"written: {args.out} (node: {node['address']}:{node['port']}, {node['remark']})", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
