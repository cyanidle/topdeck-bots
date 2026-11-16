from __future__ import annotations

import importlib.util
import pathlib
import unittest

SCRIPT = pathlib.Path(__file__).resolve().parents[1] / "deploy" / "xray_from_sub.py"
spec = importlib.util.spec_from_file_location("xray_from_sub", SCRIPT)
xray = importlib.util.module_from_spec(spec)
spec.loader.exec_module(xray)

NODE = {
    "uuid": "00000000-0000-0000-0000-000000000000",
    "address": "203.0.113.10",
    "port": 443,
    "sni": "example.com",
    "fingerprint": "firefox",
    "public_key": "pubkey",
    "short_id": "abcd",
    "spider_x": "/",
    "path": "/",
    "mode": "auto",
    "xhttp_extra": {"mode": "auto", "xPaddingBytes": "100-1000"},
}


class XrayConfigTest(unittest.TestCase):
    def setUp(self) -> None:
        self.config = xray.build_config(NODE)
        self.rules = self.config["routing"]["rules"]

    def test_only_telegram_and_scryfall_are_proxied(self):
        proxied_domains: list[str] = []
        for rule in self.rules:
            if rule["outboundTag"] == "proxy" and "domain" in rule:
                proxied_domains.extend(rule["domain"])
        self.assertIn("domain:telegram.org", proxied_domains)
        self.assertIn("domain:scryfall.com", proxied_domains)
        self.assertIn("domain:scryfall.io", proxied_domains)
        self.assertNotIn("domain:topdeck.ru", proxied_domains)

    def test_default_outbound_is_direct(self):
        self.assertEqual(self.config["outbounds"][0]["tag"], "direct")

    def test_both_inbounds_listen_on_loopback(self):
        ports = {}
        for inbound in self.config["inbounds"]:
            self.assertEqual(inbound["listen"], "127.0.0.1")
            ports[inbound["protocol"]] = inbound["port"]
        self.assertEqual(ports["socks"], 1080)
        self.assertEqual(ports["http"], 1081)

    def test_transport_is_reality_xhttp(self):
        stream = self.config["outbounds"][1]["streamSettings"]
        self.assertEqual(stream["network"], "xhttp")
        self.assertEqual(stream["security"], "reality")
        self.assertEqual(stream["realitySettings"]["publicKey"], "pubkey")
        self.assertEqual(stream["xhttpSettings"]["extra"]["xPaddingBytes"], "100-1000")

    def test_dns_uses_doh_for_proxied_domains(self):
        dns = self.config["dns"]["servers"][0]
        self.assertTrue(dns["address"].startswith("https://"))
        self.assertIn("domain:telegram.org", dns["domains"])
        self.assertEqual(self.config["dns"]["servers"][1], "localhost")

    def test_mask_hides_secrets(self):
        masked = xray.mask(self.config)
        rendered = str(masked)
        self.assertNotIn("00000000-0000-0000-0000-000000000000", rendered)
        self.assertNotIn("pubkey", rendered)
        self.assertNotIn("abcd", rendered)

    def test_parse_vless_link(self):
        link = (
            "vless://11111111-2222-3333-4444-555555555555@203.0.113.10:443"
            "?encryption=none&security=reality&sni=example.com&fp=firefox&pbk=PK&sid=SID&spx=%2F"
            "&type=xhttp&path=%2F&mode=auto&extra=%7B%22mode%22%3A%22auto%22%7D#node-1"
        )
        node = xray.parse_vless(link)
        self.assertEqual(node["uuid"], "11111111-2222-3333-4444-555555555555")
        self.assertEqual(node["address"], "203.0.113.10")
        self.assertEqual(node["port"], 443)
        self.assertEqual(node["sni"], "example.com")
        self.assertEqual(node["spider_x"], "/")
        self.assertEqual(node["xhttp_extra"], {"mode": "auto"})
        self.assertEqual(node["remark"], "node-1")

    def test_pick_link_skips_whitespace(self):
        body = "  \n vless://x@host:443?type=xhttp#node \n"
        self.assertTrue(xray.pick_link(body).startswith("vless://"))


if __name__ == "__main__":
    unittest.main()
