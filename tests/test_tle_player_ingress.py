"""Contract tests for the TLE dev player Ingress on the tailnet.

The player Ingress is an allow-list of edge prefixes (no ``/`` catch-all,
because an Ingress cannot exclude ``/auth/admin/*``). It must cover every
player route a tailnet client needs to play, including the game WebSocket.
"""

from pathlib import Path
import unittest

import yaml


ROOT = Path(__file__).resolve().parents[1]
INGRESS = ROOT / "infrastructure/ingress/config/tle-dev-player-ingress.yaml"


def paths():
    ingress = yaml.safe_load(INGRESS.read_text(encoding="utf-8"))
    (rule,) = ingress["spec"]["rules"]
    return ingress, rule["http"]["paths"]


class TlePlayerIngressTests(unittest.TestCase):
    def test_every_path_routes_to_the_edge_only(self):
        _, entries = paths()
        for entry in entries:
            with self.subTest(path=entry["path"]):
                self.assertEqual(
                    {"service": {"name": "tle-dev-edge", "port": {"name": "http"}}},
                    entry["backend"],
                )

    def test_play_routes_are_reachable_through_the_tailnet(self):
        _, entries = paths()
        exposed = {(entry["path"], entry["pathType"]) for entry in entries}
        for expected in (
            ("/auth/login", "Prefix"),
            ("/auth/me", "Prefix"),
            ("/lobby/v1", "Prefix"),
            ("/matchmaking/v1", "Prefix"),
            ("/player/v1", "Prefix"),
            ("/game/v1/ws", "Exact"),
        ):
            with self.subTest(path=expected[0]):
                self.assertIn(expected, exposed)

    def test_admin_and_catch_all_paths_stay_unexposed(self):
        _, entries = paths()
        for entry in entries:
            with self.subTest(path=entry["path"]):
                self.assertNotEqual("/", entry["path"])
                self.assertFalse(entry["path"].startswith("/auth/admin"))
                self.assertNotEqual("/auth", entry["path"].rstrip("/"))

    def test_ingress_is_served_by_the_tailscale_proxy_group(self):
        ingress, _ = paths()
        self.assertEqual("tailscale", ingress["spec"]["ingressClassName"])
        self.assertEqual(
            "ingress", ingress["metadata"]["annotations"]["tailscale.com/proxy-group"]
        )


if __name__ == "__main__":
    unittest.main()
