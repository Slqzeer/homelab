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
        self.assertEqual(
            "http://keycloak.keycloak.svc.cluster.local:8080/realms/"
            "homelab/protocol/openid-connect/certs",
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
