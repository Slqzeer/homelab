"""Executable contract for the personal OIDC client registration hook."""

import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

import yaml


ROOT = Path(__file__).resolve().parents[2]
CONFIG = ROOT / "platform/keycloak/config"


def resources(path):
    if not path.is_file():
        raise AssertionError(f"missing manifest: {path.relative_to(ROOT)}")
    return list(yaml.safe_load_all(path.read_text(encoding="utf-8")))


def resource(path, kind, name):
    matches = [doc for doc in resources(path)
               if doc and doc.get("kind") == kind and doc.get("metadata", {}).get("name") == name]
    if len(matches) != 1:
        raise AssertionError(f"expected one {kind}/{name}, found {len(matches)}")
    return matches[0]


FAKE_KCADM = r'''#!/usr/bin/env python3
import json
import os
from pathlib import Path
import sys

state_path = Path(os.environ["FAKE_STATE"])
events_path = Path(os.environ["FAKE_EVENTS"])
state = json.loads(state_path.read_text())
args = sys.argv[1:]
event = {"args": args}
if args[:2] == ["config", "credentials"]:
    pass
elif args[:2] == ["get", "clients"]:
    if os.environ.get("FAKE_FAIL_LIST"):
        sys.exit(17)
    print(json.dumps([{"id": c["id"], "clientId": c["clientId"]}
                      for c in state if c["clientId"] == "homelab-portal"]))
elif args[0] == "get" and args[1].endswith("/client-secret"):
    uuid = args[1].split("/")[1]
    print(json.dumps({"value": next(c["secret"] for c in state if c["id"] == uuid)}))
elif args[0] == "get" and args[1].startswith("clients/"):
    uuid = args[1].split("/")[1]
    client = next(c for c in state if c["id"] == uuid)
    if args[1].endswith("/default-client-scopes"):
        print(json.dumps([{"id": name + "-id", "name": name}
                          for name in client.get("_default_scopes", [])]))
    else:
        print(json.dumps({k: v for k, v in client.items() if not k.startswith("_")}))
elif args[:2] == ["get", "client-scopes"]:
    print(json.dumps([{"id": name + "-id", "name": name}
                      for name in ("profile", "email", "groups")]))
elif args[:2] == ["create", "clients"]:
    body = json.load(sys.stdin)
    if os.environ.get("FAKE_LEAK_ON_CREATE"):
        print(body["secret"], file=sys.stderr)
        sys.exit(23)
    event["body_keys"] = sorted(body)
    body["id"] = "created-id"
    body["_default_scopes"] = ["profile", "email"]
    state.append(body)
elif args[0] == "update" and args[1].startswith("clients/"):
    uuid = args[1].split("/")[1]
    client = next(c for c in state if c["id"] == uuid)
    if "/default-client-scopes/" in args[1]:
        name = args[1].rsplit("/", 1)[1].removesuffix("-id")
        if not os.environ.get("FAKE_SCOPE_NOOP"):
            client.setdefault("_default_scopes", []).append(name)
    else:
        body = json.load(sys.stdin)
        event["body_keys"] = sorted(body)
        scopes = client.get("_default_scopes", [])
        if "--merge" in args:
            client.update({k: v for k, v in body.items() if k != "defaultClientScopes"})
        else:
            client.clear()
            client.update({k: v for k, v in body.items() if k != "defaultClientScopes"})
            client["id"] = uuid
        client["_default_scopes"] = scopes
else:
    sys.exit(18)
state_path.write_text(json.dumps(state))
with events_path.open("a") as output:
    output.write(json.dumps(event) + "\n")
'''

FAKE_VAULT = r'''#!/usr/bin/env python3
import json
import os
from pathlib import Path
import sys

state_path = Path(os.environ["FAKE_VAULT_STATE"])
events_path = Path(os.environ["FAKE_VAULT_EVENTS"])
state = json.loads(state_path.read_text())
args = sys.argv[1:]
if args[:2] == ["kv", "get"]:
    path = args[-1]
    field = next((arg.split("=", 1)[1] for arg in args[2:] if arg.startswith("-field=")), None)
    if os.environ.get("FAKE_VAULT_FAIL_MATCH") == f"{path}:{field or '-'}":
        print("transient Vault transport failure", file=sys.stderr)
        sys.exit(17)
    if path not in state:
        print(f"No value found at {path}")
        sys.exit(2)
    if field is not None and field not in state[path]:
        print(f'Field "{field}" not present in secret', file=sys.stderr)
        sys.exit(2)
    if field is not None:
        sys.stdout.write(state[path][field])
    else:
        print("present")
elif args[:2] in (["kv", "put"], ["kv", "patch"]):
    path = args[2]
    body = {}
    for item in args[3:]:
        key, value = item.split("=", 1)
        if not value.startswith("@"):
            sys.exit(3)
        body[key] = Path(value[1:]).read_text()
    if args[1] == "put":
        state[path] = body
    else:
        state.setdefault(path, {}).update(body)
    state_path.write_text(json.dumps(state))
    with events_path.open("a") as output:
        output.write(json.dumps({"operation": args[1], "path": path, "keys": sorted(body)}) + "\n")
else:
    sys.exit(4)
'''


