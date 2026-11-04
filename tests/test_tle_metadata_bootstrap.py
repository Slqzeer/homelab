"""Contract tests for the TLE metadata baseline bootstrap workload."""

from pathlib import Path
import unittest

import yaml


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
BOOTSTRAP_MANIFEST = "apps/tle-dev/config/metadata-bootstrap.yaml"
METADATA_IMAGE = (
    "ghcr.io/the-last-exam/metadata-service:v0.0.2@sha256:"
    "b62fb8e977e4c7a491583456f11d857ad4f9a6d62ec1e85eb3ad38fbfcab788f"
)


def documents(relative_path):
    with (REPOSITORY_ROOT / relative_path).open(encoding="utf-8") as stream:
        return [document for document in yaml.safe_load_all(stream) if document]


def resource_wave(resource):
    annotations = resource["metadata"].get("annotations") or {}
    return int(annotations.get("argocd.argoproj.io/sync-wave", "0"))


class TleMetadataBootstrapTests(unittest.TestCase):
    def test_bootstrap_uses_the_release_image_and_packaged_command(self):
        job, = documents(BOOTSTRAP_MANIFEST)
        self.assertEqual("Job", job["kind"])
        self.assertEqual("tle-dev-metadata-seed-baseline", job["metadata"]["name"])
        self.assertEqual(
            "Sync", job["metadata"]["annotations"]["argocd.argoproj.io/hook"]
        )
        self.assertEqual(
            "BeforeHookCreation",
            job["metadata"]["annotations"]["argocd.argoproj.io/hook-delete-policy"],
        )
        self.assertEqual("1", job["metadata"]["annotations"]["argocd.argoproj.io/sync-wave"])

        pod_spec = job["spec"]["template"]["spec"]
        container, = pod_spec["containers"]
        self.assertEqual(METADATA_IMAGE, container["image"])
        self.assertEqual(["/usr/local/bin/metadata-seed-baseline"], container["command"])
        self.assertNotIn("args", container)
        self.assertEqual("OnFailure", pod_spec["restartPolicy"])
        self.assertEqual([{"name": "ghcr-pull"}], pod_spec["imagePullSecrets"])

        workloads = documents("apps/tle-dev/config/workloads.yaml")
        metadata_deployment = next(
            resource
            for resource in workloads
            if resource["kind"] == "Deployment"
            and resource["metadata"]["name"] == "tle-dev-metadata-service"
        )
        self.assertEqual(
            metadata_deployment["spec"]["template"]["spec"]["containers"][0]["image"],
            container["image"],
        )

    def test_bootstrap_uses_existing_database_sources_without_credentials_in_git(self):
        job, = documents(BOOTSTRAP_MANIFEST)
        container, = job["spec"]["template"]["spec"]["containers"]
        self.assertEqual(
            [{"configMapRef": {"name": "tle-dev-metadata-service-config"}}],
            container["envFrom"],
        )
        env = {item["name"]: item for item in container["env"]}
        self.assertEqual(
            {
                "name": "DATABASE_USER",
                "valueFrom": {
                    "secretKeyRef": {
                        "name": "tle-dev-data-secrets",
                        "key": "METADATA_DATABASE_USER",
                    }
                },
            },
            env["DATABASE_USER"],
        )
        self.assertEqual(
            {
                "name": "DATABASE_PASSWORD",
                "valueFrom": {
                    "secretKeyRef": {
                        "name": "tle-dev-data-secrets",
                        "key": "METADATA_DATABASE_PASSWORD",
                    }
                },
            },
            env["DATABASE_PASSWORD"],
        )
        self.assertNotIn("DATABASE_URL", env)

        manifest_text = (REPOSITORY_ROOT / BOOTSTRAP_MANIFEST).read_text(encoding="utf-8")
        self.assertNotIn("metadata_password", manifest_text)
        self.assertNotIn("postgres://tle_dev_metadata:", manifest_text)

    def test_database_creation_precedes_bootstrap_and_match_startup_follows_it(self):
        job, = documents(BOOTSTRAP_MANIFEST)
        bootstrap_wave = resource_wave(job)

        database_job = next(
            resource
            for resource in documents("apps/tle-dev/config/postgres-job.yaml")
            if resource["kind"] == "Job"
            and resource["metadata"]["name"] == "tle-dev-database"
        )
        self.assertLess(resource_wave(database_job), bootstrap_wave)

        guarded_deployments = {
            "tle-dev-metadata-service": next(
                resource
                for resource in documents("apps/tle-dev/config/workloads.yaml")
                if resource["kind"] == "Deployment"
                and resource["metadata"]["name"] == "tle-dev-metadata-service"
            ),
            "tle-dev-matchmaking-service": next(
                resource
                for resource in documents("apps/tle-dev/config/workloads.yaml")
                if resource["kind"] == "Deployment"
                and resource["metadata"]["name"] == "tle-dev-matchmaking-service"
            ),
            "tle-dev-game-master": next(
                resource
                for resource in documents("apps/tle-dev/config/game-master.yaml")
                if resource["kind"] == "Deployment"
                and resource["metadata"]["name"] == "tle-dev-game-master"
            ),
        }
        for name, deployment in guarded_deployments.items():
            with self.subTest(name=name):
                self.assertLess(bootstrap_wave, resource_wave(deployment))

    def test_bootstrap_has_only_postgres_network_access(self):
        job, = documents(BOOTSTRAP_MANIFEST)
        labels = job["spec"]["template"]["metadata"]["labels"]
        self.assertEqual("tle-dev-metadata-seed-baseline", labels["app"])
        self.assertNotIn("app-group", labels)

        policies = {
            resource["metadata"]["name"]: resource
            for resource in documents("apps/tle-dev/config/networkpolicy.yaml")
            if resource["kind"] == "NetworkPolicy"
        }
        policy = policies["tle-dev-metadata-bootstrap-to-postgres"]
        self.assertEqual(
            {"app": "tle-dev-metadata-seed-baseline"},
            policy["spec"]["podSelector"]["matchLabels"],
        )
        egress, = policy["spec"]["egress"]
        self.assertEqual([{"protocol": "TCP", "port": 5432}], egress["ports"])
        peer, = egress["to"]
        self.assertEqual(
            {"kubernetes.io/metadata.name": "databases"},
            peer["namespaceSelector"]["matchLabels"],
        )
        self.assertEqual({"app": "postgres"}, peer["podSelector"]["matchLabels"])

        postgres_clients = next(
            resource
            for resource in documents("platform/databases/postgres/config/networkpolicy.yaml")
            if resource["kind"] == "NetworkPolicy"
            and resource["metadata"]["name"] == "postgres-clients"
        )
        ingress, = postgres_clients["spec"]["ingress"]
        self.assertIn(
            {
                "namespaceSelector": {
                    "matchLabels": {"kubernetes.io/metadata.name": "tle-dev"}
                },
                "podSelector": {
                    "matchLabels": {"app": "tle-dev-metadata-seed-baseline"}
                },
            },
            ingress["from"],
        )

    def test_bootstrap_is_in_the_tle_render(self):
        kustomization, = documents("apps/tle-dev/config/kustomization.yaml")
        self.assertIn("metadata-bootstrap.yaml", kustomization["resources"])


if __name__ == "__main__":
    unittest.main()
