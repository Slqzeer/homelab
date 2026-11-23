"""Contract tests for the tle-dev settings The-Last-Exam/server v0.0.3 needs.

Every setting here is ignored by the pinned v0.0.2 images, so the desired
state can land before the v0.0.3 image digests are pinned:

- server#261: the API gateway verifies player tokens with AUTH_JWKS_URI,
  AUTH_TOKEN_ISSUER and AUTH_TOKEN_AUDIENCE; auth-service mints that iss/aud
  and quest-service must require the same pair and signing key.
- server#172: admin-audit-writer and internal-admin-gateway share one
  AUDIT_SERVICE_CREDENTIAL bearer.
- server#263: player-service reads starter loadouts from metadata-service.
- server#305: metadata-service no longer uses MinIO (issue #96).
"""

from pathlib import Path
import unittest

import yaml


ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "apps/tle-dev/config"


def documents(name):
    with (CONFIG / name).open(encoding="utf-8") as stream:
        return [item for item in yaml.safe_load_all(stream) if item]


def find(name, kind, resource_name):
    return next(
        item
        for item in documents(name)
        if item["kind"] == kind and item["metadata"]["name"] == resource_name
    )


def container_env(name, deployment):
    spec = find(name, "Deployment", deployment)["spec"]["template"]["spec"]
    return {item["name"]: item for item in spec["containers"][0]["env"]}


def edge_config():
    return find("edge-config.yaml", "ConfigMap", "tle-dev-edge-config")["data"]


def config_ref(key):
    return {"configMapKeyRef": {"name": "tle-dev-edge-config", "key": key}}


class PlayerTokenClaimsTests(unittest.TestCase):
    def test_edge_sets_the_v003_player_token_settings(self):
        env = container_env("edge-config.yaml", "tle-dev-edge")
        self.assertEqual(config_ref("auth-jwks-uri"), env["AUTH_JWKS_URI"]["valueFrom"])
        self.assertEqual(
            config_ref("auth-token-issuer"), env["AUTH_TOKEN_ISSUER"]["valueFrom"]
        )
        self.assertEqual(
            config_ref("auth-token-audience"), env["AUTH_TOKEN_AUDIENCE"]["valueFrom"]
        )

    def test_auth_service_mints_the_same_issuer_and_audience(self):
        env = container_env("workloads.yaml", "tle-dev-auth-service")
        self.assertEqual(
            config_ref("auth-token-issuer"), env["AUTH_TOKEN_ISSUER"]["valueFrom"]
        )
        self.assertEqual(
            config_ref("auth-token-audience"), env["AUTH_TOKEN_AUDIENCE"]["valueFrom"]
        )

    def test_quest_service_requires_the_auth_service_claims_and_key(self):
        edge = edge_config()
        quest = find("quest-workloads.yaml", "ConfigMap", "tle-dev-quest-runtime")["data"]
        self.assertEqual(edge["auth-token-issuer"], quest["QUEST_PLAYER_JWT_ISSUER"])
        self.assertEqual(edge["auth-token-audience"], quest["QUEST_PLAYER_JWT_AUDIENCE"])
        self.assertEqual(
            edge["auth-jwt-ed25519-public-key-pem"].strip(),
            quest["QUEST_PLAYER_JWT_PUBLIC_KEY_PEM"].strip(),
        )


class AuditServiceCredentialTests(unittest.TestCase):
    KEY = "AUDIT_SERVICE_CREDENTIAL"
    # admin-audit-writer checks it; both gateways present it (server#172, #327).
    CONSUMERS = (
        "tle-dev-admin-audit-writer",
        "tle-dev-internal-admin-gateway",
        "tle-dev-internal-ops-gateway",
    )

    def test_vault_projection_carries_the_credential_and_restarts_both(self):
        spec = find("admin-secrets.yaml", "VaultStaticSecret", "tle-admin-dev-misc")["spec"]
        self.assertEqual("tle-dev/misc", spec["path"])
        self.assertEqual(
            {"text": '{{ get .Secrets "AUDIT_SERVICE_CREDENTIAL" }}'},
            spec["destination"]["transformation"]["templates"][self.KEY],
        )
        for name in self.CONSUMERS:
            self.assertIn({"kind": "Deployment", "name": name}, spec["rolloutRestartTargets"])

    def test_gateway_and_writer_read_the_same_secret_key(self):
        for deployment in self.CONSUMERS:
            item = container_env("admin-workloads.yaml", deployment)[self.KEY]
            self.assertNotIn("value", item)
            self.assertEqual(
                {"name": "tle-admin-dev-misc-secrets", "key": self.KEY},
                item["valueFrom"]["secretKeyRef"],
            )

    def test_ceremony_generates_the_credential(self):
        script = (ROOT / "platform/vault/configure-vault.sh").read_text(encoding="utf-8")
        self.assertIn('"AUDIT_SERVICE_CREDENTIAL 48"; do', script)


