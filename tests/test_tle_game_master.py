"""Contract tests for the TLE Game Master workload and capability boundary."""

from pathlib import Path
import unittest

import yaml


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
GAME_MASTER_APP = "tle-dev-game-master"
GAME_NAMESPACE = "tle-dev"


class TleGameMasterTests(unittest.TestCase):
    def load_documents(self, relative_path):
        with (REPOSITORY_ROOT / relative_path).open(encoding="utf-8") as stream:
            return [document for document in yaml.safe_load_all(stream) if document]

    def resources_by_kind(self, relative_path, kind):
        return {
            resource["metadata"]["name"]: resource
            for resource in self.load_documents(relative_path)
            if resource["kind"] == kind
        }

    def test_single_replica_workload_has_no_hpa_and_uses_current_ports(self):
        path = "apps/tle-dev/config/game-master.yaml"
        deployments = self.resources_by_kind(path, "Deployment")
        deployment = deployments[GAME_MASTER_APP]
        self.assertEqual(1, deployment["spec"]["replicas"])
        self.assertEqual({}, self.resources_by_kind(path, "HorizontalPodAutoscaler"))

        pod_spec = deployment["spec"]["template"]["spec"]
        self.assertEqual(GAME_MASTER_APP, pod_spec["serviceAccountName"])
        self.assertNotEqual(False, pod_spec.get("automountServiceAccountToken"))
        container = pod_spec["containers"][0]
        self.assertEqual(
            "ghcr.io/the-last-exam/game-master:v0.0.2@sha256:"
            "966baf4811fcc71a60069dffa8bef03d1ecc2a10e16f1c48047c479b54662936",
            container["image"],
        )
        ports = {port["name"]: port["containerPort"] for port in container["ports"]}
        self.assertEqual({"http": 8082, "grpc": 9100}, ports)
        env = {item["name"]: item for item in container["env"]}
        self.assertEqual("8082", env["GAME_MASTER_HTTP_PORT"]["value"])
        self.assertEqual("9100", env["GAME_MASTER_GRPC_PORT"]["value"])
        self.assertEqual(GAME_NAMESPACE, env["GAME_MASTER_NAMESPACE"]["value"])

        self.assertEqual("/healthz", container["livenessProbe"]["httpGet"]["path"])
        self.assertEqual("/readyz", container["readinessProbe"]["httpGet"]["path"])
        self.assertEqual(
            {"cpu": "100m", "memory": "128Mi"},
            container["resources"]["requests"],
        )
        self.assertEqual(
            {"cpu": "500m", "memory": "512Mi"},
            container["resources"]["limits"],
        )

    def test_pdb_preserves_the_only_replica(self):
        pdb = self.resources_by_kind(
            "apps/tle-dev/config/game-master.yaml", "PodDisruptionBudget"
        )[GAME_MASTER_APP]
        self.assertEqual(1, pdb["spec"]["minAvailable"])
        self.assertEqual(
            {"app": GAME_MASTER_APP},
            pdb["spec"]["selector"]["matchLabels"],
        )

    def test_rbac_is_namespace_scoped_to_exactly_six_pod_verbs(self):
        path = "apps/tle-dev/config/game-master.yaml"
        role = self.resources_by_kind(path, "Role")[GAME_MASTER_APP]
        self.assertEqual(GAME_NAMESPACE, role["metadata"]["namespace"])
        self.assertEqual(
            [{
                "apiGroups": [""],
                "resources": ["pods"],
                "verbs": ["get", "list", "watch", "create", "delete", "patch"],
            }],
            role["rules"],
        )

        binding = self.resources_by_kind(path, "RoleBinding")[GAME_MASTER_APP]
        self.assertEqual(GAME_NAMESPACE, binding["metadata"]["namespace"])
        self.assertEqual(
            [{
                "kind": "ServiceAccount",
                "name": GAME_MASTER_APP,
                "namespace": GAME_NAMESPACE,
            }],
            binding["subjects"],
        )
        self.assertEqual(
            {
                "apiGroup": "rbac.authorization.k8s.io",
                "kind": "Role",
                "name": GAME_MASTER_APP,
            },
            binding["roleRef"],
        )

    def test_game_master_ingress_is_limited_to_three_contract_paths(self):
        policies = self.resources_by_kind(
            "apps/tle-dev/config/networkpolicy.yaml", "NetworkPolicy"
        )
        policy = policies["tle-dev-game-master-ingress"]
        self.assertEqual({"app": GAME_MASTER_APP}, policy["spec"]["podSelector"]["matchLabels"])
        self.assertEqual(["Ingress"], policy["spec"]["policyTypes"])
        self.assertEqual(
            [
                (
                    {"app": "tle-dev-matchmaking-service"},
                    9100,
                ),
                (
                    {"role": "game-pod"},
                    9100,
                ),
                (
                    {"app.kubernetes.io/component": "ops-job"},
                    8082,
                ),
            ],
            [
                (
                    rule["from"][0]["podSelector"]["matchLabels"],
                    rule["ports"][0]["port"],
                )
                for rule in policy["spec"]["ingress"]
            ],
        )
        serialized = yaml.safe_dump(policy)
        self.assertNotIn("tle-dev-edge", serialized)
        self.assertNotIn("tailscale", serialized)
        for rule in policy["spec"]["ingress"]:
            self.assertEqual("TCP", rule["ports"][0]["protocol"])
            self.assertNotIn("namespaceSelector", rule["from"][0])

    def test_matchmaking_and_game_master_have_only_required_peer_flows(self):
        policies = self.resources_by_kind(
            "apps/tle-dev/config/networkpolicy.yaml", "NetworkPolicy"
        )
        matchmaking = policies["tle-dev-matchmaking-to-game-master"]
        self.assertEqual(
            {"app": "tle-dev-matchmaking-service"},
            matchmaking["spec"]["podSelector"]["matchLabels"],
        )
        self.assertEqual(
            {"app": GAME_MASTER_APP},
            matchmaking["spec"]["egress"][0]["to"][0]["podSelector"]["matchLabels"],
        )
        self.assertEqual(9100, matchmaking["spec"]["egress"][0]["ports"][0]["port"])

        game_master = policies["tle-dev-game-master-egress"]
        self.assertEqual({"app": GAME_MASTER_APP}, game_master["spec"]["podSelector"]["matchLabels"])
        actual = {
            (
                rule["to"][0]["podSelector"]["matchLabels"]["app"],
                rule["ports"][0]["port"],
            )
            for rule in game_master["spec"]["egress"]
        }
        self.assertEqual(
            {
                ("tle-dev-nats", 4222),
                ("tle-dev-valkey", 6379),
                ("tle-dev-player-service", 9090),
            },
            actual,
        )

    def test_credentials_are_projected_and_shared_with_only_the_required_callers(self):
        secrets = self.resources_by_kind(
            "apps/tle-dev/config/vault-secrets.yaml", "VaultStaticSecret"
        )
        templates = secrets["tle-dev-misc"]["spec"]["destination"]["transformation"]["templates"]
        self.assertIn("GAME_MASTER_POD_REGISTRY_CREDENTIAL", templates)
        self.assertIn("GAME_MASTER_MATCHMAKING_CREDENTIAL", templates)

        game_master = self.resources_by_kind(
            "apps/tle-dev/config/game-master.yaml", "Deployment"
        )[GAME_MASTER_APP]
        gm_env = {
            item["name"]: item
            for item in game_master["spec"]["template"]["spec"]["containers"][0]["env"]
        }
        for name in (
            "GAME_MASTER_POD_REGISTRY_CREDENTIAL",
            "GAME_MASTER_MATCHMAKING_CREDENTIAL",
        ):
            self.assertEqual(
                {"name": "tle-dev-misc-secrets", "key": name},
                gm_env[name]["valueFrom"]["secretKeyRef"],
            )

        workloads = self.resources_by_kind(
            "apps/tle-dev/config/workloads.yaml", "Deployment"
        )
        matchmaking_env = {
            item["name"]: item
            for item in workloads["tle-dev-matchmaking-service"]["spec"]["template"]["spec"]
            ["containers"][0]["env"]
        }
        self.assertEqual(
            {
                "name": "tle-dev-misc-secrets",
                "key": "GAME_MASTER_MATCHMAKING_CREDENTIAL",
            },
            matchmaking_env["GAME_MASTER_MATCHMAKING_CREDENTIAL"]["valueFrom"]["secretKeyRef"],
        )
        self.assertNotIn("GAME_MASTER_POD_REGISTRY_CREDENTIAL", matchmaking_env)

    def test_game_master_is_in_the_tle_render(self):
        kustomization = self.load_documents("apps/tle-dev/config/kustomization.yaml")[0]
        self.assertIn("game-master.yaml", kustomization["resources"])


if __name__ == "__main__":
    unittest.main()
