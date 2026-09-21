#!/usr/bin/env python3
"""Regression checks for Grafana's split OIDC routing."""

from pathlib import Path
import unittest
from urllib.parse import urlparse

import yaml


VALUES = Path(__file__).with_name("values.yaml")
REALM_PATH = "/realms/homelab/protocol/openid-connect"


class GrafanaOAuthRoutingTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        values = yaml.safe_load(VALUES.read_text(encoding="utf-8"))
        cls.oauth = values["grafana"]["grafana.ini"]["auth.generic_oauth"]

    def test_browser_authorization_uses_tailnet_https(self) -> None:
        endpoint = urlparse(self.oauth["auth_url"])
        self.assertEqual(endpoint.scheme, "https")
        self.assertEqual(endpoint.hostname, "keycloak.taildf6cd4.ts.net")
        self.assertEqual(endpoint.path, f"{REALM_PATH}/auth")

    def test_server_backchannels_use_cluster_service(self) -> None:
        for setting, suffix in (("token_url", "token"), ("api_url", "userinfo")):
            with self.subTest(setting=setting):
                endpoint = urlparse(self.oauth[setting])
                self.assertEqual(endpoint.scheme, "http")
                self.assertEqual(
                    endpoint.hostname,
                    "keycloak.keycloak.svc.cluster.local",
                )
                self.assertEqual(endpoint.port, 8080)
                self.assertEqual(endpoint.path, f"{REALM_PATH}/{suffix}")


if __name__ == "__main__":
    unittest.main()