class PlayerMetadataTests(unittest.TestCase):
    def test_player_service_targets_metadata_http_and_grpc(self):
        env = container_env("workloads.yaml", "tle-dev-player-service")
        self.assertEqual(
            "http://tle-dev-metadata-service.tle-dev.svc.cluster.local:8080",
            env["PLAYER_METADATA_SERVICE_BASE_URL"]["value"],
        )
        self.assertEqual(
            "tle-dev-metadata-service.tle-dev.svc.cluster.local:9091",
            env["PLAYER_METADATA_GRPC_ADDR"]["value"],
        )
        self.assertEqual(
            {"name": "tle-dev-misc-secrets", "key": "METADATA_GRPC_INTERNAL_TOKEN"},
            env["PLAYER_METADATA_GRPC_INTERNAL_TOKEN"]["valueFrom"]["secretKeyRef"],
        )
        spec = find("vault-secrets.yaml", "VaultStaticSecret", "tle-dev-misc")["spec"]
        self.assertIn(
            {"kind": "Deployment", "name": "tle-dev-player-service"},
            spec["rolloutRestartTargets"],
        )

    def test_network_policies_open_both_directions(self):
        ports = [{"protocol": "TCP", "port": 8080}, {"protocol": "TCP", "port": 9091}]
        egress = find("networkpolicy.yaml", "NetworkPolicy", "tle-dev-player-metadata-egress")
        self.assertEqual({"app": "tle-dev-player-service"}, egress["spec"]["podSelector"]["matchLabels"])
        self.assertEqual(
            [{"to": [{"podSelector": {"matchLabels": {"app": "tle-dev-metadata-service"}}}], "ports": ports}],
            egress["spec"]["egress"],
        )
        ingress = find("networkpolicy.yaml", "NetworkPolicy", "tle-dev-metadata-from-player")
        self.assertEqual({"app": "tle-dev-metadata-service"}, ingress["spec"]["podSelector"]["matchLabels"])
        self.assertEqual(
            [{"from": [{"podSelector": {"matchLabels": {"app": "tle-dev-player-service"}}}], "ports": ports}],
            ingress["spec"]["ingress"],
        )


class DurablePushConsumerRolloutTests(unittest.TestCase):
    # Services binding JetStream durable push consumers without a queue group
    # (js.Subscribe + nats.Durable in v0.0.3). A surged pod cannot bind while
    # the old one holds them, so each must run one pod and use Recreate.
    SERVICES = {
        "tle-dev-lobby-service": "workloads.yaml",
        "tle-dev-player-service": "workloads.yaml",
        "tle-dev-projection-writer": "admin-workloads.yaml",
    }

    def test_each_recreates_a_single_pod(self):
        for name, manifest in self.SERVICES.items():
            with self.subTest(name=name):
                spec = find(manifest, "Deployment", name)["spec"]
                self.assertEqual(1, spec["replicas"])
                self.assertEqual({"type": "Recreate"}, spec["strategy"])
                for hpa in documents(manifest):
                    if hpa["kind"] == "HorizontalPodAutoscaler" and hpa["spec"]["scaleTargetRef"]["name"] == name:
                        self.assertEqual(1, hpa["spec"]["maxReplicas"])

class MinioRemovalTests(unittest.TestCase):
    def test_no_tle_dev_manifest_or_ceremony_mentions_minio_keys(self):
        paths = [CONFIG / name for name in documents("kustomization.yaml")[0]["resources"]]
        paths.append(ROOT / "platform/vault/configure-vault.sh")
        paths.append(ROOT / "apps/tle-dev/vault-seed/.env.tle-dev-misc.example")
        for path in paths:
            self.assertNotIn("MINIO_ACCESS_KEY", path.read_text(encoding="utf-8"), path)
            self.assertNotIn("MINIO_ENDPOINT", path.read_text(encoding="utf-8"), path)


if __name__ == "__main__":
    unittest.main()
