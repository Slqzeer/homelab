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

    def test_player_migrations_use_their_own_schema(self):
        # golang-migrate's schema_migrations(version, dirty) cannot share
        # tle_dev.public with notification-service's filename-keyed table.
        from urllib.parse import parse_qs, urlparse

        config = next(
            item
            for item in documents("service-config.yaml")
            if item["metadata"]["name"] == "tle-dev-player-service-config"
        )
        url = urlparse(config["data"]["DATABASE_URL"])
        self.assertEqual("/tle_dev", url.path)
        self.assertEqual(["player"], parse_qs(url.query)["search_path"])

        job = next(
            item
            for item in documents("postgres-job.yaml")
            if item["kind"] == "Job"
        )
        script = job["spec"]["template"]["spec"]["containers"][0]["command"][-1]
        self.assertRegex(
            script,
            r"-d tle_dev -c \\\s+\"CREATE SCHEMA IF NOT EXISTS player AUTHORIZATION tle_dev\"",
        )

    def test_matchmaking_waits_for_valkey_before_its_one_shot_dial(self):
        # v0.0.2 caches its first Valkey dial error forever; a new pod's
        # first packets can precede NetworkPolicy admission of its IP.
        deployment = next(
            item
            for item in documents("workloads.yaml")
            if item["kind"] == "Deployment"
            and item["metadata"]["name"] == "tle-dev-matchmaking-service"
        )
        spec = deployment["spec"]["template"]["spec"]
        init, = spec["initContainers"]
        valkey = next(
            item
            for item in documents("datastores.yaml")
            if item["kind"] == "StatefulSet"
            and item["metadata"]["name"] == "tle-dev-valkey"
        )
        self.assertEqual(
            valkey["spec"]["template"]["spec"]["containers"][0]["image"],
            init["image"],
        )
        self.assertIn("tle-dev-valkey.tle-dev.svc.cluster.local", init["command"][-1])
        self.assertIn("PING", init["command"][-1])
        self.assertEqual(["ALL"], init["securityContext"]["capabilities"]["drop"])

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
            "-1", job["metadata"]["annotations"]["argocd.argoproj.io/sync-wave"]
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


    def test_streams_exist_before_the_services_that_wait_for_them(self):
        # Argo waits for each sync wave to be healthy before the next. A
        # consumer Deployment in an earlier or equal wave than the hook
        # deadlocks the sync and the hook never runs.
        def wave(resource):
            annotations = resource["metadata"].get("annotations") or {}
            return int(annotations.get("argocd.argoproj.io/sync-wave", "0"))

        kustomization = documents("kustomization.yaml")[0]
        rendered = [
            resource
            for name in kustomization["resources"]
            for resource in documents(name)
        ]
        bootstrap = {
            (resource["kind"], resource["metadata"]["name"]): wave(resource)
            for resource in documents("jetstream-bootstrap.yaml")
        }
        hook = bootstrap[("Job", "tle-dev-jetstream-bootstrap")]
        self.assertEqual({hook}, set(bootstrap.values()))
        nats = [
            wave(resource)
            for resource in rendered
            if resource["metadata"]["name"] == "tle-dev-nats"
        ]
        self.assertEqual(3, len(nats))
        self.assertLess(max(nats), hook)
        deployments = [wave(r) for r in rendered if r["kind"] == "Deployment"]
        self.assertTrue(deployments)
        self.assertLess(hook, min(deployments))


if __name__ == "__main__":
    unittest.main()
