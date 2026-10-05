"""Contract tests for a rendered personal application and its site integration."""

from pathlib import Path
import shutil
import subprocess
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
                    "tailscale.com/proxy-group": "ingress",
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

    def test_rejects_missing_proxy_group(self):
        del self.ingress["metadata"]["annotations"]["tailscale.com/proxy-group"]
        self.assertIn("ingress must use tailscale.com/proxy-group: ingress", self.validate())

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

    def test_accepts_all_namespaces_with_selected_pods(self):
        self.additional_policies.append({
            "kind": "NetworkPolicy",
            "metadata": {"name": "selected-proxies", "namespace": "example"},
            "spec": {"podSelector": {}, "ingress": [
                {"from": [{"namespaceSelector": {}, "podSelector": {"matchLabels": {"app": "proxy"}}}],
                 "ports": [{"port": 8080}]}
            ]},
        })
        self.assertEqual([], self.validate())

    def test_accepts_selected_namespace_with_all_its_pods(self):
        self.additional_policies.append({
            "kind": "NetworkPolicy",
            "metadata": {"name": "monitoring-namespace", "namespace": "example"},
            "spec": {"podSelector": {}, "egress": [
                {"to": [{"namespaceSelector": {"matchLabels": {"kubernetes.io/metadata.name": "monitoring"}},
                         "podSelector": {}}],
                 "ports": [{"port": 443}]}
            ]},
        })
        self.assertEqual([], self.validate())

    def test_rejects_combined_unrestricted_selectors(self):
        self.additional_policies.append({
            "kind": "NetworkPolicy",
            "metadata": {"name": "universal-peer", "namespace": "example"},
            "spec": {"podSelector": {}, "ingress": [
                {"from": [{"namespaceSelector": {"matchLabels": {}}, "podSelector": {"matchLabels": {}}}],
                 "ports": [{"port": 8080}]}
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


class PortalRegistrationTests(unittest.TestCase):
    portal_revision = "783b15ef36d9b16b40c27aaff5c0ec17f0d69835"
    portal_tag = "sha-55659f8237fa5a2d0e5b79ab57268c011dc2dea1-36017826392-2"
    portal_digest = "sha256:a566f89422953f2d6365e124415cb29d7eb350ac2ccf4f176fbedbb0f78100ef"

    @staticmethod
    def render(path):
        kustomize = shutil.which("kustomize")
        if kustomize:
            command = [kustomize, "build", str(path)]
        else:
            kubectl = shutil.which("kubectl")
            if not kubectl:
                raise AssertionError("kustomize or kubectl is required for manifest tests")
            command = [kubectl, "kustomize", str(path)]
        result = subprocess.run(command, check=True, capture_output=True, text=True)
        return [document for document in yaml.safe_load_all(result.stdout) if document]

    @staticmethod
    def load_documents(path):
        with (REPOSITORY_ROOT / path).open(encoding="utf-8") as stream:
            return [document for document in yaml.safe_load_all(stream) if document]

    def test_portal_namespace_is_restricted_to_kubernetes_136(self):
        namespaces = self.load_documents("bootstrap/namespaces/namespaces.yaml")
        portal = next(document for document in namespaces if document.get("metadata", {}).get("name") == "portal")
        labels = portal["metadata"]["labels"]
        for mode in ("enforce", "audit", "warn"):
            self.assertEqual("restricted", labels[f"pod-security.kubernetes.io/{mode}"])
            self.assertEqual("v1.36", labels[f"pod-security.kubernetes.io/{mode}-version"])

    def test_portal_application_pins_release_and_site_values(self):
        application, = self.load_documents("environments/homelab/apps/homelab-portal.yaml")
        metadata = application["metadata"]
        self.assertEqual("25", metadata["annotations"]["argocd.argoproj.io/sync-wave"])
        self.assertEqual("v1", metadata["annotations"]["homelab.io/onboarding-contract"])
        self.assertEqual("personal-applications", metadata["annotations"]["homelab.io/owner"])
        self.assertEqual("stateless", metadata["annotations"]["homelab.io/state"])

        sources = application["spec"]["sources"]
        self.assertEqual(2, len(sources))
        portal = next(source for source in sources if source["repoURL"].endswith("homelab-portal.git"))
        self.assertEqual(self.portal_revision, portal["targetRevision"])
        self.assertEqual("deploy/overlays/homelab", portal["path"])
        patches = portal["kustomize"]["patches"]
        self.assertEqual(4, len(patches))
        patches_by_kind = {patch["target"]["kind"]: patch for patch in patches}
        self.assertEqual(
            {"group": "secrets.hashicorp.com", "version": "v1beta1",
             "kind": "VaultStaticSecret", "name": "homelab-portal"},
            patches_by_kind["VaultStaticSecret"]["target"],
        )
        self.assertEqual(
            {"version": "v1", "kind": "Namespace", "name": "portal"},
            patches_by_kind["Namespace"]["target"],
        )
        self.assertEqual(
            {"group": "apps", "version": "v1",
             "kind": "Deployment", "name": "homelab-portal"},
            patches_by_kind["Deployment"]["target"],
        )
        self.assertEqual(
            {"group": "networking.k8s.io", "version": "v1",
             "kind": "NetworkPolicy", "name": "homelab-portal"},
            patches_by_kind["NetworkPolicy"]["target"],
        )
        for patch in patches:
            parsed = yaml.safe_load(patch["patch"])
            if patch["target"]["kind"] == "Deployment":
                self.assertEqual(
                    [{"name": "ghcr-pull"}],
                    parsed["spec"]["template"]["spec"]["imagePullSecrets"],
                )
            elif patch["target"]["kind"] == "NetworkPolicy":
                # Admits the shared "ingress" ProxyGroup pool, scoped to the
                # tailscale namespace in the same peer.
                self.assertEqual(
                    [{
                        "op": "add",
                        "path": "/spec/ingress/0/from/-",
                        "value": {
                            "namespaceSelector": {"matchLabels": {"kubernetes.io/metadata.name": "tailscale"}},
                            "podSelector": {"matchLabels": {
                                "tailscale.com/parent-resource": "ingress",
                                "tailscale.com/parent-resource-type": "proxygroup",
                            }},
                        },
                    }],
                    parsed,
                )
            else:
                self.assertEqual("delete", parsed["$patch"])
        site = next(source for source in sources if source["repoURL"].endswith("homelab.git"))
        self.assertEqual("main", site["targetRevision"])
        self.assertEqual("apps/portal/config", site["path"])
        self.assertEqual(self.portal_revision, metadata["annotations"]["homelab.io/source-revision"])
        self.assertEqual(self.portal_tag, metadata["annotations"]["homelab.io/image-tag"])
        self.assertEqual(self.portal_digest, metadata["annotations"]["homelab.io/image-digest"])

        serialized = yaml.safe_dump(application, sort_keys=True)
        for placeholder in ("192.0.2.1", "198.51.100.1", "registry.example", "example.ts.net"):
            self.assertNotIn(placeholder, serialized)

        sync_policy = application["spec"]["syncPolicy"]
        self.assertEqual({"prune": True, "selfHeal": True}, sync_policy["automated"])
        self.assertIn("ServerSideApply=true", sync_policy["syncOptions"])
        self.assertNotIn("CreateNamespace=true", sync_policy["syncOptions"])
        self.assertEqual("portal", application["spec"]["destination"]["namespace"])

    def test_ci_lints_apps_and_validates_rendered_portal_config(self):
        workflow, = self.load_documents(".github/workflows/validate.yaml")
        lint_steps = workflow["jobs"]["yamllint"]["steps"]
        lint_install = next(
            step["run"] for step in lint_steps if step.get("name") == "Install Kustomize"
        )
        self.assertEqual("go install sigs.k8s.io/kustomize/kustomize/v5@v5.7.1", lint_install)

        lint_command = next(step["run"] for step in lint_steps if step.get("name") == "Lint")
        self.assertIn(" apps ", f" {lint_command} ")
        conform_steps = workflow["jobs"]["kubeconform"]["steps"]
        conform_install = next(
            step["run"] for step in conform_steps if step.get("name") == "Install Kustomize"
        )
        self.assertEqual(lint_install, conform_install)

        render_command = next(
            step["run"] for step in conform_steps if step.get("name") == "Render application configs"
        )
        self.assertIn("kustomize build apps/portal/config", render_command)
        self.assertIn("rendered/portal-config.yaml", render_command)
        for name in ("Validate manifests", "Fail if any resource was skipped"):
            command = next(step["run"] for step in conform_steps if step.get("name") == name)
            self.assertIn(" rendered", command)

    def test_portal_inline_patches_remove_product_owned_resources_without_duplicates(self):
        """Exercise pinned-source patch semantics locally without private Git access."""
        application, = self.load_documents("environments/homelab/apps/homelab-portal.yaml")
        source = next(
            item for item in application["spec"]["sources"]
            if item["repoURL"].endswith("homelab-portal.git")
        )
        self.assertEqual(self.portal_revision, source["targetRevision"])
        annotations = application["metadata"]["annotations"]
        self.assertEqual(self.portal_tag, annotations["homelab.io/image-tag"])
        self.assertEqual(self.portal_digest, annotations["homelab.io/image-digest"])

        fixture = [
            {"apiVersion": "v1", "kind": "Namespace", "metadata": {"name": "portal"}},
            {
                "apiVersion": "secrets.hashicorp.com/v1beta1",
                "kind": "VaultStaticSecret",
                "metadata": {"name": "homelab-portal", "namespace": "portal"},
                "spec": {"mount": "replace-with-kv-mount", "path": "replace-with-portal-secret-path"},
            },
            {
                "apiVersion": "v1",
                "kind": "ConfigMap",
                "metadata": {"name": "retained", "namespace": "portal"},
                "data": {"proof": "kept"},
            },
        ]
        with TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "fixture.yaml").write_text(yaml.safe_dump_all(fixture), encoding="utf-8")
            kustomization = {
                "apiVersion": "kustomize.config.k8s.io/v1beta1",
                "kind": "Kustomization",
                "resources": ["fixture.yaml"],
                "patches": source["kustomize"]["patches"],
            }
            (root / "kustomization.yaml").write_text(yaml.safe_dump(kustomization), encoding="utf-8")
            product = self.render(root)

        product_ids = [
            (item["apiVersion"], item["kind"], item.get("metadata", {}).get("namespace", ""),
             item["metadata"]["name"])
            for item in product
        ]
        self.assertEqual([("v1", "ConfigMap", "portal", "retained")], product_ids)

        site = self.render(REPOSITORY_ROOT / "apps/portal/config")
        site_ids = [
            (item["apiVersion"], item["kind"], item.get("metadata", {}).get("namespace", ""),
             item["metadata"]["name"])
            for item in site
        ]
        combined_ids = product_ids + site_ids
        self.assertEqual(len(combined_ids), len(set(combined_ids)))

    def test_portal_vso_projects_only_required_secret_keys(self):
        resources = self.load_documents("apps/portal/config/vault-secrets.yaml")
        by_kind = {}
        for resource in resources:
            by_kind.setdefault(resource["kind"], []).append(resource)
        vault_auth = by_kind["VaultAuth"][0]
        secrets = {item["metadata"]["name"]: item for item in by_kind["VaultStaticSecret"]}
        self.assertEqual({"homelab-portal", "ghcr-pull"}, set(secrets))
        portal_secret = secrets["homelab-portal"]

        self.assertNotIn("ServiceAccount", by_kind)
        self.assertEqual("vso-portal", vault_auth["spec"]["kubernetes"]["role"])
        self.assertEqual("homelab-portal", vault_auth["spec"]["kubernetes"]["serviceAccount"])
        self.assertEqual("portal", portal_secret["spec"]["path"])
        self.assertEqual("300s", portal_secret["spec"]["refreshAfter"])
        destination = portal_secret["spec"]["destination"]
        self.assertEqual("homelab-portal-secrets", destination["name"])
        transformation = destination["transformation"]
        self.assertIs(True, transformation["excludeRaw"])
        self.assertEqual(
            {"oidc-client-secret", "session-current-key"},
            set(transformation["templates"]),
        )
        self.assertLessEqual(
            set(transformation["templates"]),
            {"oidc-client-secret", "session-current-key", "session-previous-key"},
        )
        self.assertEqual(
            [{"kind": "Deployment", "name": "homelab-portal"}],
            portal_secret["spec"]["rolloutRestartTargets"],
        )
        self.assertIs(True, portal_secret["spec"]["hmacSecretData"])

        ghcr_secret = secrets["ghcr-pull"]
        self.assertEqual("homelab-portal", ghcr_secret["spec"]["vaultAuthRef"])
        self.assertEqual("ghcr", ghcr_secret["spec"]["path"])
        self.assertEqual("300s", ghcr_secret["spec"]["refreshAfter"])
        ghcr_destination = ghcr_secret["spec"]["destination"]
        self.assertEqual("ghcr-pull", ghcr_destination["name"])
        self.assertEqual(
            "kubernetes.io/dockerconfigjson", ghcr_destination["type"]
        )
        self.assertIs(True, ghcr_destination["transformation"]["excludeRaw"])
        self.assertEqual(
            {".dockerconfigjson"},
            set(ghcr_destination["transformation"]["templates"]),
        )


class PortalIngressPublicationTests(unittest.TestCase):
    approved_catalogue = {
        ("vault", "vault"): ("Vault", "groups", "homelab-admins"),
        ("argocd", "argocd"): ("Argo CD", "groups", "homelab-admins"),
        ("keycloak", "keycloak"): ("Keycloak", "groups", "homelab-admins"),
    }

    @staticmethod
    def load_ingresses():
        ingresses = []
        for path in sorted((REPOSITORY_ROOT / "infrastructure/ingress/config").glob("*-ingress.yaml")):
            with path.open(encoding="utf-8") as stream:
                ingresses.extend(
                    document
                    for document in yaml.safe_load_all(stream)
                    if document and document.get("kind") == "Ingress"
                )
        return ingresses

    def test_portal_ingress_exposes_only_the_named_public_port(self):
        ingresses = self.load_ingresses()
        matching = [
            ingress
            for ingress in ingresses
            if ingress.get("metadata", {}).get("namespace") == "portal"
            and ingress.get("metadata", {}).get("name") == "homelab-portal"
        ]
        self.assertEqual(1, len(matching))
        portal = matching[0]
        annotations = portal["metadata"]["annotations"]
        self.assertEqual("ingress", annotations["tailscale.com/proxy-group"])
        self.assertFalse(any(key.startswith("portal.homelab.io/") for key in annotations))
        self.assertEqual("tailscale", portal["spec"]["ingressClassName"])
        self.assertEqual([{"hosts": ["portal"]}], portal["spec"]["tls"])

        rules = portal["spec"]["rules"]
        self.assertEqual(1, len(rules))
        paths = rules[0]["http"]["paths"]
        self.assertEqual(1, len(paths))
        self.assertEqual("/", paths[0]["path"])
        self.assertEqual("Prefix", paths[0]["pathType"])
        self.assertEqual(
            {"name": "homelab-portal", "port": {"name": "public"}},
            paths[0]["backend"]["service"],
        )
        self.assertNotIn("operations", yaml.safe_dump(portal))

    def test_catalogue_contains_only_the_approved_authenticated_targets(self):
        published = {}
        for ingress in self.load_ingresses():
            metadata = ingress.get("metadata", {})
            annotations = metadata.get("annotations", {})
            if annotations.get("portal.homelab.io/enabled") != "true":
                continue
            key = (metadata.get("namespace"), metadata.get("name"))
            published[key] = (
                annotations.get("portal.homelab.io/name"),
                annotations.get("portal.homelab.io/access"),
                annotations.get("portal.homelab.io/groups"),
            )

        self.assertEqual(self.approved_catalogue, published)
        self.assertNotIn("public", {access for _, access, _ in published.values()})

    def test_published_ingresses_follow_the_portal_metadata_schema(self):
        known_icons = {
            "argocd", "generic", "grafana", "keycloak", "kubernetes",
            "prometheus", "tailscale", "vault",
        }
        allowed_access = {"public", "authenticated", "groups", "admin"}
        published_count = 0

        for ingress in self.load_ingresses():
            annotations = ingress.get("metadata", {}).get("annotations", {})
            if annotations.get("portal.homelab.io/enabled") != "true":
                continue
            published_count += 1
            name = annotations.get("portal.homelab.io/name", "").strip()
            description = annotations.get("portal.homelab.io/description", "").strip()
            category = annotations.get("portal.homelab.io/category", "").strip()
            icon = annotations.get("portal.homelab.io/icon")
            access = annotations.get("portal.homelab.io/access")
            order = annotations.get("portal.homelab.io/order", "")
            groups = annotations.get("portal.homelab.io/groups")

            self.assertTrue(0 < len(name) <= 80)
            self.assertLessEqual(len(description), 240)
            self.assertTrue(0 < len(category) <= 40)
            self.assertIn(icon, known_icons)
            self.assertIn(access, allowed_access)
            self.assertTrue(order.isdigit() and 0 <= int(order) <= 9999)
            if access == "groups":
                parsed = [group.strip() for group in (groups or "").split(",")]
                self.assertTrue(all(parsed))
                self.assertEqual(len(parsed), len(set(parsed)))
            else:
                self.assertIsNone(groups)

        self.assertEqual(len(self.approved_catalogue), published_count)

if __name__ == "__main__":
    unittest.main()
