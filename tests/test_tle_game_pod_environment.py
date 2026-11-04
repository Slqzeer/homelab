"""Contract tests for the TLE game-pod environment (issue #48)."""

import json
from pathlib import Path
import unittest

import yaml


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
GAME_NAMESPACE = "tle-dev"
GAME_POD_CONFIG_MAP = "tle-dev-game-pod-env"
GAME_POD_SECRET = "tle-dev-game-pod-credentials"


class TleGamePodEnvironmentTests(unittest.TestCase):
    def load_documents(self, relative_path):
        with (REPOSITORY_ROOT / relative_path).open(encoding="utf-8") as stream:
            return [document for document in yaml.safe_load_all(stream) if document]

    def resources_by_kind(self, relative_path, kind):
        return {
            resource["metadata"]["name"]: resource
            for resource in self.load_documents(relative_path)
            if resource["kind"] == kind
        }

    def test_config_map_covers_required_shared_game_service_values(self):
        resources = self.resources_by_kind(
            "apps/tle-dev/config/game-pod-environment.yaml", "ConfigMap"
        )
        config_map = resources[GAME_POD_CONFIG_MAP]
        self.assertEqual(GAME_NAMESPACE, config_map["metadata"]["namespace"])
        self.assertEqual(
            {
                "GAME_MASTER_GRPC_TARGET": (
                    "http://tle-dev-game-master.tle-dev.svc.cluster.local:9100"
                ),
                "GAME_METADATA_SERVICE_BASE_URL": (
                    "http://tle-dev-metadata-service.tle-dev.svc.cluster.local:8080"
                ),
                "NATS_URL": "nats://tle-dev-nats.tle-dev.svc.cluster.local:4222",
                "VALKEY_URL": "redis://tle-dev-valkey.tle-dev.svc.cluster.local:6379",
            },
            config_map["data"],
        )

        with (REPOSITORY_ROOT / "apps/tle-dev/deploy-info/config-keys.json").open(
            encoding="utf-8"
        ) as stream:
            deploy_info = json.load(stream)
        game_service = next(
            service for service in deploy_info["services"]
            if service["service"] == "game-service"
        )
        per_pod = {"GAME_POD_HTTP_PORT", "GAME_POD_ID", "GAME_POD_WS_URL"}
        required_shared = {
            key["name"] for key in game_service["keys"]
            if key["required"] and not key["secret"] and key["name"] not in per_pod
        }
        self.assertTrue(required_shared.issubset(config_map["data"]))
        self.assertFalse(
            any(key["secret"] and key["name"] in config_map["data"]
                for key in game_service["keys"])
        )

    def test_limit_range_supplies_default_requests_and_limits(self):
        limit_range = self.resources_by_kind(
            "apps/tle-dev/config/game-pod-environment.yaml", "LimitRange"
        )["tle-dev-game-pod-defaults"]
        self.assertEqual(GAME_NAMESPACE, limit_range["metadata"]["namespace"])
        self.assertEqual(
            [{
                "type": "Container",
                "defaultRequest": {"cpu": "100m", "memory": "128Mi"},
                "default": {"cpu": "500m", "memory": "512Mi"},
            }],
            limit_range["spec"]["limits"],
        )

    def test_namespace_enforces_restricted_pod_security(self):
        namespaces = self.resources_by_kind(
            "bootstrap/namespaces/namespaces.yaml", "Namespace"
        )
        labels = namespaces[GAME_NAMESPACE]["metadata"]["labels"]
        self.assertEqual("restricted", labels["pod-security.kubernetes.io/enforce"])
        self.assertEqual("v1.36", labels["pod-security.kubernetes.io/enforce-version"])

    def test_game_master_reads_only_the_named_environment_objects(self):
        roles = self.resources_by_kind("apps/tle-dev/config/game-master.yaml", "Role")
        rules = roles["tle-dev-game-master"]["rules"]
        self.assertEqual(
            {
                ("configmaps",): (GAME_POD_CONFIG_MAP,),
                ("secrets",): (GAME_POD_SECRET,),
            },
            {
                tuple(rule["resources"]): tuple(rule["resourceNames"])
                for rule in rules
                if "resourceNames" in rule
            },
        )
        for rule in rules[1:]:
            self.assertEqual(["get"], rule["verbs"])

    def test_game_master_uses_config_map_and_pod_registry_secret(self):
        deployment = self.resources_by_kind(
            "apps/tle-dev/config/game-master.yaml", "Deployment"
        )["tle-dev-game-master"]
        env = {
            item["name"]: item
            for item in deployment["spec"]["template"]["spec"]["containers"][0]["env"]
        }
        self.assertEqual(GAME_POD_CONFIG_MAP, env["GAME_POD_ENV_CONFIGMAP"]["value"])
        self.assertEqual(GAME_POD_SECRET, env["GAME_POD_ENV_SECRET"]["value"])

    def test_game_pod_network_contract_is_exact(self):
        policies = self.resources_by_kind(
            "apps/tle-dev/config/networkpolicy.yaml", "NetworkPolicy"
        )
        game_pod = policies["tle-dev-game-pod"]
        self.assertEqual(
            {"role": "game-pod"}, game_pod["spec"]["podSelector"]["matchLabels"]
        )
        self.assertEqual(["Ingress", "Egress"], game_pod["spec"]["policyTypes"])
        self.assertEqual(
            [({"app": "tle-dev-edge"}, 8080)],
            [
                (rule["from"][0]["podSelector"]["matchLabels"], rule["ports"][0]["port"])
                for rule in game_pod["spec"]["ingress"]
            ],
        )
        self.assertEqual(
            {
                ("tle-dev-nats", 4222),
                ("tle-dev-valkey", 6379),
                ("tle-dev-metadata-service", 8080),
                ("tle-dev-game-master", 9100),
            },
            {
                (rule["to"][0]["podSelector"]["matchLabels"]["app"],
                 rule["ports"][0]["port"])
                for rule in game_pod["spec"]["egress"]
            },
        )
        for direction in ("ingress", "egress"):
            for rule in game_pod["spec"][direction]:
                self.assertEqual("TCP", rule["ports"][0]["protocol"])

        foundation = policies["tle-dev-dns-api"]
        self.assertEqual(
            [{"key": "role", "operator": "NotIn", "values": ["game-pod"]}],
            foundation["spec"]["podSelector"]["matchExpressions"],
        )
        game_pod_dns = policies["tle-dev-game-pod-dns"]
        self.assertEqual(
            {"role": "game-pod"},
            game_pod_dns["spec"]["podSelector"]["matchLabels"],
        )
        self.assertEqual(
            {("UDP", 53), ("TCP", 53)},
            {
                (port["protocol"], port["port"])
                for port in game_pod_dns["spec"]["egress"][0]["ports"]
            },
        )
        self.assertEqual(
            "kube-system",
            game_pod_dns["spec"]["egress"][0]["to"][0]
            ["namespaceSelector"]["matchLabels"]["kubernetes.io/metadata.name"],
        )

        self.assertIn(
            {"podSelector": {"matchLabels": {"role": "game-pod"}}},
            policies["tle-dev-valkey-clients"]["spec"]["ingress"][0]["from"],
        )
        self.assertIn(
            {"podSelector": {"matchLabels": {"role": "game-pod"}}},
            policies["tle-dev-nats-clients"]["spec"]["ingress"][0]["from"],
        )
        metadata = policies["tle-dev-metadata-from-game-pods"]
        self.assertEqual(
            {"app": "tle-dev-metadata-service"},
            metadata["spec"]["podSelector"]["matchLabels"],
        )
        self.assertEqual(
            {"role": "game-pod"},
            metadata["spec"]["ingress"][0]["from"][0]["podSelector"]["matchLabels"],
        )

    def test_game_pod_environment_is_in_the_tle_render(self):
        kustomization = self.load_documents("apps/tle-dev/config/kustomization.yaml")[0]
        self.assertIn("game-pod-environment.yaml", kustomization["resources"])


if __name__ == "__main__":
    unittest.main()
