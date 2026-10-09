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
                # tailscale namespace in the same peer, and retargets the
                # metrics peer at the Prometheus agent -- guarded by a `test`
                # so a changed product policy fails instead of mis-patching.
                scrape_label = "/spec/ingress/1/from/0/podSelector/matchLabels/app.kubernetes.io~1name"
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
                    }, {
                        "op": "test", "path": scrape_label, "value": "prometheus",
                    }, {
                        "op": "replace", "path": scrape_label, "value": "prometheus-agent",
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
        ("penpot", "penpot"): ("Penpot", "authenticated", None),
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


class PenpotRegistrationTests(unittest.TestCase):
    chart_version = "1.11.3"
    app_version = "2.18.3"

    # Every namespace bootstrap/namespaces/namespaces.yaml declares, in file
    # order. Asserted as a whole list because the `namespaces` Application
    # reconciles this file with prune: true: a document dropped from it
    # DELETES that namespace and cascades to everything inside it.
    expected_namespaces = [
        "cert-manager", "vault", "tailscale", "vault-secrets-operator-system",
        "databases", "apps", "monitoring", "logging", "keycloak", "portal",
        "tle-dev", "omniroute", "egress", "agents", "penpot", "routeplane",
    ]

    # Each digest bound to the image it belongs to. Bare 64-hex substrings
    # would pass on a runbook with any two rows transposed.
    image_digests = (
        (
            "penpotapp/frontend:2.18.3",
            "bb8abe27d53de84c95597f2c02c0e702b2779971fb0703e543f9ecf183e999f6",
        ),
        (
            "penpotapp/backend:2.18.3",
            "2df1b3440d2a82cc3571db211b4ffdfa2b89ccc910759e8d5e9387fb62971b5c",
        ),
        (
            "penpotapp/exporter:2.18.3",
            "418232d6ca3120b1c2bfde298a56a05a1f41f567cd8494deac3fe7fbc186cfbd",
        ),
        (
            "penpotapp/mcp:2.18.3",
            "5e811e6eeb179d80d8781fb0ffd2991560785d150b3676f1ac5e28d63ba9f7c2",
        ),
    )

    @staticmethod
    def load(path):
        with (REPOSITORY_ROOT / path).open(encoding="utf-8") as stream:
            return [document for document in yaml.safe_load_all(stream) if document]

    def test_namespace_inventory_is_complete_and_ordered(self):
        # A per-namespace filter cannot see a document that was merged into
        # its neighbour and lost. `agents` is the canary: it is what a
        # missing `---` separator between two Namespace documents swallows,
        # silently, with no error from the parser. Only an assertion over
        # every document in order catches that, and reordering too.
        namespaces = self.load("bootstrap/namespaces/namespaces.yaml")
        self.assertEqual(
            ["Namespace"] * len(self.expected_namespaces),
            [document["kind"] for document in namespaces],
        )
        self.assertEqual(
            self.expected_namespaces,
            [document["metadata"]["name"] for document in namespaces],
        )

    def test_penpot_namespace_is_restricted_to_kubernetes_136(self):
        namespaces = self.load("bootstrap/namespaces/namespaces.yaml")
        matches = [
            document for document in namespaces
            if document.get("kind") == "Namespace"
            and document.get("metadata", {}).get("name") == "penpot"
        ]
        self.assertEqual(1, len(matches))
        labels = matches[0]["metadata"]["labels"]
        for mode in ("enforce", "audit", "warn"):
            self.assertEqual("restricted", labels[f"pod-security.kubernetes.io/{mode}"])
            self.assertEqual("v1.36", labels[f"pod-security.kubernetes.io/{mode}-version"])

    def test_penpot_application_pins_chart_and_site_manifests(self):
        application, = self.load("environments/homelab/apps/penpot.yaml")
        metadata = application["metadata"]
        annotations = metadata["annotations"]
        # Later tasks key off these three, so they are asserted in their own
        # right and not merely implied by spec.destination.namespace below.
        # A rename to `penpot-app` changes every Argo lookup by name.
        self.assertEqual("penpot", metadata["name"])
        self.assertEqual("argocd", metadata["namespace"])
        self.assertEqual("default", application["spec"]["project"])
        self.assertEqual("25", annotations["argocd.argoproj.io/sync-wave"])
        self.assertEqual("v1", annotations["homelab.io/onboarding-contract"])
        self.assertEqual("personal-applications", annotations["homelab.io/owner"])
        self.assertEqual("durable", annotations["homelab.io/state"])
        self.assertEqual(
            "docs/runbooks/penpot-recovery.md",
            annotations["backup.homelab.io/restore-runbook"],
        )

        sources = application["spec"]["sources"]
        self.assertEqual(3, len(sources))
        # repoURL is pinned on every source, not just checked on the chart.
        # Two of the three sources render objects into a namespace that has
        # a Vault connection, so a source silently repointed at a third-party
        # repository would be applied with this repository's privileges.
        chart = next(source for source in sources if source.get("chart"))
        self.assertEqual("http://helm.penpot.app", chart["repoURL"])
        self.assertEqual("penpot", chart["chart"])
        self.assertEqual(self.chart_version, chart["targetRevision"])
        self.assertNotIn("homelab.git", chart["repoURL"])
        self.assertEqual(
            ["$values/apps/penpot/values.yaml"], chart["helm"]["valueFiles"]
        )
        values_source = next(source for source in sources if source.get("ref"))
        self.assertEqual("git@github.com:Slqzeer/homelab.git", values_source["repoURL"])
        self.assertEqual("values", values_source["ref"])
        manifest_source = next(
            source for source in sources if source.get("path")
        )
        self.assertEqual("git@github.com:Slqzeer/homelab.git", manifest_source["repoURL"])
        self.assertEqual("apps/penpot/config", manifest_source["path"])
        self.assertEqual("main", manifest_source["targetRevision"])

        self.assertEqual("penpot", application["spec"]["destination"]["namespace"])
        sync_policy = application["spec"]["syncPolicy"]
        self.assertEqual({"prune": True, "selfHeal": True}, sync_policy["automated"])
        self.assertIn("ServerSideApply=true", sync_policy["syncOptions"])
        self.assertNotIn("CreateNamespace=true", sync_policy["syncOptions"])

    def test_penpot_runbook_is_checked_in_and_records_the_release(self):
        runbook = REPOSITORY_ROOT / "docs/runbooks/penpot-recovery.md"
        self.assertTrue(runbook.is_file())
        tracked = subprocess.run(
            ["git", "-C", str(REPOSITORY_ROOT), "ls-files", "--error-unmatch", "--",
             "docs/runbooks/penpot-recovery.md"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False,
        )
        self.assertEqual(0, tracked.returncode)
        text = runbook.read_text(encoding="utf-8")
        # Each digest is asserted against the image it is recorded beside, in
        # the runbook's own pipe row, digest prefix included. A bare 64-hex
        # assertion passes on a runbook with the frontend and backend rows
        # transposed -- which is precisely the edit an upgrade makes, and the
        # one that would leave a wrong digest trusted during a rollback.
        # Whitespace is collapsed first so a row wrapped across lines matches.
        normalized = " ".join(text.split())
        for image, digest in self.image_digests:
            with self.subTest(image=image):
                self.assertIn(f"`{image}` | `sha256:{digest}`", normalized)
        self.assertIn(self.chart_version, text)
        self.assertIn(self.app_version, text)
        # The eviction deviation is a real operational hazard, so it has to
        # survive edits to this file rather than living only in a commit
        # message. Asserted here because Task 1 is what creates the runbook.
        self.assertIn("allkeys-lru", text)
        self.assertIn("volatile-lfu", text)
        self.assertIn("dedicated", text)

    def test_penpot_values_wire_shared_datastores_and_oidc(self):
        values, = self.load("apps/penpot/values.yaml")
        config = values["config"]
        self.assertEqual("https://penpot.taildf6cd4.ts.net", config["publicUri"])

        flags = config["flags"].split()
        self.assertIn("enable-login-with-oidc", flags)
        self.assertIn("enable-mcp", flags)
        # Keycloak is the only way in. Penpot 2.18.3's built-in defaults
        # enable registration and password login, so leaving them out is not
        # enough: each needs an explicit disable-.
        self.assertIn("disable-registration", flags)
        self.assertIn("disable-login-with-password", flags)
        self.assertNotIn("enable-registration", flags)
        self.assertNotIn("enable-login-with-password", flags)
        # `login` is the legacy alias the backend also accepts for password
        # login (rpc/commands/auth.clj).
        self.assertNotIn("enable-login", flags)
        # With registration off, only this lets a first Keycloak login create
        # its Penpot account (auth/oidc.clj).
        self.assertIn("enable-oidc-registration", flags)
        # OIDC-provisioned profiles are created inactive unless Keycloak
        # asserts email_verified (auth.clj), and there is no SMTP to deliver
        # verification mail. Password sign-up/login are off, so verification
        # guards nothing; without this flag users loop at login.
        self.assertIn("disable-email-verification", flags)
        # The chart default is on; nothing leaves this installation unasked.
        self.assertIs(False, config["telemetryEnabled"])
        # The admin console is a fifth deployment this installation does not need.
        self.assertNotIn("enable-admin-console", flags)
        # Every flag carries the enable-/disable- prefix the chart requires;
        # a bare token is silently ignored by Penpot.
        for flag in flags:
            self.assertTrue(flag.startswith(("enable-", "disable-")), flag)

        self.assertEqual("penpot-secrets", config["existingSecret"])
        self.assertEqual("api-secret-key", config["secretKeys"]["apiSecretKey"])

        postgres = config["postgresql"]
        self.assertEqual("postgres.databases.svc.cluster.local", postgres["host"])
        self.assertEqual(5432, postgres["port"])
        self.assertEqual("penpot", postgres["database"])
        self.assertEqual("penpot-secrets", postgres["existingSecret"])
        self.assertEqual("postgres-username", postgres["secretKeys"]["usernameKey"])
        self.assertEqual("postgres-password", postgres["secretKeys"]["passwordKey"])
        # A URI key would carry the password inside the URI; the chart builds
        # a credential-free URI when only the username/password keys are set.
        self.assertEqual("", postgres["secretKeys"]["postgresqlUriKey"])
        self.assertNotIn("password", postgres)

        redis = config["redis"]
        self.assertEqual("redis.databases.svc.cluster.local", redis["host"])
        self.assertEqual("6379", redis["port"])
        # Index 3, not 0: this Redis is shared and index 0 may hold keys.
        self.assertEqual("3", redis["database"])
        # A separate Secret: a second VaultStaticSecret renders the URI from
        # the shared Redis password, and two cannot own one destination.
        self.assertEqual("penpot-redis", redis["existingSecret"])
        self.assertEqual("redis-uri", redis["secretKeys"]["redisUriKey"])

        assets = values["persistence"]["assets"]
        self.assertIs(True, assets["enabled"])
        self.assertEqual("local-path", assets["storageClass"])

        # OIDC lives in backend.extraEnvs, not config.extraEnvs: the chart
        # injects config.extraEnvs into all five components, so putting the
        # client secret there would hand it to the MCP server too.
        env = {entry["name"]: entry for entry in values["backend"]["extraEnvs"]}
        self.assertEqual("penpot", env["PENPOT_OIDC_CLIENT_ID"]["value"])
        self.assertEqual(
            "penpot-secrets",
            env["PENPOT_OIDC_CLIENT_SECRET"]["valueFrom"]["secretKeyRef"]["name"],
        )
        auth = env["PENPOT_OIDC_AUTH_URI"]["value"]
        self.assertIn("keycloak.taildf6cd4.ts.net", auth)
        for name in ("PENPOT_OIDC_TOKEN_URI", "PENPOT_OIDC_USER_URI",
                     "PENPOT_OIDC_JWKS_URI"):
            self.assertIn(
                "keycloak.keycloak.svc.cluster.local", env[name]["value"], name,
            )
        # This Keycloak serves realms at /realms/<realm>; the legacy /auth
        # prefix answers 404, so the login redirect would dead-end.
        for name in ("PENPOT_OIDC_BASE_URI", "PENPOT_OIDC_AUTH_URI",
                     "PENPOT_OIDC_TOKEN_URI", "PENPOT_OIDC_USER_URI",
                     "PENPOT_OIDC_JWKS_URI"):
            self.assertIn("/realms/homelab/", env[name]["value"], name)
            self.assertNotIn("/auth/realms/", env[name]["value"], name)
        # The shared PostgreSQL allows 50 connections in all. Penpot's pool
        # defaults to 60 and fills eagerly, which starved every other client.
        self.assertLessEqual(int(env["PENPOT_DATABASE_MAX_POOL_SIZE"]["value"]), 10)
        self.assertLessEqual(
            int(env["PENPOT_DATABASE_MIN_POOL_SIZE"]["value"]),
            int(env["PENPOT_DATABASE_MAX_POOL_SIZE"]["value"]),
        )
        # A rolling update would run two pools at once against that limit,
        # and the assets volume is ReadWriteOnce anyway.
        self.assertEqual({"type": "Recreate"}, values["backend"]["updateStrategy"])
        ssrf = env["PENPOT_SSRF_ALLOWED_HOSTS"]["value"].split()
        self.assertIn("keycloak.keycloak.svc.cluster.local", ssrf)
        self.assertIn("keycloak.taildf6cd4.ts.net", ssrf)

    def test_penpot_components_have_measured_resources(self):
        values, = self.load("apps/penpot/values.yaml")
        for component in ("backend", "frontend", "exporter", "mcp"):
            resources = values[component]["resources"]
            for kind in ("requests", "limits"):
                self.assertEqual({"cpu", "memory"}, set(resources[kind]),
                                 f"{component}.{kind}")
        # Without a cap the JVM sizes its heap from the node, not the limit.
        env = {entry["name"]: entry for entry in values["backend"]["extraEnvs"]}
        self.assertIn("MaxRAMPercentage", env["JVM_OPTS"]["value"])

    def test_penpot_pods_satisfy_restricted_pod_security(self):
        # Checked on values, not on a render: CI has no helm. Chart 1.11.3
        # copies each component's two blocks verbatim into its Deployment
        # (and the frontend's into the helm-test pod). Its own defaults fail
        # `restricted` -- no seccompProfile, and `drop: [all]` in lower case.
        values, = self.load("apps/penpot/values.yaml")
        for component in ("backend", "frontend", "exporter", "mcp"):
            pod = values[component]["podSecurityContext"]
            self.assertEqual({"type": "RuntimeDefault"}, pod["seccompProfile"], component)
            self.assertEqual(1001, pod["fsGroup"], component)
            container = values[component]["containerSecurityContext"]
            self.assertEqual(["ALL"], container["capabilities"]["drop"], component)
            self.assertIs(True, container["runAsNonRoot"], component)
            self.assertIs(False, container["allowPrivilegeEscalation"], component)
            self.assertEqual(1001, container["runAsUser"], component)

    def test_penpot_pods_do_not_wear_the_vso_service_account(self):
        # Checked on values plus the site manifests, not on a render: CI has
        # no helm. The chart creates a ServiceAccount named
        # serviceAccount.name and runs every pod as it; `penpot` belongs to
        # VSO (Vault role vso-penpot) and is defined in apps/penpot/config.
        values, = self.load("apps/penpot/values.yaml")
        account = values["serviceAccount"]
        self.assertEqual("penpot-workload", account["name"])
        site_accounts = {
            document["metadata"]["name"]
            for path in sorted((REPOSITORY_ROOT / "apps/penpot/config").glob("*.yaml"))
            if path.name != "kustomization.yaml"
            for document in self.load(path.relative_to(REPOSITORY_ROOT).as_posix())
            if document.get("kind") == "ServiceAccount"
            and document["metadata"].get("namespace") == "penpot"
        }
        self.assertIn("penpot", site_accounts)
        self.assertNotIn(account["name"], site_accounts)

    def test_penpot_values_contain_no_credential(self):
        text = (REPOSITORY_ROOT / "apps/penpot/values.yaml").read_text(encoding="utf-8")
        for forbidden in ("apiSecretKey: \"", "password: penpot", "mcp-key"):
            self.assertNotIn(forbidden, text)

    def test_penpot_vso_projects_only_required_keys(self):
        resources = self.load("apps/penpot/config/vault-secrets.yaml")
        by_kind = {}
        for resource in resources:
            by_kind.setdefault(resource["kind"], []).append(resource)

        self.assertEqual(["penpot"], [item["metadata"]["name"]
                                      for item in by_kind["ServiceAccount"]])
        auth, = by_kind["VaultAuth"]
        self.assertEqual("vso-penpot", auth["spec"]["kubernetes"]["role"])
        self.assertEqual("penpot", auth["spec"]["kubernetes"]["serviceAccount"])
        self.assertEqual(["vault"], auth["spec"]["kubernetes"]["audiences"])

        secrets = {item["metadata"]["name"]: item for item in by_kind["VaultStaticSecret"]}
        self.assertEqual({"penpot", "penpot-redis", "keycloak-penpot-client"}, set(secrets))

        penpot = secrets["penpot"]
        self.assertEqual("homelab", penpot["spec"]["mount"])
        self.assertEqual("penpot", penpot["spec"]["path"])
        self.assertEqual("300s", penpot["spec"]["refreshAfter"])
        self.assertIs(True, penpot["spec"]["hmacSecretData"])
        transformation = penpot["spec"]["destination"]["transformation"]
        self.assertEqual("penpot-secrets", penpot["spec"]["destination"]["name"])
        self.assertIs(True, transformation["excludeRaw"])
        self.assertEqual([".*"], transformation["excludes"])
        # redis-uri lives in its own Secret (penpot-redis), not here.
        self.assertEqual(
            {"postgres-username", "postgres-password",
             "api-secret-key", "oidc-client-secret"},
            set(transformation["templates"]),
        )
        # The MCP key is issued by Penpot after first login; projecting it
        # before it exists would leave the pod waiting on a value nobody has.
        self.assertNotIn("mcp-key", transformation["templates"])
        # Exactly the Deployments whose chart 1.11.3 render references
        # penpot-secrets (backend and exporter; frontend and mcp do not).
        self.assertEqual(
            [{"kind": "Deployment", "name": "penpot-backend"},
             {"kind": "Deployment", "name": "penpot-exporter"}],
            penpot["spec"]["rolloutRestartTargets"],
        )

        redis = secrets["penpot-redis"]
        self.assertEqual("penpot", redis["metadata"]["namespace"])
        self.assertEqual("penpot", redis["spec"]["vaultAuthRef"])
        self.assertEqual("homelab", redis["spec"]["mount"])
        self.assertEqual("kv-v2", redis["spec"]["type"])
        self.assertEqual("redis", redis["spec"]["path"])
        self.assertEqual("300s", redis["spec"]["refreshAfter"])
        self.assertIs(True, redis["spec"]["hmacSecretData"])
        destination = redis["spec"]["destination"]
        self.assertEqual("penpot-redis", destination["name"])
        self.assertIs(True, destination["create"])
        self.assertIs(True, destination["transformation"]["excludeRaw"])
        self.assertEqual([".*"], destination["transformation"]["excludes"])
        templates = destination["transformation"]["templates"]
        self.assertEqual({"redis-uri"}, set(templates))
        self.assertEqual(
            'redis://:{{ get .Secrets "password" }}'
            "@redis.databases.svc.cluster.local:6379/3",
            templates["redis-uri"]["text"],
        )
        self.assertEqual(
            [{"kind": "Deployment", "name": "penpot-backend"},
             {"kind": "Deployment", "name": "penpot-exporter"},
             {"kind": "Deployment", "name": "penpot-mcp"}],
            redis["spec"]["rolloutRestartTargets"],
        )

        client = secrets["keycloak-penpot-client"]
        self.assertEqual("keycloak", client["metadata"]["namespace"])
        self.assertEqual("penpot-client", client["spec"]["path"])
        self.assertEqual("keycloak-penpot-client",
                         client["spec"]["destination"]["name"])
        client_transformation = client["spec"]["destination"]["transformation"]
        self.assertIs(True, client_transformation["excludeRaw"])
        self.assertEqual({"clientSecret"}, set(client_transformation["templates"]))
        self.assertEqual('{{ get .Secrets "clientSecret" }}',
                         client_transformation["templates"]["clientSecret"]["text"])

    def test_penpot_database_job_creates_role_and_database_idempotently(self):
        job, = [item for item in self.load("apps/penpot/config/postgres-job.yaml")
                if item["kind"] == "Job"]
        self.assertEqual("databases", job["metadata"]["namespace"])
        self.assertEqual("Sync", job["metadata"]["annotations"]["argocd.argoproj.io/hook"])
        pod = job["spec"]["template"]["spec"]
        self.assertEqual("penpot-db", pod["serviceAccountName"])
        self.assertEqual({"job": "postgres-client"},
                         job["spec"]["template"]["metadata"]["labels"])
        container, = pod["containers"]
        env = {entry["name"]: entry for entry in container["env"]}
        self.assertEqual("postgres-credentials",
                         env["PGPASSWORD"]["valueFrom"]["secretKeyRef"]["name"])
        self.assertEqual("penpot-db",
                         env["PENPOT_PASSWORD"]["valueFrom"]["secretKeyRef"]["name"])
        self.assertEqual("postgres.databases.svc.cluster.local", env["PGHOST"]["value"])
        # \getenv, not string interpolation into SQL text.
        script = " ".join(container["command"])
        self.assertIn("\\getenv pw PENPOT_PASSWORD", script)
        self.assertIn("CREATE ROLE penpot LOGIN", script)
        self.assertIn("CREATE DATABASE penpot OWNER penpot", script)
        self.assertNotIn("password: penpot", script)

    @staticmethod
    def pod_set(selector):
        # The Penpot components a podSelector picks out: "*" for every pod,
        # otherwise the `app` values it names via matchLabels or `app In`.
        if selector == {}:
            return "*"
        labels = selector.get("matchLabels", {})
        if labels:
            return frozenset({labels["app"]})
        expression, = selector["matchExpressions"]
        assert (expression["key"], expression["operator"]) == ("app", "In"), expression
        return frozenset(expression["values"])

    @staticmethod
    def peer_key(peer):
        # (namespace or None for "this namespace", labels as sorted pairs).
        # A namespace-wide peer has labels None, so it can never compare
        # equal to a pod-scoped one.
        namespace = None
        if "namespaceSelector" in peer:
            namespace = peer["namespaceSelector"]["matchLabels"][
                "kubernetes.io/metadata.name"]
        labels = peer.get("podSelector", {}).get("matchLabels")
        return namespace, tuple(sorted(labels.items())) if labels is not None else None

    def policy_flows(self, policy):
        # Every (direction, selected pods, peer, port) a policy grants.
        flows = set()
        selected = self.pod_set(policy["spec"]["podSelector"])
        for direction, peers_key in (("ingress", "from"), ("egress", "to")):
            for rule in policy["spec"].get(direction, []):
                # No rule may omit its peers or ports: either means "any".
                self.assertTrue(rule.get(peers_key), policy["metadata"]["name"])
                self.assertTrue(rule.get("ports"), policy["metadata"]["name"])
                for peer in rule[peers_key]:
                    for port in rule["ports"]:
                        self.assertNotIn("endPort", port)
                        flows.add((direction, selected, self.peer_key(peer),
                                   port.get("protocol", "TCP"), port["port"]))
        return flows

    def test_penpot_network_policies_fence_the_namespace(self):
        policies = self.load("apps/penpot/config/networkpolicy.yaml")
        by_name = {item["metadata"]["name"]: item for item in policies}
        self.assertEqual(len(policies), len(by_name), "duplicate policy name")
        for item in policies:
            self.assertEqual("penpot", item["metadata"]["namespace"])
        for direction in ("ingress", "egress"):
            deny = by_name.pop(f"default-deny-{direction}")
            self.assertEqual({}, deny["spec"]["podSelector"])
            self.assertEqual([direction.capitalize()], deny["spec"]["policyTypes"])
            self.assertNotIn(direction, deny["spec"])

        def app(name):
            return frozenset({f"penpot-{name}"})

        def local(name):
            return None, (("app", f"penpot-{name}"),)

        tailnet = ("tailscale", (
            ("tailscale.com/parent-resource", "ingress"),
            ("tailscale.com/parent-resource-type", "proxygroup"),
        ))
        kube_dns = ("kube-system", None)
        postgres = ("databases", (("app", "postgres"),))
        redis = ("databases", (("app", "redis"),))
        keycloak = ("keycloak", (("app", "keycloak"),))
        egress_proxy = ("egress", (("app", "egress-proxy"),))

        expected = {
            "penpot-dns": {
                ("egress", "*", kube_dns, "UDP", 53),
                ("egress", "*", kube_dns, "TCP", 53),
            },
            # Only the frontend is reachable from the tailnet, on 8080 only.
            "penpot-tailnet": {("ingress", app("frontend"), tailnet, "TCP", 8080)},
            # In-namespace flows: default-deny-egress covers every pod, so
            # each needs an ingress grant on the receiver AND an egress
            # grant on the sender.
            "penpot-frontend-to-backend": {
                ("ingress", app("backend"), local("frontend"), "TCP", 6060)},
            "penpot-frontend-to-mcp": {
                ("ingress", app("mcp"), local("frontend"), "TCP", 4401),
                ("ingress", app("mcp"), local("frontend"), "TCP", 4402),
            },
            "penpot-frontend-to-exporter": {
                ("ingress", app("exporter"), local("frontend"), "TCP", 6061)},
            "penpot-exporter-to-frontend": {
                ("ingress", app("frontend"), local("exporter"), "TCP", 8080)},
            "penpot-frontend-egress": {
                ("egress", app("frontend"), local("backend"), "TCP", 6060),
                ("egress", app("frontend"), local("exporter"), "TCP", 6061),
                ("egress", app("frontend"), local("mcp"), "TCP", 4401),
                ("egress", app("frontend"), local("mcp"), "TCP", 4402),
            },
            "penpot-exporter-egress": {
                ("egress", app("exporter"), local("frontend"), "TCP", 8080)},
            # Only the backend speaks to PostgreSQL.
            "penpot-postgres": {("egress", app("backend"), postgres, "TCP", 5432)},
            # Backend, exporter and MCP consume the Redis URI; the frontend
            # does not.
            "penpot-redis": {
                ("egress", app("backend") | app("exporter") | app("mcp"),
                 redis, "TCP", 6379)},
            "penpot-keycloak": {("egress", app("backend"), keycloak, "TCP", 8080)},
            "penpot-egress-proxy": {
                ("egress", app("backend"), egress_proxy, "TCP", 3128)},
        }
        # Exact equality: a policy added, dropped or widened fails here. In
        # particular there is no penpot-metrics: nothing in this namespace
        # serves /metrics (the exporter's 6061 is a rendering endpoint).
        actual = {name: self.policy_flows(policy) for name, policy in by_name.items()}
        self.assertEqual(expected, actual)
        self.assertNotIn("penpot-metrics", actual)

        flows = set().union(*actual.values())
        # Both sides of every in-namespace flow are granted.
        for direction, selected, peer, protocol, port in flows:
            if peer[0] is not None or selected == "*":
                continue
            here, = selected
            there = dict(peer[1])["app"]
            opposite = "ingress" if direction == "egress" else "egress"
            mirror = (opposite, frozenset({there}), local(here[len("penpot-"):]),
                      protocol, port)
            self.assertIn(mirror, flows, (direction, here, there, port))

        # The MCP invariant: 4401/4402 are admitted from the frontend and
        # from nothing else, and the tailnet reaches only frontend:8080.
        for direction, selected, peer, protocol, port in flows:
            if direction == "ingress" and port in (4401, 4402):
                self.assertEqual(local("frontend"), peer)
            if peer[0] == "tailscale":
                self.assertEqual(("ingress", app("frontend"), 8080),
                                 (direction, selected, port))

        text = yaml.safe_dump_all(policies)
        self.assertNotIn("0.0.0.0/0", text)
        self.assertNotIn("endPort: 65535", text)

    def test_databases_admit_exactly_the_penpot_components_that_need_them(self):
        postgres_policies = {
            item["metadata"]["name"]: item
            for item in self.load("platform/databases/postgres/config/networkpolicy.yaml")}
        redis_policies = {
            item["metadata"]["name"]: item
            for item in self.load("platform/databases/redis/config/networkpolicy.yaml")}
        clients = {
            "postgres-clients": postgres_policies["postgres-clients"],
            "redis-clients": redis_policies["redis-clients"],
        }
        penpot_flows = {
            name: {flow for flow in self.policy_flows(policy) if flow[2][0] == "penpot"}
            for name, policy in clients.items()
        }
        self.assertEqual({
            # Only the backend uses PostgreSQL.
            "postgres-clients": {
                ("ingress", frozenset({"postgres"}),
                 ("penpot", (("app", "penpot-backend"),)), "TCP", 5432)},
            # Backend, exporter and MCP all read the Redis URI.
            "redis-clients": {
                ("ingress", frozenset({"redis"}),
                 ("penpot", (("app", f"penpot-{component}"),)), "TCP", 6379)
                for component in ("backend", "exporter", "mcp")},
        }, penpot_flows)
        # No namespace-wide penpot peer: that would admit the MCP server and
        # anything else that ever lands in the namespace.
        for name, policy in clients.items():
            for rule in policy["spec"]["ingress"]:
                for peer in rule["from"]:
                    if self.peer_key(peer)[0] == "penpot":
                        self.assertIn("podSelector", peer, name)

    def test_penpot_config_renders_and_is_wired_into_ci(self):
        workflow, = self.load(".github/workflows/validate.yaml")
        conform_steps = workflow["jobs"]["kubeconform"]["steps"]
        render_command = next(
            step["run"] for step in conform_steps
            if step.get("name") == "Render application configs"
        )
        self.assertIn("kustomize build apps/penpot/config", render_command)
        self.assertIn("rendered/penpot-config.yaml", render_command)

        kustomization, = self.load("apps/penpot/config/kustomization.yaml")
        # No namespace: field on purpose -- this directory holds objects in
        # penpot, databases and keycloak, and kustomize rewrites even an
        # explicit namespace.
        self.assertNotIn("namespace", kustomization)
        self.assertEqual(
            ["vault-secrets.yaml", "networkpolicy.yaml", "postgres-job.yaml"],
            kustomization["resources"],
        )

    def test_penpot_ingress_routes_only_the_frontend(self):
        ingress, = [item for item in self.load(
            "infrastructure/ingress/config/penpot-ingress.yaml")
            if item["kind"] == "Ingress"]
        metadata = ingress["metadata"]
        annotations = metadata["annotations"]
        self.assertEqual("tailscale", ingress["spec"]["ingressClassName"])
        self.assertEqual("ingress", annotations["tailscale.com/proxy-group"])
        self.assertEqual([{"hosts": ["penpot"]}], ingress["spec"]["tls"])
        # Published only after OIDC login was verified live; the catalogue
        # entry itself is checked by PortalIngressPublicationTests.
        self.assertEqual("authenticated", annotations["portal.homelab.io/access"])

        # The chart names the frontend Service `penpot`, not `penpot-frontend`
        # (verified by helm template; the selector is penpot-frontend).
        backend = ingress["spec"]["defaultBackend"]["service"]
        self.assertEqual("penpot", backend["name"])
        self.assertEqual({"name": "http"}, backend["port"])

        # The backend, exporter and MCP ports must never appear here.
        text = yaml.safe_dump(ingress)
        for forbidden in ("6060", "6061", "4401", "4402", "admin-console"):
            self.assertNotIn(forbidden, text)


if __name__ == "__main__":
    unittest.main()
