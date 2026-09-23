"""Contract tests for a rendered personal application and its site integration."""

from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

import yaml

from scripts.validate_application_onboarding import REPOSITORY_ROOT, validate_application


DIGEST = "a" * 64


class ApplicationOnboardingTests(unittest.TestCase):
    def setUp(self):
        self.directory = TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.application = {
            "apiVersion": "argoproj.io/v1alpha1",
            "kind": "Application",
            "metadata": {
                "name": "example",
                "namespace": "argocd",
                "annotations": {
                    "argocd.argoproj.io/sync-wave": "25",
                    "homelab.io/onboarding-contract": "v1",
                    "homelab.io/owner": "personal-applications",
                    "homelab.io/state": "stateless",
                },
            },
            "spec": {
                "source": {
                    "repoURL": "https://github.com/example/example.git",
                    "targetRevision": "v1.2.3",
                    "path": "deploy/overlays/homelab",
                },
                "destination": {"namespace": "example"},
                "syncPolicy": {
                    "automated": {"prune": True, "selfHeal": True},
                    "syncOptions": ["ServerSideApply=true"],
                },
            },
        }
        self.namespace = {
            "apiVersion": "v1",
            "kind": "Namespace",
            "metadata": {
                "name": "example",
                "labels": {
                    "pod-security.kubernetes.io/enforce": "restricted",
                    "pod-security.kubernetes.io/enforce-version": "v1.36",
                    "pod-security.kubernetes.io/audit": "restricted",
                    "pod-security.kubernetes.io/audit-version": "v1.36",
                    "pod-security.kubernetes.io/warn": "restricted",
                    "pod-security.kubernetes.io/warn-version": "v1.36",
                },
            },
        }
        self.deployment = {
            "apiVersion": "apps/v1",
            "kind": "Deployment",
            "metadata": {"name": "example", "namespace": "example"},
            "spec": {
                "template": {
                    "spec": {"containers": [{"name": "web", "image": f"ghcr.io/example/example:v1.2.3@sha256:{DIGEST}"}]}
                }
            },
        }
        self.service = {
            "apiVersion": "v1",
            "kind": "Service",
            "metadata": {"name": "example", "namespace": "example"},
            "spec": {"ports": [{"name": "public", "port": 8080}, {"name": "operations", "port": 9000}]},
        }
        self.vso = {
            "apiVersion": "secrets.hashicorp.com/v1beta1",
            "kind": "VaultStaticSecret",
            "metadata": {"name": "example", "namespace": "example"},
            "spec": {"destination": {"transformation": {"excludeRaw": True}}},
        }
        self.policy = {
            "apiVersion": "networking.k8s.io/v1",
            "kind": "NetworkPolicy",
            "metadata": {"name": "default-deny", "namespace": "example"},
            "spec": {"podSelector": {}, "policyTypes": ["Ingress", "Egress"]},
        }
        self.additional_policies = []
        self.ingress = {
            "apiVersion": "networking.k8s.io/v1",
            "kind": "Ingress",
            "metadata": {
                "name": "example",
                "namespace": "example",
                "annotations": {
                    "tailscale.com/proxy-class": "homelab",
                    "portal.homelab.io/enabled": "true",
                    "portal.homelab.io/name": "Example",
                    "portal.homelab.io/description": "Example application",
                    "portal.homelab.io/category": "Personal",
                    "portal.homelab.io/icon": "example",
                    "portal.homelab.io/access": "groups",
                    "portal.homelab.io/groups": "homelab-users",
                    "portal.homelab.io/order": "20",
                },
            },
            "spec": {
                "ingressClassName": "tailscale",
                "defaultBackend": {"service": {"name": "example", "port": {"name": "public"}}},
                "tls": [{"hosts": ["example"]}],
            },
        }

    def validate(self):
        application_path = self.root / "application.yaml"
        rendered_path = self.root / "rendered.yaml"
        ingress_path = self.root / "ingress.yaml"
        application_path.write_text(yaml.safe_dump(self.application), encoding="utf-8")
        rendered_path.write_text(
            yaml.safe_dump_all(
                [self.namespace, self.deployment, self.service, self.vso, self.policy, *self.additional_policies]
            ),
            encoding="utf-8",
        )
        ingress_path.write_text(yaml.safe_dump(self.ingress), encoding="utf-8")
        return validate_application(application_path, [rendered_path], ingress_path)

    def test_accepts_valid_application(self):
        self.assertEqual([], self.validate())

    def test_rejects_floating_image(self):
        self.deployment["spec"]["template"]["spec"]["containers"][0]["image"] = "ghcr.io/example/example:latest"
        self.assertIn("deployment image must use an immutable sha256 digest", self.validate())

    def test_rejects_zero_digest(self):
        self.deployment["spec"]["template"]["spec"]["containers"][0]["image"] = (
            "ghcr.io/example/example:v1.2.3@sha256:" + "0" * 64
        )
        self.assertIn("deployment image must use an immutable sha256 digest", self.validate())

    def test_rejects_missing_restricted_namespace_labels(self):
        del self.namespace["metadata"]["labels"]["pod-security.kubernetes.io/enforce"]
        self.assertIn("namespace must have restricted Pod Security labels", self.validate())

    def test_rejects_vso_without_exclude_raw(self):
        self.vso["spec"]["destination"]["transformation"]["excludeRaw"] = False
        self.assertIn("VaultStaticSecret must set destination.transformation.excludeRaw: true", self.validate())

    def test_rejects_missing_default_deny(self):
        self.policy["spec"]["podSelector"] = {"matchLabels": {"app": "example"}}
        self.assertIn("namespace requires default-deny ingress and egress NetworkPolicy", self.validate())

    def test_rejects_missing_proxy_class(self):
        del self.ingress["metadata"]["annotations"]["tailscale.com/proxy-class"]
        self.assertIn("ingress must use tailscale.com/proxy-class: homelab", self.validate())

    def test_rejects_published_ingress_without_access(self):
        del self.ingress["metadata"]["annotations"]["portal.homelab.io/access"]
        self.assertIn("published ingress requires portal.homelab.io/access", self.validate())

    def test_rejects_exposed_operations_port(self):
        self.ingress["spec"]["defaultBackend"]["service"]["port"] = {"name": "operations"}
        self.assertIn("ingress must not expose an operations port", self.validate())

    def test_rejects_state_without_restore_runbook(self):
        self.application["metadata"]["annotations"]["homelab.io/state"] = "durable"
        self.assertIn("durable state requires backup.homelab.io/restore-runbook", self.validate())


    def test_rejects_floating_source_revision(self):
        self.application["spec"]["source"]["targetRevision"] = "main"
        self.assertIn("external source must use an immutable release revision", self.validate())

    def test_rejects_plaintext_secret_manifest(self):
        self.service["kind"] = "Secret"
        self.assertIn("rendered application must not contain a Secret manifest", self.validate())

    def test_rejects_restore_runbook_outside_repository(self):
        annotations = self.application["metadata"]["annotations"]
        annotations["homelab.io/state"] = "durable"
        annotations["backup.homelab.io/restore-runbook"] = "../../outside.md"
        self.assertIn("restore runbook must be a checked-in repository path", self.validate())

    def test_rejects_untracked_restore_runbook(self):
        with TemporaryDirectory(dir=REPOSITORY_ROOT) as directory:
            runbook = Path(directory) / "restore.md"
            runbook.write_text("Restore rehearsal", encoding="utf-8")
            annotations = self.application["metadata"]["annotations"]
            annotations["homelab.io/state"] = "durable"
            annotations["backup.homelab.io/restore-runbook"] = str(runbook.relative_to(REPOSITORY_ROOT))
            self.assertIn("restore runbook must be a checked-in repository path", self.validate())

    def test_rejects_malformed_yaml_as_error(self):
        self.validate()
        (self.root / "rendered.yaml").write_text("kind: [broken", encoding="utf-8")
        errors = validate_application(
            self.root / "application.yaml", [self.root / "rendered.yaml"], self.root / "ingress.yaml"
        )
        self.assertTrue(any(error.startswith("cannot parse ") for error in errors))


    def test_accepts_immutable_commit_revision(self):
        self.application["spec"]["source"]["targetRevision"] = "b" * 40
        self.assertEqual([], self.validate())

    def test_rejects_numeric_operations_port(self):
        self.ingress["spec"]["defaultBackend"]["service"]["port"] = {"number": 9000}
        self.assertIn("ingress must not expose an operations port", self.validate())

    def test_rejects_missing_sync_wave(self):
        del self.application["metadata"]["annotations"]["argocd.argoproj.io/sync-wave"]
        self.assertIn("application requires a numeric sync wave", self.validate())


    def test_rejects_missing_source(self):
        del self.application["spec"]["source"]
        self.assertIn("application requires a source with an immutable revision", self.validate())

    def test_rejects_missing_workload_image(self):
        self.deployment["kind"] = "ConfigMap"
        self.assertIn("rendered application requires a workload image", self.validate())

    def test_rejects_invalid_state_type_as_error(self):
        self.application["metadata"]["annotations"]["homelab.io/state"] = ["stateless"]
        self.assertIn("application requires homelab.io/state: stateless or durable", self.validate())


    def test_rejects_statefulset_volume_claim_template_as_stateless(self):
        self.deployment["kind"] = "StatefulSet"
        self.deployment["spec"]["volumeClaimTemplates"] = [{"metadata": {"name": "data"}}]
        self.assertIn("durable state requires homelab.io/state: durable", self.validate())

    def test_rejects_allow_all_policy_even_with_default_deny(self):
        self.additional_policies.append({
            "kind": "NetworkPolicy",
            "metadata": {"name": "allow-all", "namespace": "example"},
            "spec": {"podSelector": {}, "policyTypes": ["Ingress", "Egress"], "ingress": [{}], "egress": [{}]},
        })
        self.assertIn("network policy must not allow all ingress or egress", self.validate())

    def test_rejects_all_port_allow_rule(self):
        self.additional_policies.append({
            "kind": "NetworkPolicy",
            "metadata": {"name": "all-ports", "namespace": "example"},
            "spec": {"podSelector": {}, "ingress": [{"from": [{"podSelector": {"matchLabels": {"app": "proxy"}}}]}]},
        })
        self.assertIn("network policy must not allow all ingress or egress", self.validate())

    def test_rejects_unrestricted_peer_allow_rule(self):
        self.additional_policies.append({
            "kind": "NetworkPolicy",
            "metadata": {"name": "all-namespaces", "namespace": "example"},
            "spec": {"podSelector": {}, "ingress": [{"from": [{"namespaceSelector": {}}], "ports": [{"port": 8080}]}]},
        })
        self.assertIn("network policy must not allow all ingress or egress", self.validate())

    def test_rejects_semantically_empty_namespace_selector(self):
        self.additional_policies.append({
            "kind": "NetworkPolicy",
            "metadata": {"name": "empty-namespace-labels", "namespace": "example"},
            "spec": {"podSelector": {}, "ingress": [
                {"from": [{"namespaceSelector": {"matchLabels": {}}}], "ports": [{"port": 8080}]}
            ]},
        })
        self.assertIn("network policy must not allow all ingress or egress", self.validate())

    def test_rejects_semantically_empty_pod_selector(self):
        self.additional_policies.append({
            "kind": "NetworkPolicy",
            "metadata": {"name": "empty-pod-labels", "namespace": "example"},
            "spec": {"podSelector": {}, "egress": [
                {"to": [{"podSelector": {"matchLabels": {}}}], "ports": [{"port": 443}]}
            ]},
        })
        self.assertIn("network policy must not allow all ingress or egress", self.validate())

    def test_rejects_protocol_wide_full_port_range(self):
        self.additional_policies.append({
            "kind": "NetworkPolicy",
            "metadata": {"name": "full-tcp-range", "namespace": "example"},
            "spec": {"podSelector": {}, "ingress": [
                {"from": [{"podSelector": {"matchLabels": {"app": "proxy"}}}],
                 "ports": [{"protocol": "TCP", "port": 1, "endPort": 65535}]}
            ]},
        })
        self.assertIn("network policy must not allow all ingress or egress", self.validate())

    def test_rejects_operations_service_port_on_another_number(self):
        self.service["spec"]["ports"][1]["port"] = 9443
        self.ingress["spec"]["defaultBackend"]["service"]["port"] = {"number": 9443}
        self.assertIn("ingress must not expose an operations port", self.validate())


    def test_rejects_public_service_port_targeting_operations(self):
        self.service["spec"]["ports"][0]["targetPort"] = "operations"
        self.assertIn("ingress must not expose an operations port", self.validate())

if __name__ == "__main__":
    unittest.main()
