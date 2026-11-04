"""Contract tests for TLE quest roles and ordered rollout hooks."""

from pathlib import Path
import unittest

import yaml


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
MANIFEST = "apps/tle-dev/config/quest-workloads.yaml"
IMAGE = (
    "ghcr.io/the-last-exam/quest-service:v0.0.2@sha256:"
    "3dc4bf8f9226efb06695c292c67031073ad7b4f13872bc2748547145b7b1ac26"
)
ROLES = {
    "tle-dev-quest-api": "quest-api",
    "tle-dev-quest-ingress": "quest-ingress",
    "tle-dev-quest-evaluator": "quest-evaluator",
    "tle-dev-quest-operations": "quest-operations",
    "tle-dev-quest-outbox": "quest-outbox",
}


class TleQuestWorkloadTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with (REPOSITORY_ROOT / MANIFEST).open(encoding="utf-8") as stream:
            cls.documents = [document for document in yaml.safe_load_all(stream) if document]

    def resources(self, kind):
        return {
            document["metadata"]["name"]: document
            for document in self.documents
            if document["kind"] == kind
        }

    def test_each_role_is_a_separate_pinned_deployment(self):
        deployments = self.resources("Deployment")
        self.assertEqual(set(ROLES), set(deployments))

        for name, role in ROLES.items():
            with self.subTest(name=name):
                deployment = deployments[name]
                self.assertEqual(1, deployment["spec"]["replicas"])
                pod_spec = deployment["spec"]["template"]["spec"]
                container = pod_spec["containers"][0]
                self.assertEqual(IMAGE, container["image"])
                env = {item["name"]: item for item in container["env"]}
                self.assertEqual(role, env["QUEST_ROLE"]["value"])
                self.assertEqual(
                    {"cpu": "50m", "memory": "128Mi"},
                    container["resources"]["requests"],
                )
                self.assertEqual(
                    {"cpu": "500m", "memory": "512Mi"},
                    container["resources"]["limits"],
                )
                self.assertEqual("/healthz", container["startupProbe"]["httpGet"]["path"])
                self.assertEqual("/healthz", container["livenessProbe"]["httpGet"]["path"])
                self.assertEqual("/readyz", container["readinessProbe"]["httpGet"]["path"])

    def test_hpas_exist_only_for_listed_roles_and_preserve_bounds(self):
        hpas = self.resources("HorizontalPodAutoscaler")
        self.assertEqual({"tle-dev-quest-api", "tle-dev-quest-evaluator"}, set(hpas))
        self.assertEqual((1, 6), (
            hpas["tle-dev-quest-api"]["spec"]["minReplicas"],
            hpas["tle-dev-quest-api"]["spec"]["maxReplicas"],
        ))
        self.assertEqual((1, 8), (
            hpas["tle-dev-quest-evaluator"]["spec"]["minReplicas"],
            hpas["tle-dev-quest-evaluator"]["spec"]["maxReplicas"],
        ))
        for hpa in hpas.values():
            self.assertEqual(
                "3", hpa["metadata"]["annotations"]["argocd.argoproj.io/sync-wave"]
            )

    def test_sync_hooks_gate_migrate_then_seed_before_deployments(self):
        jobs = self.resources("Job")
        self.assertEqual(
            {"tle-dev-quest-migrate", "tle-dev-quest-seed-baseline"}, set(jobs)
        )
        migrate = jobs["tle-dev-quest-migrate"]
        seed = jobs["tle-dev-quest-seed-baseline"]
        self.assertEqual("Sync", migrate["metadata"]["annotations"]["argocd.argoproj.io/hook"])
        self.assertEqual("Sync", seed["metadata"]["annotations"]["argocd.argoproj.io/hook"])
        self.assertEqual("1", migrate["metadata"]["annotations"]["argocd.argoproj.io/sync-wave"])
        self.assertEqual("2", seed["metadata"]["annotations"]["argocd.argoproj.io/sync-wave"])
        for deployment in self.resources("Deployment").values():
            self.assertEqual(
                "3",
                deployment["metadata"]["annotations"]["argocd.argoproj.io/sync-wave"],
            )
        self.assertEqual("OnFailure", migrate["spec"]["template"]["spec"]["restartPolicy"])
        self.assertEqual("OnFailure", seed["spec"]["template"]["spec"]["restartPolicy"])

        migrate_container = migrate["spec"]["template"]["spec"]["containers"][0]
        self.assertEqual(IMAGE, migrate_container["image"])
        self.assertEqual(
            [{"name": "ghcr-pull"}],
            migrate["spec"]["template"]["spec"]["imagePullSecrets"],
        )
        self.assertEqual(["/usr/local/bin/quest-migrate"], migrate_container["command"])
        self.assertEqual(["-env", "production", "-direction", "up"], migrate_container["args"])

        seed_container = seed["spec"]["template"]["spec"]["containers"][0]
        self.assertEqual(IMAGE, seed_container["image"])
        self.assertEqual(
            [{"name": "ghcr-pull"}],
            seed["spec"]["template"]["spec"]["imagePullSecrets"],
        )
        self.assertEqual(["/usr/local/bin/quest-seed-baseline"], seed_container["command"])
        self.assertEqual(["production"], seed_container["args"])
        seed_env = {item["name"]: item for item in seed_container["env"]}
        self.assertEqual("quest-evaluator", seed_env["QUEST_ROLE"]["value"])

    def test_operations_mounts_history_hydration_item_read_only(self):
        deployment = self.resources("Deployment")["tle-dev-quest-operations"]
        pod_spec = deployment["spec"]["template"]["spec"]
        container = pod_spec["containers"][0]
        self.assertEqual(
            [{
                "name": "history-hydration",
                "mountPath": "/config/history-hydration.json",
                "subPath": "history-hydration.json",
                "readOnly": True,
            }],
            container["volumeMounts"],
        )
        self.assertEqual(
            [{
                "name": "history-hydration",
                "configMap": {
                    "name": "tle-dev-quest-runtime",
                    "items": [{
                        "key": "history-hydration.json",
                        "path": "history-hydration.json",
                    }],
                },
            }],
            pod_spec["volumes"],
        )

    def test_only_api_role_is_in_the_player_facing_service_group(self):
        deployments = self.resources("Deployment")
        api_labels = deployments["tle-dev-quest-api"]["spec"]["template"]["metadata"]["labels"]
        self.assertEqual("tle-dev-service", api_labels["app-group"])
        for name in set(ROLES) - {"tle-dev-quest-api"}:
            labels = deployments[name]["spec"]["template"]["metadata"]["labels"]
            self.assertNotIn("app-group", labels)

    def test_manifest_is_in_tle_render(self):
        with (REPOSITORY_ROOT / "apps/tle-dev/config/kustomization.yaml").open(
            encoding="utf-8"
        ) as stream:
            kustomization = yaml.safe_load(stream)
        self.assertIn("quest-workloads.yaml", kustomization["resources"])


if __name__ == "__main__":
    unittest.main()
