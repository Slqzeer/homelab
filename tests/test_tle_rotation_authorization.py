"""Static contracts for TLE signing-key rotation authorization."""

from pathlib import Path
import unittest

import yaml


ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "apps/tle-dev/config"


def resources(name):
    return [
        document
        for document in yaml.safe_load_all((CONFIG / name).read_text(encoding="utf-8"))
        if document
    ]


def resource(name, kind, resource_name):
    matches = [
        document
        for document in resources(name)
        if document["kind"] == kind
        and document["metadata"]["name"] == resource_name
    ]
    if len(matches) != 1:
        raise AssertionError(
            f"expected one {kind}/{resource_name} in {name}, found {len(matches)}"
        )
    return matches[0]


class TleRotationAuthorizationTests(unittest.TestCase):
    def test_auth_service_consumes_rotation_keycloak_contract(self):
        edge_config = resource(
            "edge-config.yaml", "ConfigMap", "tle-dev-edge-config"
        )["data"]
        deployment = resource(
            "workloads.yaml", "Deployment", "tle-dev-auth-service"
        )
        container = deployment["spec"]["template"]["spec"]["containers"][0]
        env = {entry["name"]: entry for entry in container["env"]}

        expected = {
            "KEYCLOAK_ISSUER": "keycloak-issuer",
            "KEYCLOAK_OPERATOR_CLIENT_ID": "keycloak-operator-client-id",
            "KEYCLOAK_JWKS_URI": "keycloak-jwks-uri",
        }
        self.assertEqual(
            "https://keycloak.taildf6cd4.ts.net/realms/homelab",
            edge_config["keycloak-issuer"],
        )
        self.assertEqual("tle-admin-dev", edge_config["keycloak-operator-client-id"])
        # #4: auth-service verifies operator tokens against Keycloak's RS256
        # JWKS, never against its own Ed25519 JWKS (#99).
        self.assertEqual(
            "http://keycloak.keycloak.svc.cluster.local:8080/realms/homelab/"
            "protocol/openid-connect/certs",
            edge_config["keycloak-jwks-uri"],
        )
        for env_name, config_key in expected.items():
            with self.subTest(env_name=env_name):
                self.assertEqual(
                    {
                        "configMapKeyRef": {
                            "name": "tle-dev-edge-config",
                            "key": config_key,
                        }
                    },
                    env[env_name]["valueFrom"],
                )

        edge = resource("edge-config.yaml", "Deployment", "tle-dev-edge")
        revision = "split-jwks-v1"
        for workload in (deployment, edge):
            with self.subTest(workload=workload["metadata"]["name"]):
                self.assertEqual(
                    revision,
                    workload["spec"]["template"]["metadata"]["annotations"][
                        "homelab.io/jwks-config-revision"
                    ],
                )

    def test_gateway_player_jwks_is_auth_service_through_its_own_key(self):
        # #99 phase 1: the gateway verifies player tokens minted by
        # auth-service, through a ConfigMap key it shares with no one.
        edge_config = resource(
            "edge-config.yaml", "ConfigMap", "tle-dev-edge-config"
        )["data"]
        self.assertEqual(
            "http://tle-dev-auth-service.tle-dev.svc.cluster.local:8080/"
            ".well-known/jwks.json",
            edge_config["auth-jwks-uri"],
        )
        edge = resource("edge-config.yaml", "Deployment", "tle-dev-edge")
        env = {
            entry["name"]: entry
            for entry in edge["spec"]["template"]["spec"]["containers"][0]["env"]
        }
        self.assertEqual(
            {"configMapKeyRef": {"name": "tle-dev-edge-config", "key": "auth-jwks-uri"}},
            env["AUTH_JWKS_URI"]["valueFrom"],
        )
        # server#261 (v0.0.3): the gateway no longer reads Keycloak settings.
        self.assertFalse([name for name in env if name.startswith("KEYCLOAK_")])

        auth = resource("workloads.yaml", "Deployment", "tle-dev-auth-service")
        auth_env = auth["spec"]["template"]["spec"]["containers"][0]["env"]
        for entry in auth_env:
            with self.subTest(env_name=entry["name"]):
                self.assertNotEqual(
                    "auth-jwks-uri",
                    entry.get("valueFrom", {}).get("configMapKeyRef", {}).get("key"),
                )

    def test_auth_service_keycloak_network_flow_remains_narrow(self):
        policies = {
            document["metadata"]["name"]: document
            for document in resources("networkpolicy.yaml")
            if document["kind"] == "NetworkPolicy"
        }
        egress = policies["tle-dev-auth-to-keycloak"]
        self.assertEqual(
            {"app": "tle-dev-auth-service"}, egress["spec"]["podSelector"]["matchLabels"]
        )
        self.assertEqual(
            [{"protocol": "TCP", "port": 8080}], egress["spec"]["egress"][0]["ports"]
        )

        ingress = policies["tle-dev-keycloak-from-auth"]
        peers = ingress["spec"]["ingress"][0]["from"]
        auth_peers = [
            peer
            for peer in peers
            if peer.get("podSelector", {}).get("matchLabels", {}).get("app")
            == "tle-dev-auth-service"
        ]
        self.assertEqual(1, len(auth_peers))
        self.assertEqual(
            {"kubernetes.io/metadata.name": "tle-dev"},
            auth_peers[0]["namespaceSelector"]["matchLabels"],
        )
        self.assertEqual(
            [{"protocol": "TCP", "port": 8080}], ingress["spec"]["ingress"][0]["ports"]
        )


if __name__ == "__main__":
    unittest.main()