class ClientRegistrationTests(unittest.TestCase):
    def script(self):
        manifest = CONFIG / "client-registration.yaml"
        config_map = resource(manifest, "ConfigMap", "keycloak-client-registration")
        return config_map["data"]["reconcile.sh"]

    def run_hook(self, state, *, fail_list=False, leak_on_create=False, scope_noop=False):
        with tempfile.TemporaryDirectory() as directory:
            temp = Path(directory)
            fake = temp / "kcadm.sh"
            fake.write_text(FAKE_KCADM, encoding="utf-8")
            fake.chmod(0o700)
            jq = temp / "jq"
            jq.write_text("#!/bin/sh\nprintf '%s\\n' \"$*\" >> \"$FAKE_JQ_ARGS\"\nexec /usr/bin/jq \"$@\"\n", encoding="utf-8")
            jq.chmod(0o700)
            script = self.script()
            self.assertIn("KCADM=/opt/keycloak/bin/kcadm.sh", script)
            self.assertIn("JQ=/tools/jq", script)
            script = script.replace("KCADM=/opt/keycloak/bin/kcadm.sh", f"KCADM={fake}")
            script = script.replace("JQ=/tools/jq", f"JQ={jq}")
            script_path = temp / "reconcile.sh"
            script_path.write_text(script, encoding="utf-8")
            state_path = temp / "state.json"
            state_path.write_text(json.dumps(state), encoding="utf-8")
            events_path = temp / "events.jsonl"
            jq_args_path = temp / "jq-args"
            env = dict(os.environ, FAKE_STATE=str(state_path), FAKE_EVENTS=str(events_path),
                       FAKE_JQ_ARGS=str(jq_args_path), KC_ADMIN_USERNAME="admin",
                       KC_CLI_PASSWORD="admin-canary-secret",
                       PORTAL_CLIENT_SECRET="portal-canary-secret")
            if fail_list:
                env["FAKE_FAIL_LIST"] = "1"
            if leak_on_create:
                env["FAKE_LEAK_ON_CREATE"] = "1"
            if scope_noop:
                env["FAKE_SCOPE_NOOP"] = "1"
            runs = []
            for _ in range(2 if not fail_list else 1):
                runs.append(subprocess.run(["/bin/sh", str(script_path)], env=env,
                                           text=True, capture_output=True, check=False))
                if runs[-1].returncode:
                    break
            events = [json.loads(line) for line in events_path.read_text().splitlines()] if events_path.exists() else []
            jq_args = jq_args_path.read_text() if jq_args_path.exists() else ""
            return runs, json.loads(state_path.read_text()), events, jq_args

    def test_client_contract_and_second_run_converges(self):
        unrelated = {"id": "other-id", "clientId": "unrelated", "secret": "untouched"}
        runs, state, events, jq_args = self.run_hook([unrelated])
        self.assertEqual([0, 0], [run.returncode for run in runs], [run.stderr for run in runs])
        self.assertEqual(unrelated, state[0])
        portal = next(client for client in state if client["clientId"] == "homelab-portal")
        self.assertEqual("openid-connect", portal["protocol"])
        self.assertTrue(portal["enabled"])
        self.assertFalse(portal["publicClient"])
        self.assertTrue(portal["clientAuthenticatorType"] == "client-secret")
        self.assertTrue(portal["standardFlowEnabled"])
        self.assertFalse(portal["implicitFlowEnabled"])
        self.assertFalse(portal["directAccessGrantsEnabled"])
        self.assertEqual(["https://portal.taildf6cd4.ts.net/auth/callback"], portal["redirectUris"])
        self.assertEqual(["https://portal.taildf6cd4.ts.net"], portal["webOrigins"])
        self.assertEqual("https://portal.taildf6cd4.ts.net/auth/logout",
                         portal["attributes"]["post.logout.redirect.uris"])
        self.assertIn("groups", portal["_default_scopes"])
        writes = [event for event in events if event["args"][0] in ("create", "update")]
        self.assertEqual(["create", "update"], [event["args"][0] for event in writes])
        self.assertIn("/default-client-scopes/groups-id", writes[1]["args"][1])
        for event in events:
            self.assertNotIn("portal-canary-secret", " ".join(event["args"]))
            self.assertNotIn("admin-canary-secret", " ".join(event["args"]))
        self.assertNotIn("portal-canary-secret", jq_args)
        self.assertNotIn("admin-canary-secret", jq_args)
        self.assertNotIn("portal-canary-secret", "".join(run.stdout + run.stderr for run in runs))

    def test_drift_updates_only_allowlisted_client(self):
        unrelated = {"id": "other-id", "clientId": "unrelated", "secret": "untouched"}
        portal = {"id": "portal-id", "clientId": "homelab-portal", "secret": "old",
                  "redirectUris": ["https://wrong.invalid/callback"], "custom": "preserve"}
        runs, state, events, _ = self.run_hook([unrelated, portal])
        self.assertEqual([0, 0], [run.returncode for run in runs], [run.stderr for run in runs])
        self.assertEqual(unrelated, state[0])
        self.assertEqual("preserve", state[1]["custom"])
        self.assertEqual("portal-canary-secret", state[1]["secret"])
        writes = [event for event in events if event["args"][0] in ("create", "update")]
        self.assertEqual(["update", "update", "update", "update"],
                         [event["args"][0] for event in writes])
        self.assertEqual("clients/portal-id", writes[0]["args"][1])
        self.assertIn("--merge", writes[0]["args"])
        self.assertEqual(["profile", "email", "groups"], state[1]["_default_scopes"])

    def test_existing_scope_drift_converges(self):
        portal = {"id": "portal-id", "clientId": "homelab-portal", "secret": "old",
                  "redirectUris": ["https://wrong.invalid/callback"],
                  "_default_scopes": ["profile", "email", "custom"]}
        runs, state, events, _ = self.run_hook([portal])
        self.assertEqual([0, 0], [run.returncode for run in runs], [run.stderr for run in runs])
        self.assertEqual(["profile", "email", "custom", "groups"], state[0]["_default_scopes"])
        writes = [event for event in events if event["args"][0] == "update"]
        self.assertEqual(2, len(writes), writes)
        self.assertEqual("clients/portal-id/default-client-scopes/groups-id", writes[1]["args"][1])

    def test_scope_link_must_be_verified(self):
        runs, state, _, _ = self.run_hook([], scope_noop=True)
        self.assertNotEqual(0, runs[0].returncode)
        self.assertNotIn("groups", state[0]["_default_scopes"])
        self.assertIn("verification failed", runs[0].stderr)

    def test_duplicate_client_ids_fail_closed(self):
        portal = {"clientId": "homelab-portal", "secret": "old"}
        runs, state, events, _ = self.run_hook([dict(portal, id="one"), dict(portal, id="two")])
        self.assertNotEqual(0, runs[0].returncode)
        self.assertIn("duplicate", runs[0].stderr.lower())
        self.assertEqual(2, len(state))
        self.assertFalse(any(e["args"][0] in ("create", "update") for e in events))

    def test_client_list_failure_does_not_create(self):
        runs, state, events, _ = self.run_hook([], fail_list=True)
        self.assertNotEqual(0, runs[0].returncode)
        self.assertEqual([], state)
        self.assertFalse(any(e["args"][0] in ("create", "update") for e in events))

    def test_secret_bearing_cli_error_is_redacted(self):
        runs, state, _, _ = self.run_hook([], leak_on_create=True)
        self.assertNotEqual(0, runs[0].returncode)
        self.assertEqual([], state)
        self.assertNotIn("portal-canary-secret", runs[0].stdout + runs[0].stderr)
        self.assertIn("failed", runs[0].stderr.lower())

    def test_hook_and_vault_projection_contract(self):
        manifest = CONFIG / "client-registration.yaml"
        job = resource(manifest, "Job", "keycloak-client-registration")
        self.assertEqual("PostSync", job["metadata"]["annotations"]["argocd.argoproj.io/hook"])
        self.assertGreater(job["spec"]["activeDeadlineSeconds"], 0)
        pod = job["spec"]["template"]["spec"]
        self.assertEqual("Never", pod["restartPolicy"])
        container = pod["containers"][0]
        keycloak_image = ("quay.io/keycloak/keycloak:26.7.4@sha256:"
                          "82a77884f3af238beab1e7afd63b5f530e1b5c0590bd7aa60b40a40463e29b2c")
        self.assertEqual(keycloak_image, container["image"])
        self.assertIn("/tools", [mount["mountPath"] for mount in container["volumeMounts"]])
        self.assertTrue(container["resources"]["requests"])
        self.assertTrue(container["resources"]["limits"])
        image_volume = next(volume["image"] for volume in pod["volumes"] if "image" in volume)
        self.assertRegex(image_volume["reference"], r"^ghcr\.io/jqlang/jq:[^@]+@sha256:[0-9a-f]{64}$")
        init = pod["initContainers"][0]
        self.assertEqual(keycloak_image, init["image"])
        init_command = " ".join(init["command"])
        self.assertIn("sha256sum -c", init_command)
        self.assertIn("cp /jq-image/jq /tools/jq", init_command)
        self.assertRegex(init_command, r"[0-9a-f]{64}")
        env = {entry["name"]: entry for entry in container["env"]}
        self.assertEqual("keycloak-admin", env["KC_CLI_PASSWORD"]["valueFrom"]["secretKeyRef"]["name"])
        self.assertEqual("keycloak-portal-client", env["PORTAL_CLIENT_SECRET"]["valueFrom"]["secretKeyRef"]["name"])
        script = self.script()
        self.assertIn("/tools/jq", script)
        self.assertIn("-f -", script)
        self.assertNotIn("--password", script)
        self.assertNotIn("get realms", script)
        self.assertNotIn("update realms", script)
        self.assertNotIn("delete clients", script)
        self.assertNotIn("--arg secret", script)
        self.assertIn("env.PORTAL_CLIENT_SECRET", script)
        self.assertIn("del(.secret)", script)
        for doc in resources(CONFIG / "vault-secrets.yaml"):
            if doc and doc["kind"] == "VaultStaticSecret":
                self.assertIs(doc["spec"]["destination"]["transformation"]["excludeRaw"], True)
        projection = resource(CONFIG / "vault-secrets.yaml", "VaultStaticSecret", "keycloak-portal-client")
        self.assertEqual("keycloak-portal", projection["spec"]["path"])
        self.assertEqual("keycloak-portal-client", projection["spec"]["destination"]["name"])

    def test_vault_policy_is_narrow_and_portal_role_is_bound(self):
        script = (ROOT / "platform/vault/configure-vault.sh").read_text(encoding="utf-8")
        for path in ("homelab/keycloak-portal", "homelab/portal"):
            self.assertIn(f"vault_optional_get {path}", script)
            self.assertIn(f"vault kv put {path}", script)
        self.assertIn("clientSecret=@", script)
        self.assertIn("oidc-client-secret=@", script)
        self.assertIn("session-current-key=@", script)
        self.assertIn('path "homelab/data/keycloak-portal"', script)
        self.assertIn('path "homelab/data/portal"', script)
        role = script.split("vault write auth/kubernetes/role/vso-portal \\\n", 1)[1].split("\n\n", 1)[0]
        self.assertIn("bound_service_account_names=homelab-portal", role)
        self.assertIn("bound_service_account_namespaces=portal", role)
        self.assertIn("token_policies=vso-portal-read", role)
        self.assertNotIn("*", role)

    def test_vault_seed_repairs_empty_key_and_preserves_rotation_key(self):
        source = (ROOT / "platform/vault/configure-vault.sh").read_text(encoding="utf-8")
        seed = source.split('echo "==> seeding homelab/keycloak-portal and homelab/portal"', 1)[1]
        seed = seed.split('echo "==> policy vso-keycloak-read"', 1)[0]
        with tempfile.TemporaryDirectory() as directory:
            temp = Path(directory)
            vault = temp / "vault"
            vault.write_text(FAKE_VAULT, encoding="utf-8")
            vault.chmod(0o700)
            script = temp / "seed.sh"
            script.write_text('set -eu\necho "==> seeding homelab/keycloak-portal and homelab/portal"' + seed,
                              encoding="utf-8")
            state_path = temp / "vault.json"
            state_path.write_text(json.dumps({
                "homelab/keycloak-portal": {"clientSecret": "new-canary"},
                "homelab/portal": {"oidc-client-secret": "old-canary",
                                   "session-current-key": "", "session-previous-key": "previous-canary"}
            }), encoding="utf-8")
            events_path = temp / "vault-events.jsonl"
            env = dict(os.environ, PATH=f"{temp}:{os.environ['PATH']}",
                       FAKE_VAULT_STATE=str(state_path), FAKE_VAULT_EVENTS=str(events_path))
            first = subprocess.run(["/bin/sh", str(script)], env=env, text=True,
                                   capture_output=True, check=False)
            self.assertEqual(0, first.returncode, first.stderr)
            state = json.loads(state_path.read_text())
            self.assertEqual("new-canary", state["homelab/portal"]["oidc-client-secret"])
            self.assertTrue(state["homelab/portal"]["session-current-key"])
            self.assertEqual("previous-canary", state["homelab/portal"]["session-previous-key"])
            writes = events_path.read_text().splitlines()
            second = subprocess.run(["/bin/sh", str(script)], env=env, text=True,
                                    capture_output=True, check=False)
            self.assertEqual(0, second.returncode, second.stderr)
            self.assertEqual(state, json.loads(state_path.read_text()))
            self.assertEqual(writes, events_path.read_text().splitlines())
            self.assertNotIn("new-canary", first.stdout + first.stderr + second.stdout + second.stderr)
            self.assertNotIn("previous-canary", first.stdout + first.stderr + second.stdout + second.stderr)

    def test_vault_missing_fields_recover_and_keep_other_values(self):
        source = (ROOT / "platform/vault/configure-vault.sh").read_text(encoding="utf-8")
        seed = source.split('echo "==> seeding homelab/keycloak-portal and homelab/portal"', 1)[1]
        seed = seed.split('echo "==> policy vso-keycloak-read"', 1)[0]
        cases = (
            ("clientSecret",
             {"homelab/keycloak-portal": {},
              "homelab/portal": {"oidc-client-secret": "portal-canary",
                                 "session-current-key": "session-canary",
                                 "session-previous-key": "previous-canary"}},
             "homelab/keycloak-portal", "clientSecret", "portal-canary", "put"),
            ("oidc-client-secret",
             {"homelab/keycloak-portal": {"clientSecret": "keycloak-canary"},
              "homelab/portal": {"session-current-key": "session-canary",
                                 "session-previous-key": "previous-canary"}},
             "homelab/portal", "oidc-client-secret", "keycloak-canary", "patch"),
            ("session-current-key",
             {"homelab/keycloak-portal": {"clientSecret": "keycloak-canary"},
              "homelab/portal": {"oidc-client-secret": "keycloak-canary",
                                 "session-previous-key": "previous-canary"}},
             "homelab/portal", "session-current-key", None, "patch"),
        )
        for missing, initial, path, key, expected, operation in cases:
            with self.subTest(missing=missing), tempfile.TemporaryDirectory() as directory:
                temp = Path(directory)
                vault = temp / "vault"
                vault.write_text(FAKE_VAULT, encoding="utf-8")
                vault.chmod(0o700)
                script = temp / "seed.sh"
                script.write_text('set -eu\necho "==> seeding homelab/keycloak-portal and homelab/portal"' + seed,
                                  encoding="utf-8")
                state_path = temp / "vault.json"
                state_path.write_text(json.dumps(initial), encoding="utf-8")
                events_path = temp / "vault-events.jsonl"
                env = dict(os.environ, PATH=f"{temp}:{os.environ['PATH']}",
                           FAKE_VAULT_STATE=str(state_path), FAKE_VAULT_EVENTS=str(events_path))
                first = subprocess.run(["/bin/sh", str(script)], env=env, text=True,
                                       capture_output=True, check=False)
                self.assertEqual(0, first.returncode, first.stderr)
                state = json.loads(state_path.read_text())
                self.assertTrue(state[path][key])
                if expected is not None:
                    self.assertEqual(expected, state[path][key])
                self.assertEqual("previous-canary",
                                 state["homelab/portal"]["session-previous-key"])
                for other_path, fields in initial.items():
                    for other_key, value in fields.items():
                        self.assertEqual(value, state[other_path][other_key])
                events = [json.loads(line) for line in events_path.read_text().splitlines()]
                self.assertEqual([{"operation": operation, "path": path, "keys": [key]}], events)
                second = subprocess.run(["/bin/sh", str(script)], env=env, text=True,
                                        capture_output=True, check=False)
                self.assertEqual(0, second.returncode, second.stderr)
                self.assertEqual(state, json.loads(state_path.read_text()))
                self.assertEqual(events, [json.loads(line) for line in events_path.read_text().splitlines()])
                for canary in ("portal-canary", "keycloak-canary", "session-canary",
                               "previous-canary"):
                    self.assertNotIn(canary, first.stdout + first.stderr + second.stdout + second.stderr)

    def test_vault_transient_reads_abort_without_writes(self):
        source = (ROOT / "platform/vault/configure-vault.sh").read_text(encoding="utf-8")
        seed = source.split('echo "==> seeding homelab/keycloak-portal and homelab/portal"', 1)[1]
        seed = seed.split('echo "==> policy vso-keycloak-read"', 1)[0]
        initial = {
            "homelab/keycloak-portal": {"clientSecret": "keycloak-canary"},
            "homelab/portal": {"oidc-client-secret": "portal-canary",
                               "session-current-key": "current-canary",
                               "session-previous-key": "previous-canary"},
        }
        for failure in ("homelab/keycloak-portal:clientSecret",
                        "homelab/portal:-",
                        "homelab/portal:oidc-client-secret",
                        "homelab/portal:session-current-key"):
            with self.subTest(failure=failure), tempfile.TemporaryDirectory() as directory:
                temp = Path(directory)
                vault = temp / "vault"
                vault.write_text(FAKE_VAULT, encoding="utf-8")
                vault.chmod(0o700)
                script = temp / "seed.sh"
                script.write_text('set -eu\necho "==> seeding homelab/keycloak-portal and homelab/portal"' + seed,
                                  encoding="utf-8")
                state_path = temp / "vault.json"
                state_path.write_text(json.dumps(initial), encoding="utf-8")
                events_path = temp / "vault-events.jsonl"
                env = dict(os.environ, PATH=f"{temp}:{os.environ['PATH']}",
                           FAKE_VAULT_STATE=str(state_path), FAKE_VAULT_EVENTS=str(events_path),
                           FAKE_VAULT_FAIL_MATCH=failure)
                run = subprocess.run(["/bin/sh", str(script)], env=env, text=True,
                                     capture_output=True, check=False)
                self.assertNotEqual(0, run.returncode, failure)
                self.assertEqual(initial, json.loads(state_path.read_text()), failure)
                self.assertFalse(events_path.exists(), failure)
                for canary in ("keycloak-canary", "portal-canary", "current-canary", "previous-canary"):
                    self.assertNotIn(canary, run.stdout + run.stderr)

    def test_network_policies_grant_only_selected_peers_and_ports(self):
        path = CONFIG / "networkpolicy.yaml"
        deny = resource(path, "NetworkPolicy", "default-deny-ingress")
        self.assertEqual({}, deny["spec"]["podSelector"])
        self.assertEqual(["Ingress"], deny["spec"]["policyTypes"])
        policies = [doc for doc in resources(path) if doc and doc["kind"] == "NetworkPolicy"
                    and doc["metadata"]["name"] != "default-deny-ingress"]
        grants = []
        for policy in policies:
            self.assertEqual({"matchLabels": {"app": "keycloak"}}, policy["spec"]["podSelector"])
            for ingress in policy["spec"].get("ingress", []):
                self.assertEqual(1, len(ingress["ports"]))
                self.assertIn(ingress["ports"][0]["port"], (8080, 9000))
                for peer in ingress["from"]:
                    self.assertTrue(peer.get("podSelector", {}).get("matchLabels"))
                    grants.append((peer.get("namespaceSelector", {}).get("matchLabels", {}).get(
                        "kubernetes.io/metadata.name", "keycloak"), ingress["ports"][0]["port"]))
        self.assertIn(("tailscale", 8080), grants)
        self.assertIn(("monitoring", 9000), grants)
        self.assertIn(("monitoring", 8080), grants)
        self.assertIn(("keycloak", 8080), grants)
        self.assertIn(("portal", 8080), grants)
        self.assertIn(("nextcloud", 8080), grants)
        self.assertEqual(6, len(grants))


if __name__ == "__main__":
    unittest.main()
