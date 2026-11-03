"""Contract tests for private GHCR pulls in the TLE dev namespace."""

from pathlib import Path
import subprocess
import unittest

import yaml


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]


class TleRegistryCredentialTests(unittest.TestCase):
    def load_documents(self, relative_path):
        with (REPOSITORY_ROOT / relative_path).open(encoding="utf-8") as stream:
            return [document for document in yaml.safe_load_all(stream) if document]

    def test_vault_projects_only_the_docker_config(self):
        resources = self.load_documents("apps/tle-dev/config/registry-credentials.yaml")
        by_kind_name = {
            (resource["kind"], resource["metadata"]["name"]): resource
            for resource in resources
        }

        auth = by_kind_name[("VaultAuth", "tle-dev-registry")]
        self.assertEqual("vso-ghcr", auth["spec"]["kubernetes"]["role"])
        self.assertEqual("tle-dev-registry", auth["spec"]["kubernetes"]["serviceAccount"])

        secret = by_kind_name[("VaultStaticSecret", "ghcr-pull")]
        self.assertEqual("tle-dev-registry", secret["spec"]["vaultAuthRef"])
        self.assertEqual("ghcr", secret["spec"]["path"])
        destination = secret["spec"]["destination"]
        self.assertEqual("kubernetes.io/dockerconfigjson", destination["type"])
        self.assertEqual(
            {".dockerconfigjson"},
            set(destination["transformation"]["templates"]),
        )
        self.assertEqual([".*"], destination["transformation"]["excludes"])

    def test_every_deployment_and_dynamic_game_pod_can_pull_private_images(self):
        rendered = subprocess.run(
            ["kubectl", "kustomize", str(REPOSITORY_ROOT / "apps/tle-dev/config")],
            check=True,
            capture_output=True,
            text=True,
        ).stdout
        resources = [document for document in yaml.safe_load_all(rendered) if document]

        deployments = [resource for resource in resources if resource["kind"] == "Deployment"]
        self.assertTrue(deployments)
        for deployment in deployments:
            self.assertEqual(
                [{"name": "ghcr-pull"}],
                deployment["spec"]["template"]["spec"]["imagePullSecrets"],
                deployment["metadata"]["name"],
            )

        default = next(
            resource for resource in resources
            if resource["kind"] == "ServiceAccount"
            and resource["metadata"]["name"] == "default"
        )
        self.assertEqual([{"name": "ghcr-pull"}], default["imagePullSecrets"])

    def test_vault_role_is_limited_to_the_two_registry_identities(self):
        script = (REPOSITORY_ROOT / "platform/vault/configure-vault.sh").read_text(
            encoding="utf-8"
        )
        role = script.split('echo "==> role vso-ghcr"', 1)[1].split(
            'echo "==> seeding homelab/grafana-cloud"', 1
        )[0]
        self.assertIn("bound_service_account_names=registry,tle-dev-registry", role)
        self.assertIn("bound_service_account_namespaces=apps,tle-dev", role)


if __name__ == "__main__":
    unittest.main()
