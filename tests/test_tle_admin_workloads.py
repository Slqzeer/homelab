"""Contract tests for the TLE dev administrative workloads."""

from pathlib import Path
import re
import unittest

import yaml


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
WORKLOAD_PATH = "apps/tle-dev/config/admin-workloads.yaml"
ADMIN_NAMESPACE = "tle-admin-dev"

EXPECTED = {
    "tle-dev-admin-audit-writer": {
        "image": (
            "ghcr.io/the-last-exam/admin-audit-writer:v0.0.2@sha256:"
            "7c0da00a399b089cf4387cc8474395a6c27729397a292792eb09b479b3f48078"
        ),
        "replicas": 2,
        "probes": ("/healthz", "/healthz", "/readyz"),
        "requests": {"cpu": "100m", "memory": "64Mi"},
        "limits": {"cpu": "500m", "memory": "256Mi"},
        "hpa": (2, 4),
    },
    "tle-dev-internal-admin-gateway": {
        "image": (
            "ghcr.io/the-last-exam/internal-admin-gateway:v0.0.2@sha256:"
            "1048cd10d5c7859169231876e20e76329f56a23b6e05ef0cec61db1a41ac4736"
        ),
        "replicas": 2,
        "probes": (None, "/healthz", "/readyz"),
        "requests": {"cpu": "100m", "memory": "128Mi"},
        "limits": {"cpu": "500m", "memory": "256Mi"},
        "hpa": (2, 6),
    },
    "tle-dev-internal-ops-gateway": {
        "image": (
            "ghcr.io/the-last-exam/internal-ops-gateway:v0.0.2@sha256:"
            "903ce62cef35744b98878678cd0b0807cc6bf684d73e22a028135fa13a40f46e"
        ),
        "replicas": 2,
        "probes": (None, "/healthz", "/readyz"),
        "requests": {"cpu": "50m", "memory": "64Mi"},
        "limits": {"cpu": "200m", "memory": "128Mi"},
        "hpa": (2, 5),
    },
    "tle-dev-projection-writer": {
        "image": (
            "ghcr.io/the-last-exam/projection-writer:v0.0.2@sha256:"
            "8287bdcb383c388b7bba1010c5cdfa20082c87e154a3c25460e016231a2a65b1"
        ),
        "replicas": 2,
        "probes": (None, "/healthz", "/readyz"),
        "requests": {"cpu": "50m", "memory": "64Mi"},
        "limits": {"cpu": "250m", "memory": "256Mi"},
        "hpa": (2, 5),
    },
}


