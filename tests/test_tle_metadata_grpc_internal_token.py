"""Contract tests for issue #59: provision the Metadata Service gRPC token.

Scope is narrowed to Metadata Service provisioning only (see the issue
comment thread): caller-side wiring for Game Service, Matchmaking, and
Lobby waits on a published The-Last-Exam/server contract and is out of
scope here.
"""

from pathlib import Path
import unittest

import yaml


ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "apps/tle-dev/config"
METADATA_APP = "tle-dev-metadata-service"


def documents(name):
    with (CONFIG / name).open(encoding="utf-8") as stream:
        return [item for item in yaml.safe_load_all(stream) if item]


class TleMetadataGrpcInternalTokenTests(unittest.TestCase):
    def _metadata_container(self):
        deployment = next(
            item
            for item in documents("workloads.yaml")
            if item["kind"] == "Deployment"
            and item["metadata"]["name"] == METADATA_APP
        )
        return deployment["spec"]["template"]["spec"]["containers"][0]

    def test_metadata_service_runs_as_the_production_environment(self):
        # "dev" is the server's explicit local-development escape hatch that
        # accepts gRPC callers even without METADATA_GRPC_INTERNAL_TOKEN; the
        # only Metadata Service deployment in this single-environment
        # cluster must not run with it.
        container = self._metadata_container()
        env = {item["name"]: item for item in container["env"]}
        self.assertEqual("prod", env["METADATA_ENV"]["value"])

    def test_metadata_service_grpc_internal_token_comes_from_a_secret(self):
        container = self._metadata_container()
        env = {item["name"]: item for item in container["env"]}
        token = env["METADATA_GRPC_INTERNAL_TOKEN"]
        self.assertNotIn("value", token)
        self.assertEqual(
            {"name": "tle-dev-misc-secrets", "key": "METADATA_GRPC_INTERNAL_TOKEN"},
            token["valueFrom"]["secretKeyRef"],
        )

    def test_token_is_never_rendered_as_a_literal_or_configmap_value(self):
        # Acceptance criterion: the token must not appear in rendered
        # manifests or logs. Assert it only ever flows through a
        # secretKeyRef by scanning every ConfigMap data value and every
        # container's literal env "value" across the rendered app.
        kustomization = documents("kustomization.yaml")[0]
        for name in kustomization["resources"]:
            for resource in documents(name):
                if resource["kind"] == "ConfigMap":
                    for value in (resource.get("data") or {}).values():
                        self.assertNotIn("METADATA_GRPC_INTERNAL_TOKEN", value)
                if resource["kind"] in ("Deployment", "Job", "StatefulSet"):
                    containers = resource["spec"]["template"]["spec"]["containers"]
                    for container in containers:
                        for item in container.get("env") or []:
                            if "value" in item:
                                self.assertNotIn(
                                    "METADATA_GRPC_INTERNAL_TOKEN", str(item["value"])
                                )

    def test_misc_secret_restarts_metadata_service_on_rotation(self):
        secret = next(
            item
            for item in documents("vault-secrets.yaml")
            if item["kind"] == "VaultStaticSecret"
            and item["metadata"]["name"] == "tle-dev-misc"
        )
        self.assertIn(
            {"kind": "Deployment", "name": METADATA_APP},
            secret["spec"]["rolloutRestartTargets"],
        )
        templates = secret["spec"]["destination"]["transformation"]["templates"]
        self.assertIn("METADATA_GRPC_INTERNAL_TOKEN", templates)


if __name__ == "__main__":
    unittest.main()
