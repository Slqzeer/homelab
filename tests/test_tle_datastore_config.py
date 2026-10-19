"""Contract tests for TLE datastore address and credential delivery."""

from pathlib import Path
import unittest
from urllib.parse import urlsplit

import yaml


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]

SHARED_DATABASE_URL = "postgres://postgres.databases:5432/tle_dev"
METADATA_DATABASE_URL = "postgres://postgres.databases:5432/tle_dev_metadata"
QUEST_DATABASE_URL = "postgres://postgres.databases:5432/tle_dev_quest"
NATS_URL = "nats://tle-dev-nats.tle-dev.svc.cluster.local:4222"
VALKEY_URL = "redis://tle-dev-valkey.tle-dev.svc.cluster.local:6379"
VALKEY_ADDR = "tle-dev-valkey.tle-dev.svc.cluster.local:6379"

EXPECTED_SERVICE_CONFIG = {
    "admin-audit-writer": {"DATABASE_URL": SHARED_DATABASE_URL},
    "auth-service": {
        "DATABASE_URL": SHARED_DATABASE_URL,
        "NATS_URL": NATS_URL,
        "VALKEY_URL": VALKEY_URL,
    },
    "game-master": {"NATS_URL": NATS_URL},
    "game-service": {"NATS_URL": NATS_URL, "VALKEY_URL": VALKEY_URL},
    "internal-admin-gateway": {"VALKEY_URL": VALKEY_URL},
    "internal-ops-gateway": {"VALKEY_URL": VALKEY_URL},
    "lobby-service": {"NATS_URL": NATS_URL, "VALKEY_ADDR": VALKEY_ADDR},
    "matchmaking-service": {"NATS_URL": NATS_URL, "VALKEY_ADDR": VALKEY_ADDR},
    "metadata-service": {
        "DATABASE_URL": METADATA_DATABASE_URL,
        "NATS_URL": NATS_URL,
        "VALKEY_ADDR": VALKEY_ADDR,
    },
    "notification-service": {
        "DATABASE_URL": SHARED_DATABASE_URL,
        "NATS_URL": NATS_URL,
        "VALKEY_ADDR": VALKEY_ADDR,
    },
    "player-service": {
        "DATABASE_URL": SHARED_DATABASE_URL,
        "NATS_URL": NATS_URL,
        "VALKEY_ADDR": VALKEY_ADDR,
    },
    "projection-writer": {"DATABASE_URL": SHARED_DATABASE_URL, "NATS_URL": NATS_URL},
    "quest-service": {"QUEST_DATABASE_URL": QUEST_DATABASE_URL, "QUEST_NATS_URL": NATS_URL},
    "social-service": {
        "DATABASE_URL": SHARED_DATABASE_URL,
        "NATS_URL": NATS_URL,
        "VALKEY_ADDR": VALKEY_ADDR,
    },
    "wsterminator": {"NATS_URL": NATS_URL, "VALKEY_ADDR": VALKEY_ADDR},
}


class TleDatastoreConfigTests(unittest.TestCase):
    def load_documents(self, relative_path):
        with (REPOSITORY_ROOT / relative_path).open(encoding="utf-8") as stream:
            return [document for document in yaml.safe_load_all(stream) if document]

    def test_each_service_gets_credential_free_datastore_config(self):
        resources = self.load_documents("apps/tle-dev/config/service-config.yaml")
        config_maps = {
            resource["metadata"]["name"].removeprefix("tle-dev-").removesuffix("-config"):
                resource["data"]
            for resource in resources
            if resource["kind"] == "ConfigMap"
        }
        self.assertEqual(EXPECTED_SERVICE_CONFIG, config_maps)

        for service, data in config_maps.items():
            for key, value in data.items():
                self.assertNotIn("@", value, f"{service} {key} contains userinfo")
                if key.endswith("_URL"):
                    parsed = urlsplit(value)
                    self.assertIsNone(parsed.username, f"{service} {key} contains a username")
                    self.assertIsNone(parsed.password, f"{service} {key} contains a password")

    def test_data_secret_adds_split_credentials_alongside_legacy_urls(self):
        resources = self.load_documents("apps/tle-dev/config/vault-secrets.yaml")
        data_secret = next(
            resource for resource in resources
            if resource["kind"] == "VaultStaticSecret"
            and resource["metadata"]["name"] == "tle-dev-data"
        )
        templates = data_secret["spec"]["destination"]["transformation"]["templates"]

        self.assertEqual(
            {
                "DATABASE_URL",
                "METADATA_DATABASE_URL",
                "QUEST_DATABASE_URL",
                "DATABASE_USER",
                "DATABASE_PASSWORD",
                "METADATA_DATABASE_USER",
                "METADATA_DATABASE_PASSWORD",
                "QUEST_DATABASE_USER",
                "QUEST_DATABASE_PASSWORD",
            },
            set(templates),
        )
        self.assertEqual("tle_dev", templates["DATABASE_USER"]["text"])
        self.assertEqual("tle_dev_metadata", templates["METADATA_DATABASE_USER"]["text"])
        self.assertEqual("tle_dev_quest", templates["QUEST_DATABASE_USER"]["text"])
        self.assertEqual('{{ get .Secrets "password" }}', templates["DATABASE_PASSWORD"]["text"])
        self.assertEqual(
            '{{ get .Secrets "metadata_password" }}',
            templates["METADATA_DATABASE_PASSWORD"]["text"],
        )
        self.assertEqual(
            '{{ get .Secrets "quest_password" }}',
            templates["QUEST_DATABASE_PASSWORD"]["text"],
        )

    def test_service_config_is_rendered_by_the_tle_kustomization(self):
        kustomization = self.load_documents("apps/tle-dev/config/kustomization.yaml")[0]
        self.assertIn("service-config.yaml", kustomization["resources"])


if __name__ == "__main__":
    unittest.main()
