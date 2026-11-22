"""Contract tests for issue #63: provision auth-service's TOTP encryption key.

The-Last-Exam/server#230 makes ``AUTH_TOTP_ENCRYPTION_KEY`` a required
auth-service secret: base64 of exactly 32 random bytes, identical across
replicas and restarts. Releases after v0.0.2 fail startup without it, so it
must be provisioned before that promotion.
"""

from pathlib import Path
import unittest

import yaml


ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "apps/tle-dev/config"
KEY = "AUTH_TOTP_ENCRYPTION_KEY"


def documents(name):
    with (CONFIG / name).open(encoding="utf-8") as stream:
        return [item for item in yaml.safe_load_all(stream) if item]


class TleTotpEncryptionKeyTests(unittest.TestCase):
    def test_vault_projection_carries_the_key_and_restarts_auth_service(self):
        secret = next(
            item
            for item in documents("vault-secrets.yaml")
            if item["kind"] == "VaultStaticSecret"
            and item["metadata"]["name"] == "tle-dev-auth"
        )
        spec = secret["spec"]
        self.assertEqual("tle-dev/auth", spec["path"])
        self.assertEqual("tle-dev-auth-secrets", spec["destination"]["name"])
        self.assertEqual(
            {"text": '{{ get .Secrets "AUTH_TOTP_ENCRYPTION_KEY" }}'},
            spec["destination"]["transformation"]["templates"][KEY],
        )
        self.assertIn(
            {"kind": "Deployment", "name": "tle-dev-auth-service"},
            spec["rolloutRestartTargets"],
        )

    def test_auth_service_reads_the_key_only_from_its_secret(self):
        deployment = next(
            item
            for item in documents("workloads.yaml")
            if item["kind"] == "Deployment"
            and item["metadata"]["name"] == "tle-dev-auth-service"
        )
        container = deployment["spec"]["template"]["spec"]["containers"][0]
        env = {item["name"]: item for item in container["env"]}
        self.assertNotIn("value", env[KEY])
        self.assertEqual(
            {"name": "tle-dev-auth-secrets", "key": KEY},
            env[KEY]["valueFrom"]["secretKeyRef"],
        )

    def test_ceremony_generates_a_32_byte_base64_key_without_overwriting(self):
        script = (ROOT / "platform/vault/configure-vault.sh").read_text(
            encoding="utf-8"
        )
        # tle_ensure_field seeds only an absent field, so re-running the
        # ceremony never rotates the key under enrolled players.
        self.assertIn(
            "tle_generate_base64 32\n"
            f'tle_ensure_field homelab/tle-dev/auth {KEY} "generated"\n',
            script,
        )
        self.assertNotIn(f"tle_generate 32\ntle_ensure_field homelab/tle-dev/auth {KEY}", script)

    def test_key_is_never_a_literal_or_configmap_value(self):
        kustomization = documents("kustomization.yaml")[0]
        for name in kustomization["resources"]:
            for resource in documents(name):
                if resource["kind"] == "ConfigMap":
                    for value in (resource.get("data") or {}).values():
                        self.assertNotIn(KEY, value)
                if resource["kind"] in ("Deployment", "Job", "StatefulSet"):
                    for container in resource["spec"]["template"]["spec"]["containers"]:
                        for item in container.get("env") or []:
                            if item["name"] == KEY:
                                self.assertNotIn("value", item)


if __name__ == "__main__":
    unittest.main()
