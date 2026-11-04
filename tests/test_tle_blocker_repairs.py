"""Contract tests for the repository-owned TLE rollout blocker repairs."""

from pathlib import Path
import unittest

import yaml


ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "apps/tle-dev/config"


def documents(name):
    with (CONFIG / name).open(encoding="utf-8") as stream:
        return [item for item in yaml.safe_load_all(stream) if item]


class TleBlockerRepairTests(unittest.TestCase):
    def test_valkey_listens_on_the_pod_interface_behind_network_policy(self):
        stateful_set = next(
            item
            for item in documents("datastores.yaml")
            if item["kind"] == "StatefulSet"
            and item["metadata"]["name"] == "tle-dev-valkey"
        )
        args = stateful_set["spec"]["template"]["spec"]["containers"][0]["args"]
        self.assertIn("--bind", args)
        self.assertEqual("0.0.0.0", args[args.index("--bind") + 1])
        self.assertIn("--protected-mode", args)
        self.assertEqual("no", args[args.index("--protected-mode") + 1])

    def test_only_game_master_can_reach_the_k3s_api_endpoint(self):
        policies = {
            item["metadata"]["name"]: item
            for item in documents("networkpolicy.yaml")
            if item["kind"] == "NetworkPolicy"
        }
        policy = policies["tle-dev-game-master-kubernetes-api"]
        self.assertEqual(
            {"app": "tle-dev-game-master"},
            policy["spec"]["podSelector"]["matchLabels"],
        )
        self.assertEqual(
            [{"ipBlock": {"cidr": "192.168.1.201/32"}}],
            policy["spec"]["egress"][0]["to"],
        )
        self.assertEqual(
            [{"protocol": "TCP", "port": 6443}],
            policy["spec"]["egress"][0]["ports"],
        )
        dns_policy = policies["tle-dev-dns-api"]
        self.assertNotIn("default", yaml.safe_dump(dns_policy))
        self.assertNotIn("6443", yaml.safe_dump(dns_policy))

    def test_jetstream_sync_hook_reconciles_the_required_subjects(self):
        resources = documents("jetstream-bootstrap.yaml")
        by_kind = {item["kind"]: item for item in resources}
        streams = {}
        for line in by_kind["ConfigMap"]["data"]["streams.tsv"].splitlines():
            name, subjects = line.split("\t")
            streams[name] = subjects
        self.assertEqual(
            {
                "AUTH": "auth.>",
                "GAME_PLAYER": "game.>",
                "LOBBY": "lobby.player.>",
                "MATCHMAKING_MATCH_READY": "notification.match_ready",
                "METADATA": "metadata.version.published",
                "PARTY": "party.>",
                "PLAYER": "player.>",
                "QUEST_EVENTS": "quest.events.>",
                "QUEST_FACTS": "quest.facts.received",
                "QUEST_GRANTS": "quest.entitlement.>",
                "SOCIAL": "social.>",
            },
            streams,
        )

        job = by_kind["Job"]
        self.assertEqual(
            "Sync", job["metadata"]["annotations"]["argocd.argoproj.io/hook"]
        )
        self.assertEqual(
            "1", job["metadata"]["annotations"]["argocd.argoproj.io/sync-wave"]
        )
        pod_spec = job["spec"]["template"]["spec"]
        self.assertFalse(pod_spec["automountServiceAccountToken"])
        container = pod_spec["containers"][0]
        self.assertEqual(
            "natsio/nats-box:0.14.5@sha256:"
            "0784ab710aefaf6ef037ed797ee7dcde613c6ad208c4dbff1945fc7c1b5b5375",
            container["image"],
        )
        command = container["command"][2]
        self.assertIn("stream info", command)
        self.assertIn("stream edit", command)
        self.assertIn("stream add", command)

        policies = {
            item["metadata"]["name"]: item
            for item in documents("networkpolicy.yaml")
            if item["kind"] == "NetworkPolicy"
        }
        egress = policies["tle-dev-jetstream-bootstrap"]
        self.assertEqual(
            {"app": "tle-dev-jetstream-bootstrap"},
            egress["spec"]["podSelector"]["matchLabels"],
        )
        self.assertEqual(
            4222, egress["spec"]["egress"][0]["ports"][0]["port"]
        )
        nats_ingress = policies["tle-dev-nats-clients"]["spec"]["ingress"][0][
            "from"
        ]
        self.assertIn(
            {"podSelector": {"matchLabels": {"app": "tle-dev-jetstream-bootstrap"}}},
            nats_ingress,
        )

    def test_bootstrap_is_in_the_tle_render(self):
        kustomization = documents("kustomization.yaml")[0]
        self.assertIn("jetstream-bootstrap.yaml", kustomization["resources"])


if __name__ == "__main__":
    unittest.main()
