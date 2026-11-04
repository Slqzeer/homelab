"""Contract tests for TLE workload identities declared by pinned images."""

from pathlib import Path
import unittest

import yaml


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]

DISTROLESS_DEPLOYMENTS = {
    "tle-dev-game-master",
    "tle-dev-lobby-service",
    "tle-dev-matchmaking-service",
    "tle-dev-metadata-service",
    "tle-dev-notification-service",
    "tle-dev-social-service",
}


class TleRuntimeIdentityTests(unittest.TestCase):
    def load_deployments(self):
        deployments = {}
        for relative_path in (
            "apps/tle-dev/config/workloads.yaml",
            "apps/tle-dev/config/game-master.yaml",
        ):
            with (REPOSITORY_ROOT / relative_path).open(encoding="utf-8") as stream:
                for document in yaml.safe_load_all(stream):
                    if document and document["kind"] == "Deployment":
                        deployments[document["metadata"]["name"]] = document
        return deployments

    def test_distroless_workloads_use_their_image_runtime_identity(self):
        deployments = self.load_deployments()

        for name in DISTROLESS_DEPLOYMENTS:
            with self.subTest(name=name):
                security_context = deployments[name]["spec"]["template"]["spec"][
                    "securityContext"
                ]
                self.assertTrue(security_context["runAsNonRoot"])
                self.assertEqual(65532, security_context["runAsUser"])
                self.assertEqual(65532, security_context["runAsGroup"])

    def test_non_distroless_workloads_keep_their_numeric_runtime_identity(self):
        deployments = self.load_deployments()

        for name in ("tle-dev-auth-service", "tle-dev-player-service"):
            with self.subTest(name=name):
                security_context = deployments[name]["spec"]["template"]["spec"][
                    "securityContext"
                ]
                self.assertTrue(security_context["runAsNonRoot"])
                self.assertEqual(10001, security_context["runAsUser"])


if __name__ == "__main__":
    unittest.main()
