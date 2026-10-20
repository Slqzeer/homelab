"""Contracts for Qdrant and native-protocol tailnet exposure."""

from pathlib import Path
import unittest

import yaml


ROOT = Path(__file__).resolve().parents[1]


def load(path):
    with (ROOT / path).open(encoding="utf-8") as stream:
        return [document for document in yaml.safe_load_all(stream) if document]


class DatabaseTailnetServiceTests(unittest.TestCase):
    def test_qdrant_application_and_persistent_workload(self):
        application, = load("environments/homelab/apps/qdrant.yaml")
        self.assertEqual("qdrant", application["metadata"]["name"])
        self.assertEqual(
            "23", application["metadata"]["annotations"]["argocd.argoproj.io/sync-wave"]
        )
        self.assertEqual(
            "platform/databases/qdrant/config", application["spec"]["source"]["path"]
        )

        resources = load("platform/databases/qdrant/config/qdrant.yaml")
        stateful_set, = [item for item in resources if item["kind"] == "StatefulSet"]
        pod = stateful_set["spec"]["template"]["spec"]
        self.assertFalse(pod["automountServiceAccountToken"])
        container, = pod["containers"]
        self.assertEqual("qdrant/qdrant:v1.19.2-unprivileged", container["image"])
        self.assertTrue(container["securityContext"]["readOnlyRootFilesystem"])
        self.assertEqual(["ALL"], container["securityContext"]["capabilities"]["drop"])
        env = {item["name"]: item for item in container["env"]}
        secret_ref = env["QDRANT__SERVICE__API_KEY"]["valueFrom"]["secretKeyRef"]
        self.assertEqual(
            {"name": "qdrant-credentials", "key": "api-key"}, secret_ref
        )
        claim, = stateful_set["spec"]["volumeClaimTemplates"]
        self.assertEqual("local-path", claim["spec"]["storageClassName"])
        self.assertEqual("20Gi", claim["spec"]["resources"]["requests"]["storage"])

    def test_qdrant_tailnet_service_exposes_client_ports_only(self):
        resources = load("platform/databases/qdrant/config/qdrant.yaml")
        service, = [
            item for item in resources
            if item["kind"] == "Service" and item["metadata"]["name"] == "qdrant-tailnet"
        ]
        self.assertEqual("LoadBalancer", service["spec"]["type"])
        self.assertEqual("tailscale", service["spec"]["loadBalancerClass"])
        self.assertEqual(
            {"tailscale.com/proxy-group": "ingress", "tailscale.com/hostname": "qdrant"},
            service["metadata"]["annotations"],
        )
        self.assertEqual({6333, 6334}, {port["port"] for port in service["spec"]["ports"]})

        policy, = load("platform/databases/qdrant/config/networkpolicy.yaml")
        rule, = policy["spec"]["ingress"]
        peer, = rule["from"]
        self.assertEqual(
            "tailscale", peer["namespaceSelector"]["matchLabels"][
                "kubernetes.io/metadata.name"
            ]
        )
        self.assertEqual(
            {
                "tailscale.com/parent-resource": "ingress",
                "tailscale.com/parent-resource-type": "proxygroup",
            },
            peer["podSelector"]["matchLabels"],
        )
        self.assertEqual({6333, 6334}, {port["port"] for port in rule["ports"]})

    def test_qdrant_credential_is_vault_managed_and_rotates_workload(self):
        resources = load("platform/databases/qdrant/config/vault-secrets.yaml")
        auth, = [item for item in resources if item["kind"] == "VaultAuth"]
        self.assertEqual("vso-qdrant", auth["spec"]["kubernetes"]["role"])
        self.assertEqual("qdrant", auth["spec"]["kubernetes"]["serviceAccount"])
        self.assertEqual(["vault"], auth["spec"]["kubernetes"]["audiences"])

        secret, = [item for item in resources if item["kind"] == "VaultStaticSecret"]
        self.assertEqual("qdrant", secret["spec"]["path"])
        self.assertEqual(
            [{"kind": "StatefulSet", "name": "qdrant"}],
            secret["spec"]["rolloutRestartTargets"],
        )
        configure = (ROOT / "platform/vault/configure-vault.sh").read_text(encoding="utf-8")
        for value in (
            "homelab/qdrant",
            'path "homelab/data/qdrant"',
            "auth/kubernetes/role/vso-qdrant",
            "bound_service_account_names=qdrant",
        ):
            self.assertIn(value, configure)

    def test_redis_is_exposed_through_the_same_private_proxy_group(self):
        resources = load("platform/databases/redis/config/redis.yaml")
        service, = [
            item for item in resources
            if item["kind"] == "Service" and item["metadata"]["name"] == "redis-tailnet"
        ]
        self.assertEqual("tailscale", service["spec"]["loadBalancerClass"])
        self.assertEqual("ingress", service["metadata"]["annotations"]["tailscale.com/proxy-group"])
        self.assertEqual("redis", service["metadata"]["annotations"]["tailscale.com/hostname"])
        self.assertEqual([6379], [port["port"] for port in service["spec"]["ports"]])

        policies = load("platform/databases/redis/config/networkpolicy.yaml")
        clients, = [item for item in policies if item["metadata"]["name"] == "redis-clients"]
        peers = clients["spec"]["ingress"][0]["from"]
        tailnet_peers = [
            peer for peer in peers
            if peer.get("namespaceSelector", {}).get("matchLabels", {}).get(
                "kubernetes.io/metadata.name"
            ) == "tailscale"
        ]
        self.assertEqual(1, len(tailnet_peers))
        self.assertEqual(
            "proxygroup",
            tailnet_peers[0]["podSelector"]["matchLabels"][
                "tailscale.com/parent-resource-type"
            ],
        )


if __name__ == "__main__":
    unittest.main()