class TleAdminWorkloadTests(unittest.TestCase):
    def load_documents(self, relative_path=WORKLOAD_PATH):
        with (REPOSITORY_ROOT / relative_path).open(encoding="utf-8") as stream:
            return [document for document in yaml.safe_load_all(stream) if document]

    def resources_by_kind(self, kind):
        return {
            resource["metadata"]["name"]: resource
            for resource in self.load_documents()
            if resource["kind"] == kind
        }

    def test_workload_table_is_declared_exactly(self):
        deployments = self.resources_by_kind("Deployment")
        hpas = self.resources_by_kind("HorizontalPodAutoscaler")

        self.assertEqual(set(EXPECTED), set(deployments))
        self.assertEqual(set(EXPECTED), set(hpas))

        for name, expected in EXPECTED.items():
            with self.subTest(name=name):
                deployment = deployments[name]
                self.assertEqual(ADMIN_NAMESPACE, deployment["metadata"]["namespace"])
                self.assertEqual(expected["replicas"], deployment["spec"]["replicas"])
                container = deployment["spec"]["template"]["spec"]["containers"][0]
                self.assertEqual(expected["image"], container["image"])
                self.assertRegex(
                    container["image"],
                    re.compile(r"^.+:v\d+\.\d+\.\d+@sha256:[0-9a-f]{64}$"),
                )
                self.assertEqual(expected["requests"], container["resources"]["requests"])
                self.assertEqual(expected["limits"], container["resources"]["limits"])

                actual_probes = tuple(
                    container.get(probe, {}).get("httpGet", {}).get("path")
                    for probe in ("startupProbe", "livenessProbe", "readinessProbe")
                )
                self.assertEqual(expected["probes"], actual_probes)

                hpa = hpas[name]
                self.assertEqual(ADMIN_NAMESPACE, hpa["metadata"]["namespace"])
                self.assertEqual(expected["hpa"][0], hpa["spec"]["minReplicas"])
                self.assertEqual(expected["hpa"][1], hpa["spec"]["maxReplicas"])
                self.assertEqual(name, hpa["spec"]["scaleTargetRef"]["name"])

    def test_admin_rollout_cannot_gate_the_player_facing_waves(self):
        wave = lambda resource: int(
            (resource["metadata"].get("annotations") or {}).get(
                "argocd.argoproj.io/sync-wave", "0"
            )
        )
        kustomization = self.load_documents("apps/tle-dev/config/kustomization.yaml")[0]
        tle_dev_waves = [
            wave(resource)
            for name in kustomization["resources"]
            if not name.startswith("admin-")
            for resource in self.load_documents(f"apps/tle-dev/config/{name}")
        ]
        for kind in ("Deployment", "HorizontalPodAutoscaler"):
            for name, resource in self.resources_by_kind(kind).items():
                with self.subTest(kind=kind, name=name):
                    self.assertGreater(wave(resource), max(tle_dev_waves))

    def test_gateways_run_in_an_environment_v0_0_2_accepts(self):
        deployments = self.resources_by_kind("Deployment")
        for name, variable in (
            ("tle-dev-internal-admin-gateway", "ADMIN_GATEWAY_ENV"),
            ("tle-dev-internal-ops-gateway", "OPS_GATEWAY_ENV"),
        ):
            with self.subTest(name=name):
                env = {
                    item["name"]: item.get("value")
                    for item in deployments[name]["spec"]["template"]["spec"]["containers"][0]["env"]
                }
                self.assertEqual("staging", env[variable])

    def test_admin_namespace_is_restricted_and_separate(self):
        namespaces = self.load_documents("bootstrap/namespaces/namespaces.yaml")
        namespace = next(
            resource
            for resource in namespaces
            if resource["kind"] == "Namespace"
            and resource["metadata"]["name"] == ADMIN_NAMESPACE
        )
        labels = namespace["metadata"]["labels"]
        for mode in ("enforce", "audit", "warn"):
            self.assertEqual("restricted", labels[f"pod-security.kubernetes.io/{mode}"])
            self.assertEqual("v1.36", labels[f"pod-security.kubernetes.io/{mode}-version"])

        for resource in self.load_documents():
            self.assertEqual(ADMIN_NAMESPACE, resource["metadata"]["namespace"])

    def test_admin_resources_and_secret_projections_are_rendered(self):
        kustomization = self.load_documents("apps/tle-dev/config/kustomization.yaml")[0]
        self.assertIn("admin-workloads.yaml", kustomization["resources"])
        self.assertIn("admin-secrets.yaml", kustomization["resources"])
        self.assertIn("admin-networkpolicy.yaml", kustomization["resources"])

        secrets = self.load_documents("apps/tle-dev/config/admin-secrets.yaml")
        vault_secrets = {
            resource["metadata"]["name"]: resource
            for resource in secrets
            if resource["kind"] == "VaultStaticSecret"
        }
        self.assertEqual(
            {
                "tle-admin-dev-data",
                "tle-admin-dev-misc",
                "tle-admin-dev-oidc",
                "tle-admin-dev-ghcr-pull",
            },
            set(vault_secrets),
        )
        for secret in vault_secrets.values():
            transformation = secret["spec"]["destination"]["transformation"]
            self.assertTrue(transformation["excludeRaw"])

    def test_admin_namespace_starts_default_denied(self):
        policies = self.load_documents("apps/tle-dev/config/admin-networkpolicy.yaml")
        self.assertEqual(
            {"default-deny-ingress", "default-deny-egress"},
            {policy["metadata"]["name"] for policy in policies},
        )
        for policy in policies:
            self.assertEqual(ADMIN_NAMESPACE, policy["metadata"]["namespace"])
            self.assertEqual({}, policy["spec"]["podSelector"])
            self.assertNotIn("ingress", policy["spec"])
            self.assertNotIn("egress", policy["spec"])

    def test_workloads_use_restricted_distroless_identity(self):
        for deployment in self.resources_by_kind("Deployment").values():
            pod_spec = deployment["spec"]["template"]["spec"]
            self.assertFalse(pod_spec["automountServiceAccountToken"])
            self.assertEqual(65532, pod_spec["securityContext"]["runAsUser"])
            self.assertEqual(65532, pod_spec["securityContext"]["runAsGroup"])
            container_security = pod_spec["containers"][0]["securityContext"]
            self.assertFalse(container_security["allowPrivilegeEscalation"])
            self.assertTrue(container_security["readOnlyRootFilesystem"])
            self.assertEqual(["ALL"], container_security["capabilities"]["drop"])


if __name__ == "__main__":
    unittest.main()
