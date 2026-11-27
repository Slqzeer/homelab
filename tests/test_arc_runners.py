"""Static checks for the ARC runner isolation model (issue #113)."""

from pathlib import Path
import re
import unittest

import yaml

ROOT = Path(__file__).resolve().parents[1]
SETS = {"ci": "arc-runners", "publish": "arc-publish"}


def load(path):
    with (ROOT / path).open(encoding="utf-8") as stream:
        return [d for d in yaml.safe_load_all(stream) if d]


class ArcRunnerTests(unittest.TestCase):
    def test_runner_image_pinned_by_digest(self):
        for name in SETS:
            values = load(f"platform/arc/{name}/values.yaml")[0]
            image = values["template"]["spec"]["containers"][0]["image"]
            self.assertRegex(image, r":\d+\.\d+\.\d+@sha256:[0-9a-f]{64}$", name)

    def test_runners_pinned_to_dedicated_node_and_no_docker(self):
        for name in SETS:
            values = load(f"platform/arc/{name}/values.yaml")[0]
            spec = values["template"]["spec"]
            self.assertEqual(spec["nodeSelector"], {"homelab.io/runner": "true"})
            self.assertNotIn("containerMode", values)
            self.assertNotIn("privileged", yaml.safe_dump(values))
            self.assertNotIn("docker.sock", yaml.safe_dump(values))
            for volume in spec["volumes"]:
                self.assertNotIn("hostPath", volume)

    def test_runner_role_cannot_read_secrets(self):
        for name in SETS:
            for doc in load(f"platform/arc/{name}/config/rbac.yaml"):
                if doc["kind"] != "Role":
                    continue
                for rule in doc["rules"]:
                    if "secrets" in rule["resources"]:
                        self.assertFalse({"get", "list", "watch", "*"} & set(rule["verbs"]))

    def test_job_pods_have_no_token_and_default_sa_has_no_binding(self):
        for name, ns in SETS.items():
            hook = load(f"platform/arc/{name}/config/hook-template.yaml")[0]
            template = yaml.safe_load(hook["data"]["content"])
            self.assertIs(template["spec"]["automountServiceAccountToken"], False)
            self.assertEqual(template["spec"]["nodeSelector"], {"homelab.io/runner": "true"})
            for doc in load(f"platform/arc/{name}/config/rbac.yaml"):
                if doc["kind"] == "RoleBinding":
                    self.assertNotIn(
                        "default", [s["name"] for s in doc["subjects"]], ns)

    def test_network_policy_is_default_deny_and_api_only_for_runner(self):
        for name, ns in SETS.items():
            docs = load(f"platform/arc/{name}/config/networkpolicy.yaml")
            deny = [d for d in docs if d["metadata"]["name"] == "default-deny"][0]
            self.assertEqual(deny["spec"]["podSelector"], {})
            self.assertEqual(set(deny["spec"]["policyTypes"]), {"Ingress", "Egress"})
            self.assertFalse(deny["spec"].get("egress") or deny["spec"].get("ingress"))
            for doc in docs:
                self.assertEqual(doc["metadata"]["namespace"], ns)
                for rule in doc["spec"].get("egress", []):
                    for peer in rule["to"]:
                        self.assertNotEqual(peer.get("ipBlock", {}).get("cidr"), "0.0.0.0/0")
            api = [d for d in docs if d["metadata"]["name"] == "runner-kubernetes-api"][0]
            self.assertEqual(
                api["spec"]["podSelector"]["matchLabels"], {"homelab.io/arc-role": "runner"})

    def test_namespaces_exist_without_privileged_pod_security(self):
        names = {d["metadata"]["name"]: d for d in load("bootstrap/namespaces/namespaces.yaml")}
        for ns in ("arc-systems", *SETS.values()):
            labels = names[ns]["metadata"]["labels"]
            self.assertIn(labels["pod-security.kubernetes.io/enforce"], {"baseline", "restricted"})

    def test_github_app_secret_comes_from_vault_and_matches_script(self):
        script = (ROOT / "platform/vault/configure-vault.sh").read_text()
        self.assertIn("bound_service_account_names=arc-github-app", script)
        for name, ns in SETS.items():
            docs = {d["kind"]: d for d in load(f"platform/arc/{name}/config/vault-secrets.yaml")}
            self.assertEqual(docs["VaultStaticSecret"]["spec"]["path"], "arc-github-app")
            self.assertEqual(docs["VaultAuth"]["spec"]["kubernetes"]["role"], "vso-arc")
            self.assertIn(ns, re.search(r"role/vso-arc[^\n]*\\\n(?:[^\n]*\\\n)*?\s*bound_service_account_namespaces=(\S+)", script).group(1))
            values = load(f"platform/arc/{name}/values.yaml")[0]
            self.assertEqual(values["githubConfigSecret"], "arc-github-app")
            self.assertNotIn("github_token", values)

    def test_proxy_admits_arc_namespaces(self):
        docs = load("platform/egress-proxy/config/networkpolicy.yaml")
        clients = [d for d in docs if d["metadata"]["name"] == "egress-proxy-clients"][0]
        admitted = {
            p["namespaceSelector"]["matchLabels"]["kubernetes.io/metadata.name"]
            for p in clients["spec"]["ingress"][0]["from"]
        }
        self.assertEqual(admitted, {"arc-systems", *SETS.values()})

    def test_applications_exist_in_wave_order(self):
        waves = {}
        for path in (ROOT / "environments/homelab/apps").glob("arc-*.yaml"):
            doc = yaml.safe_load(path.read_text())
            waves[doc["metadata"]["name"]] = int(
                doc["metadata"]["annotations"]["argocd.argoproj.io/sync-wave"])
        self.assertEqual(
            waves,
            {"arc-controller": 24, "arc-ci-config": 25, "arc-publish-config": 25,
             "arc-ci": 26, "arc-publish": 26})


if __name__ == "__main__":
    unittest.main()
