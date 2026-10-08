"""Executable contract for the personal OIDC client registration hook."""

import json
import os
from pathlib import Path
import re
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
    allowed = os.environ.get("FAKE_ALLOWLIST", "homelab-portal,penpot").split(",")
    print(json.dumps([{"id": c["id"], "clientId": c["clientId"]}
                      for c in state if c["clientId"] in allowed]))
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
    body["id"] = "created-" + body["clientId"]
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
if os.environ.get("FAKE_VAULT_ARGV"):
    # Every invocation's argv, so a test can prove no secret was an argument.
    with open(os.environ["FAKE_VAULT_ARGV"], "a") as argv_log:
        argv_log.write(json.dumps(args) + "\n")
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
        if os.environ.get("FAKE_VAULT_DRAIN") == f"{path}:{field}":
            # Models another writer emptying the field right after this read,
            # so the next read of it races and finds nothing.
            state[path][field] = ""
            state_path.write_text(json.dumps(state))
    else:
        print("present")
elif args[:2] in (["kv", "put"], ["kv", "patch"]):
    path = args[2]
    body = {}
    for item in args[3:]:
        key, value = item.split("=", 1)
        if not value.startswith("@"):
            # A real `vault kv put` accepts a literal as well as key=@file. The
            # penpot block seeds postgres-username=penpot, a constant, so a test
            # that executes that block asks for literals. Default stays OFF, so
            # the tests that exist to catch a secret interpolated into argv
            # still see this exit 3.
            if not os.environ.get("FAKE_VAULT_ALLOW_LITERAL"):
                sys.exit(3)
            body[key] = value
            continue
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

# The shared Redis's one requirepass, at homelab/redis. Penpot gets it from VSO
# (a VaultStaticSecret template under the vso-penpot role), never from the
# penpot seed block, so the seed tests put this canary in Vault and prove the
# block neither reads, copies, prints nor passes it.
REDIS_PASSWORD = "RedisCanary7Qw3Zx9Lm2Np4Vb6Tk8Hd"
REDIS_PATH = {"username": "default", "password": REDIS_PASSWORD}


class ClientRegistrationTests(unittest.TestCase):
    def script(self):
        manifest = CONFIG / "client-registration.yaml"
        config_map = resource(manifest, "ConfigMap", "keycloak-client-registration")
        return config_map["data"]["reconcile.sh"]

    def run_hook(self, state, *, fail_list=False, leak_on_create=False, scope_noop=False,
                 penpot_secret="penpot-canary-secret"):
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
                       PORTAL_CLIENT_SECRET="portal-canary-secret",
                       PENPOT_CLIENT_SECRET="penpot-canary-secret")
            # None models the optional secretKeyRef whose Secret does not
            # exist yet: the kubelet leaves the variable unset entirely.
            if penpot_secret is None:
                del env["PENPOT_CLIENT_SECRET"]
            else:
                env["PENPOT_CLIENT_SECRET"] = penpot_secret
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
        # Portal, then Penpot: one create plus one `groups` scope add each.
        # The second run must add nothing, so four writes cover both runs.
        self.assertEqual(
            ["create", "update", "create", "update"],
            [event["args"][0] for event in writes],
        )
        self.assertEqual(
            "clients/created-homelab-portal/default-client-scopes/groups-id",
            writes[1]["args"][1],
        )
        self.assertEqual(
            "clients/created-penpot/default-client-scopes/groups-id",
            writes[3]["args"][1],
        )
        for event in events:
            self.assertNotIn("portal-canary-secret", " ".join(event["args"]))
            self.assertNotIn("admin-canary-secret", " ".join(event["args"]))
        self.assertNotIn("portal-canary-secret", jq_args)
        self.assertNotIn("admin-canary-secret", jq_args)
        self.assertNotIn("portal-canary-secret", "".join(run.stdout + run.stderr for run in runs))

    def test_penpot_client_is_allowlisted_with_its_own_redirect(self):
        runs, state, events, _ = self.run_hook([])
        self.assertEqual([0, 0], [run.returncode for run in runs], [run.stderr for run in runs])
        ids = sorted(client["clientId"] for client in state)
        self.assertEqual(["homelab-portal", "penpot"], ids)
        penpot = next(client for client in state if client["clientId"] == "penpot")
        self.assertEqual("openid-connect", penpot["protocol"])
        self.assertFalse(penpot["publicClient"])
        self.assertEqual("client-secret", penpot["clientAuthenticatorType"])
        self.assertTrue(penpot["standardFlowEnabled"])
        self.assertFalse(penpot["implicitFlowEnabled"])
        self.assertEqual(
            ["https://penpot.taildf6cd4.ts.net/api/oauth/redirect"],
            penpot["redirectUris"],
        )
        self.assertEqual(
            ["https://penpot.taildf6cd4.ts.net"], penpot["webOrigins"],
        )
        self.assertEqual(
            # Penpot does expose POST /api/logout, but there is no
            # logout URL a browser can be redirected to, so the allowlist entry
            # passes "/" as the logout path and post-logout lands on the
            # application root: origin + "/" = this exact string.
            "https://penpot.taildf6cd4.ts.net/",
            penpot["attributes"]["post.logout.redirect.uris"],
        )
        self.assertEqual("penpot-canary-secret", penpot["secret"])
        # The fake seeds ["profile", "email"] on create and the hook then adds
        # the third required default, so the converged set has three entries.
        self.assertEqual(["profile", "email", "groups"], penpot["_default_scopes"])

    def test_penpot_secret_never_reaches_argv_or_output(self):
        runs, state, events, jq_args = self.run_hook([])
        self.assertEqual([0, 0], [run.returncode for run in runs])
        for event in events:
            self.assertNotIn("penpot-canary-secret", " ".join(event["args"]))
        self.assertNotIn("penpot-canary-secret", jq_args)
        self.assertNotIn(
            "penpot-canary-secret",
            "".join(run.stdout + run.stderr for run in runs),
        )

    def test_absent_penpot_secret_skips_penpot_and_still_reconciles_portal(self):
        # The penpot Application (wave 25) projects keycloak-penpot-client
        # after this hook can first run. Until then the hook must not wedge
        # the portal: it reconciles the portal, skips penpot with a warning
        # and succeeds. "" covers a Secret present with an empty key.
        for penpot_secret in (None, ""):
            with self.subTest(penpot_secret=penpot_secret):
                runs, state, events, _ = self.run_hook([], penpot_secret=penpot_secret)
                self.assertEqual([0, 0], [run.returncode for run in runs],
                                 [run.stderr for run in runs])
                self.assertEqual(["homelab-portal"],
                                 [client["clientId"] for client in state])
                self.assertIn("groups", state[0]["_default_scopes"])
                self.assertFalse(any("penpot" in " ".join(event["args"])
                                     for event in events))
                for run in runs:
                    self.assertIn("WARNING: penpot client skipped", run.stderr)
                    self.assertNotIn("portal-canary-secret", run.stdout + run.stderr)
                    self.assertNotIn("admin-canary-secret", run.stdout + run.stderr)

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
        # Four for the drifted portal: one representation update plus three
        # scope adds, because the fixture has no `_default_scopes` at all.
        # Two more for Penpot, which is created and then gains `groups`.
        self.assertEqual(["update", "update", "update", "update", "create", "update"],
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
        self.assertEqual(3, len(writes), writes)
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
        self.assertEqual("keycloak-penpot-client", env["PENPOT_CLIENT_SECRET"]["valueFrom"]["secretKeyRef"]["name"])
        # Optional: the Secret belongs to the later penpot Application, and a
        # required reference would hold the pod in CreateContainerConfigError.
        self.assertIs(True, env["PENPOT_CLIENT_SECRET"]["valueFrom"]["secretKeyRef"]["optional"])
        # The admin and portal references stay required.
        for name in ("KC_ADMIN_USERNAME", "KC_CLI_PASSWORD", "PORTAL_CLIENT_SECRET"):
            self.assertNotIn("optional", env[name]["valueFrom"]["secretKeyRef"], name)
        script = self.script()
        self.assertIn("/tools/jq", script)
        self.assertIn("-f -", script)
        self.assertNotIn("--password", script)
        self.assertNotIn("get realms", script)
        self.assertNotIn("update realms", script)
        self.assertNotIn("delete clients", script)
        # The secret must never be a command-line argument of any kind. The
        # allowlist entry names its env var instead, and jq reads the value
        # out of its own environment.
        self.assertNotIn("--arg secret", script)
        self.assertNotIn("--arg secret_value", script)
        self.assertIn("secret: env[$env_name]", script)
        self.assertIn('secret_value=$("$JQ" -nr --arg env_name "$secret_var"', script)
        # jq is already a hard dependency of this script; a coreutils
        # environment reader is not, and cannot be assumed present in the
        # Keycloak image. Under set -e a missing binary aborts the hook.
        self.assertNotIn("printenv", script)
        # Both allowlist entries, each naming its own secret env var.
        self.assertIn("reconcile_client homelab-portal PORTAL_CLIENT_SECRET", script)
        self.assertIn("reconcile_client penpot PENPOT_CLIENT_SECRET", script)
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

    def roleSettings(self, role_block):
        """A vault role's `field=value` arguments as a dict.

        Exact by construction. The lines are split before they are compared,
        so no field can match as a prefix of a longer one -- which a substring
        check on the raw block allows, in both directions:
        `bound_service_account_names=penpot` sits inside
        `bound_service_account_names=penpot-db`, and `...=penpot` matches a
        role naming `penpot-db` or `penpot-x`. Comparing whole values closes
        both.

        A duplicate field is a dict entry too, and the last one wins -- which
        would let `bound_service_account_names=penpot-x` followed by
        `bound_service_account_names=penpot` pass. Hence the length check:
        the dict must hold exactly one line's worth of settings. Vault also
        takes only the last of a repeated CLI key, so a repeat is never what
        the author meant.
        """
        settings = {}
        lines = role_block.strip().splitlines()
        for line in lines:
            field, _, value = line.strip().partition("=")
            settings[field] = value.rstrip("\\ ").strip()
        self.assertEqual(len(lines), len(settings),
                         f"a field is repeated: {sorted(settings)}")
        return settings

    def test_vault_penpot_paths_and_roles_are_narrow(self):
        script = (ROOT / "platform/vault/configure-vault.sh").read_text(encoding="utf-8")
        for path in ("homelab/penpot", "homelab/penpot/data", "homelab/penpot-client"):
            self.assertIn(f"vault_optional_get {path}", script)
            # Anchored, or the first case is satisfied by `... penpot/data`.
            self.assertRegex(script, rf"(?m)^ *vault kv put {re.escape(path)}( |\n)")
        for key in ("postgres-password", "api-secret-key", "oidc-client-secret"):
            # The `@` is load-bearing: it is what proves the value is read from
            # a FILE. Without it, a generated secret interpolated straight into
            # the command line would satisfy the assertion while being visible
            # in `ps`.
            self.assertIn(f"{key}=@", script)
        # The one literal: a constant, and the only value in this block that is
        # not read from a file.
        self.assertIn("postgres-username=penpot", script)
        # The MCP key is issued by Penpot and cannot be seeded here.
        self.assertNotIn("mcp-key=@", script)
        # Nor is a Redis URI: VSO renders it from homelab/redis, so no copy of
        # the shared Redis password is ever kept at homelab/penpot.
        seed_block = script.split('echo "==> seeding homelab/penpot,', 1)[1]
        seed_block = seed_block.split('echo "==> policy vso-penpot-read"', 1)[0]
        self.assertNotIn("redis-uri=", seed_block)
        self.assertNotIn("homelab/redis password", seed_block)
        self.assertIn('path "homelab/data/penpot"', script)
        self.assertIn('path "homelab/data/penpot/data"', script)

        # homelab/penpot-client carries exactly one key, read from a file, and
        # exactly one write creates it. `clientSecret=` alone would pass on the
        # keycloak-portal block's own use of the same field name.
        client_writes = [line.strip() for line in script.splitlines()
                         if line.strip().startswith("vault kv put homelab/penpot-client")]
        self.assertEqual(1, len(client_writes), client_writes)
        # Anchored past the path, so a longer name cannot satisfy it.
        self.assertRegex(client_writes[0],
                         r"^vault kv put homelab/penpot-client clientSecret=@")

        # The keycloak namespace reads the Penpot client secret through its own
        # vso-keycloak-read policy, so that policy must grant the dedicated path.
        # It must NOT be widened to homelab/data/penpot: Keycloak needs one
        # credential from Penpot, not its database password, Redis URI or API
        # secret key. The closing quote is what makes each of these exact --
        # `homelab/data/penpot` is a prefix of `homelab/data/penpot-client`.
        keycloak_read = script.split("vault policy write vso-keycloak-read - <<'POLICY'\n", 1)[1]
        keycloak_read = keycloak_read.split("\nPOLICY", 1)[0]
        self.assertIn('path "homelab/data/penpot-client"', keycloak_read)
        self.assertNotIn('path "homelab/data/penpot"', keycloak_read)
        self.assertNotIn("*", keycloak_read)

        # Least privilege, whatever the new policy is called: exactly one policy in the
        # whole script may grant homelab/data/penpot, and it is vso-penpot-read's.
        # Without this, adding a second policy that grants the path and binding
        # it to vso-keycloak would satisfy every other assertion here.
        policies = {}
        for chunk in script.split("vault policy write ")[1:]:
            name, _, body = chunk.partition(" - <<'POLICY'\n")
            policies[name] = body.split("\nPOLICY", 1)[0]
        granting_penpot = [name for name, body in policies.items()
                           if 'path "homelab/data/penpot"' in body]
        self.assertEqual(["vso-penpot-read"], granting_penpot)

        # And the keycloak role must name only the policy that carries the
        # dedicated path -- one `token_policies`, not a list of them.
        self.assertEqual({
            "bound_service_account_names": "keycloak",
            "bound_service_account_namespaces": "keycloak",
            "audience": "vault",
            "token_policies": "vso-keycloak-read",
            "ttl": "1h",
        }, self.roleSettings(script.split("vault write auth/kubernetes/role/vso-keycloak \\\n", 1)[1]
                             .split("\n\n", 1)[0]))

        # One role per ServiceAccount. `vso-penpot` matches its own header only:
        # the db role is `vso-penpot-db`, which this search string cannot reach.
        app_role = script.split("vault write auth/kubernetes/role/vso-penpot \\\n", 1)[1]
        app_role = app_role.split("\n\n", 1)[0]
        self.assertEqual({
            "bound_service_account_names": "penpot",
            "bound_service_account_namespaces": "penpot",
            "audience": "vault",
            # vso-redis-read is the existing policy on homelab/data/redis, from
            # which Penpot's VaultStaticSecret renders the Redis URI.
            "token_policies": "vso-penpot-read,vso-redis-read",
            "ttl": "1h",
        }, self.roleSettings(app_role))
        self.assertNotIn("*", app_role)
        # Granted by adding the existing policy to the role, NOT by widening
        # vso-penpot-read, which stays the one-path policy it is.
        self.assertEqual(['homelab/data/penpot'],
                         re.findall(r'path "([^"]+)"', policies["vso-penpot-read"]))
        self.assertEqual(['homelab/data/redis'],
                         re.findall(r'path "([^"]+)"', policies["vso-redis-read"]))

        db_role = script.split("vault write auth/kubernetes/role/vso-penpot-db \\\n", 1)[1]
        db_role = db_role.split("\n\n", 1)[0]
        self.assertEqual({
            "bound_service_account_names": "penpot-db",
            "bound_service_account_namespaces": "databases",
            "audience": "vault",
            "token_policies": "vso-penpot-db-read",
            "ttl": "1h",
        }, self.roleSettings(db_role))
        self.assertNotIn("*", db_role)
        # The Job in `databases` never talks to Redis.
        self.assertNotIn("vso-redis-read", db_role)

    def run_penpot_seed(self, state, allow_literal=True, drain=None, tmpdir=None):
        """Execute the real penpot seed block against FAKE_VAULT, twice.

        The block calls vault_optional_get, which is defined earlier in the
        script, so the test has to supply it -- the same way the tests above
        supply only the block they exercise.

        homelab/redis is seeded earlier in the same script, so it is always
        present here, holding a canary. The block must never read it, write it,
        or write a redis-uri anywhere: Penpot's Redis URI is VSO's to render.
        homelab/redis is dropped from the returned state, which therefore holds
        only the paths this block owns. Every run is also checked for any
        secret value in Vault -- before or after -- reaching the terminal or a
        vault argv.
        """
        state = dict(state, **{"homelab/redis": REDIS_PATH})
        source = (ROOT / "platform/vault/configure-vault.sh").read_text(encoding="utf-8")
        header = ('echo "==> seeding homelab/penpot, homelab/penpot/data '
                  'and homelab/penpot-client"')
        seed = source.split(header, 1)[1]
        seed = seed.split('echo "==> policy vso-penpot-read"', 1)[0]
        helper = source.split("vault_optional_get() {", 1)[1].split("\n}\n", 1)[0]
        with tempfile.TemporaryDirectory() as directory:
            temp = Path(directory)
            vault = temp / "vault"
            vault.write_text(FAKE_VAULT, encoding="utf-8")
            vault.chmod(0o700)
            script = temp / "seed.sh"
            script.write_text(
                "set -eu\numask 077\n"
                'PORTAL_READ_ERROR_FILE=$(mktemp)\n'
                "vault_optional_get() {" + helper + "\n}\n" + header + seed,
                encoding="utf-8")
            state_path = temp / "vault.json"
            state_path.write_text(json.dumps(state), encoding="utf-8")
            events_path = temp / "vault-events.jsonl"
            # The fake only appends on a write, so start it: a run that writes
            # nothing is a result this test needs to be able to read.
            events_path.write_text("")
            argv_path = temp / "vault-argv.jsonl"
            argv_path.write_text("")
            env = dict(os.environ, PATH=f"{temp}:{os.environ['PATH']}",
                       FAKE_VAULT_STATE=str(state_path), FAKE_VAULT_EVENTS=str(events_path),
                       FAKE_VAULT_ARGV=str(argv_path))
            if allow_literal:
                env["FAKE_VAULT_ALLOW_LITERAL"] = "1"
            if drain:
                env["FAKE_VAULT_DRAIN"] = drain
            if tmpdir:
                # Every mktemp in the block lands here, so a test can see
                # what the EXIT trap failed to remove.
                env["TMPDIR"] = str(tmpdir)
            runs, per_run_events = [], []
            for _ in range(2):
                runs.append(subprocess.run(["/bin/sh", str(script)], env=env, text=True,
                                           capture_output=True, check=False))
                per_run_events.append(events_path.read_text())
            final = json.loads(state_path.read_text())
            argv = argv_path.read_text()
        self.assertEqual(state["homelab/redis"], final.pop("homelab/redis", None),
                         "the penpot block must never write homelab/redis")
        self.assertNotIn("homelab/redis", argv, "the penpot block must not read homelab/redis")
        for events in per_run_events:
            for line in events.splitlines():
                self.assertNotIn("redis-uri", json.loads(line)["keys"],
                                 "the penpot block must never write a redis-uri")
        output = "".join(run.stdout + run.stderr for run in runs)
        # postgres-username is the one non-secret: the constant `penpot`, which
        # is also part of every path name.
        secrets = {value for body in (*state.values(), *final.values())
                   for key, value in body.items() if value and key != "postgres-username"}
        for secret in secrets:
            self.assertNotIn(secret, argv, "a secret value became a vault argument")
            self.assertNotIn(secret, output, "a secret value reached the terminal")
        return runs, final, per_run_events

    def test_vault_penpot_seed_gives_each_shared_credential_exactly_one_value(self):
        """Every combination of which of the three paths already exists.

        Two credentials live at two paths each: the database password at
        homelab/penpot/postgres-password and homelab/penpot/data/password, and
        the OIDC client secret at homelab/penpot/oidc-client-secret and
        homelab/penpot-client/clientSecret. 2^3 = 8 starting states, which
        between them cover all four states of both pairs (neither, first only,
        second only, both). In every one of them each credential must end up
        identical in both of its paths and non-empty.

        The state that needs this most -- homelab/penpot/data present while
        homelab/penpot is absent -- is the one where a guard keyed only on the
        data path leaves the password unresolved, and the block then writes the
        zero-byte mktemp as the chart's database password.
        """
        def app_at(password, oidc):
            """homelab/penpot populated, with the two shared values it carries."""
            return {"postgres-username": "penpot", "postgres-password": password,
                    "api-secret-key": "app-api", "oidc-client-secret": oidc}

        data = {"homelab/penpot/data": {"password": "db-shared"}}
        client = {"homelab/penpot-client": {"clientSecret": "oidc-shared"}}
        cases = (
            # Starting state, expected password, expected client secret. None
            # means the script generated it, and only the two-copies-match and
            # non-empty properties are asserted. A fixture that gave one
            # credential two different values would be a state no run of this
            # script can produce, so the shared value is always "…-shared".
            ("neither present", {}, None, None),
            ("app only", {"homelab/penpot": app_at("db-shared", "oidc-shared")},
             "db-shared", "oidc-shared"),
            ("data only", dict(data), "db-shared", None),
            ("client only", dict(client), None, "oidc-shared"),
            ("app + data", dict(data, **{"homelab/penpot": app_at("db-shared", "oidc-shared")}),
             "db-shared", "oidc-shared"),
            ("app + client", dict(client, **{"homelab/penpot": app_at("db-shared", "oidc-shared")}),
             "db-shared", "oidc-shared"),
            ("data + client", {**data, **client}, "db-shared", "oidc-shared"),
            ("all three", {**data, **client,
                           "homelab/penpot": app_at("db-shared", "oidc-shared")},
             "db-shared", "oidc-shared"),
        )
        for label, seeded, password, oidc in cases:
            with self.subTest(starting=label):
                runs, state, per_run_events = self.run_penpot_seed(seeded)
                self.assertEqual(0, runs[0].returncode, runs[0].stderr)
                self.assertEqual(
                    {"homelab/penpot", "homelab/penpot/data", "homelab/penpot-client"},
                    set(state))

                written = state["homelab/penpot"]["postgres-password"]
                self.assertNotEqual("", written, "an empty password was seeded")
                self.assertEqual(written, state["homelab/penpot/data"]["password"],
                                 "the two copies of the database password differ")
                secret = state["homelab/penpot"]["oidc-client-secret"]
                self.assertNotEqual("", secret, "an empty client secret was seeded")
                self.assertEqual(secret, state["homelab/penpot-client"]["clientSecret"],
                                 "the two copies of the OIDC client secret differ")

                # Where the pair was only partly present, the surviving copy wins.
                if password is not None:
                    self.assertEqual(password, written)
                if oidc is not None:
                    self.assertEqual(oidc, secret)

                # Idempotent: a second run writes nothing and changes nothing.
                self.assertEqual(0, runs[1].returncode, runs[1].stderr)
                self.assertEqual(per_run_events[0], per_run_events[1])
                # No value ever reaches the terminal.
                output = runs[0].stdout + runs[0].stderr + runs[1].stdout + runs[1].stderr
                for canary in ("db-shared", "oidc-shared", "app-api"):
                    self.assertNotIn(canary, output)

    def test_vault_penpot_seed_leaves_no_temporary_file_behind(self):
        # The block's EXIT trap replaces the earlier ones, so it must name
        # every temporary file still live -- including the shared
        # PORTAL_READ_ERROR_FILE, which vault_optional_get recreates after the
        # portal block removed it, and which can hold a vault error message.
        with tempfile.TemporaryDirectory() as directory:
            runs, _, _ = self.run_penpot_seed({}, tmpdir=directory)
            self.assertEqual([0, 0], [run.returncode for run in runs],
                             [run.stderr for run in runs])
            self.assertEqual([], sorted(os.listdir(directory)))

    def test_vault_penpot_seed_repairs_a_present_path_missing_a_field(self):
        """A path that exists but lacks a field is repaired with kv patch.

        This is the same shape as tle_ensure_field and omni_ensure_field: a KV
        v2 `put` replaces every key at a path, so a path that already exists is
        only ever `patch`ed, one field at a time. The other keys must survive,
        which is what makes repairing better than refusing.
        """
        app = {"postgres-username": "penpot", "postgres-password": "shared-pw",
               "api-secret-key": "app-api", "oidc-client-secret": "shared-oidc"}
        client = {"homelab/penpot-client": {"clientSecret": "shared-oidc"}}
        data = {"homelab/penpot/data": {"password": "shared-pw"}}
        without_oidc = {k: v for k, v in app.items() if k != "oidc-client-secret"}
        without_api = {k: v for k, v in app.items() if k != "api-secret-key"}
        cases = (
            # label, starting state, the field that must be repaired
            ("app path missing oidc-client-secret",
             {"homelab/penpot": without_oidc, **client, **data},
             ("homelab/penpot", "oidc-client-secret", "shared-oidc")),
            ("app path missing postgres-username",
             {"homelab/penpot": {k: v for k, v in app.items()
                                 if k != "postgres-username"}, **client, **data},
             ("homelab/penpot", "postgres-username", "penpot")),
            ("data path missing password",
             {"homelab/penpot": app, **client, "homelab/penpot/data": {}},
             ("homelab/penpot/data", "password", "shared-pw")),
        )
        for label, seeded, (path, field, expected) in cases:
            with self.subTest(case=label):
                runs, state, per_run_events = self.run_penpot_seed(seeded)
                self.assertEqual(0, runs[0].returncode, runs[0].stderr)
                self.assertEqual(expected, state[path][field])
                self.assertIn("repaired", runs[0].stdout)
                # Every other key that was there before is still there: a `put`
                # would have replaced them all.
                for key, value in seeded.get(path, {}).items():
                    self.assertEqual(value, state[path][key], key)
                for other, body in seeded.items():
                    if other != path:
                        self.assertEqual(body, state[other], other)
                # Repaired, and then a second run writes nothing.
                self.assertEqual(0, runs[1].returncode, runs[1].stderr)
                self.assertEqual(per_run_events[0], per_run_events[1])
                for canary in ("shared-pw", "shared-oidc", "app-api"):
                    self.assertNotIn(canary, runs[0].stdout + runs[0].stderr)

        # A field with no sibling to copy from is generated, then patched.
        runs, state, _ = self.run_penpot_seed(
            {"homelab/penpot": without_api, **client, **data})
        self.assertEqual(0, runs[0].returncode, runs[0].stderr)
        self.assertRegex(state["homelab/penpot"]["api-secret-key"], r"^[0-9a-f]{64}$")
        for key, value in without_api.items():
            self.assertEqual(value, state["homelab/penpot"][key], key)

    def test_vault_penpot_seed_refuses_two_copies_that_differ(self):
        """Both copies usable but not equal must stop the run, silently fixed never.

        Reachable by hand, and the plan leads an operator straight into it: the
        rotation ceremony says to change "the" database password, and with the
        credential at two paths that instruction changes only one copy. The
        block must not paper over it by preferring either value, because either
        preference hides a split behind a login that still works.
        """
        app = {"postgres-username": "penpot", "postgres-password": "shared-pw",
               "api-secret-key": "app-api", "oidc-client-secret": "shared-oidc"}
        cases = (
            ("database password differs",
             {"homelab/penpot": dict(app, **{"postgres-password": "left-pw"}),
              "homelab/penpot/data": {"password": "right-pw"},
              "homelab/penpot-client": {"clientSecret": "shared-oidc"}},
             "homelab/penpot/postgres-password", "homelab/penpot/data/password",
             ("left-pw", "right-pw")),
            ("OIDC client secret differs",
             {"homelab/penpot": dict(app, **{"oidc-client-secret": "left-oidc"}),
              "homelab/penpot/data": {"password": "shared-pw"},
              "homelab/penpot-client": {"clientSecret": "right-oidc"}},
             "homelab/penpot/oidc-client-secret", "homelab/penpot-client/clientSecret",
             ("left-oidc", "right-oidc")),
        )
        for label, seeded, left, right, values in cases:
            with self.subTest(case=label):
                # The fixture must really hold both values at both paths, or
                # the no-leak assertions below check strings Vault never had.
                for where, value in zip((left, right), values):
                    path, field = where.rsplit("/", 1)
                    self.assertEqual(value, seeded[path][field], where)
                runs, state, _ = self.run_penpot_seed(seeded)
                self.assertNotEqual(0, runs[0].returncode, runs[0].stdout)
                # Both paths are named, and the values are not printed.
                self.assertIn(left, runs[0].stderr)
                self.assertIn(right, runs[0].stderr)
                output = runs[0].stdout + runs[0].stderr
                for value in values:
                    self.assertNotIn(value, output)
                # Nothing was written: not even the pair that agreed.
                self.assertEqual(seeded, state)

    def test_vault_penpot_seed_refuses_when_no_copy_holds_a_usable_value(self):
        """Nothing to copy from: stop rather than invent a credential.

        A KV v2 `put` accepts an empty string without complaint, so a password
        resolved to nothing would be stored silently and the next run would
        leave it there forever. Generating a replacement for a key an operator
        may have deliberately emptied is not this block's call to make, so it
        stops and says which field to seed by hand.
        """
        # Each case names the command the message must offer for each copy:
        # `kv put` only where the path does not exist yet, `kv patch` where it
        # does, and always `<path> <field>=@FILE` -- path and key as separate
        # arguments, which is the only form the CLI accepts.
        absent_app_db = "vault kv put homelab/penpot postgres-password=@FILE"
        blank_data = "vault kv patch homelab/penpot/data password=@FILE"
        cases = (
            ("data password is the empty string",
             {"homelab/penpot/data": {"password": ""}},
             (absent_app_db, blank_data)),
            ("data path exists without a password field",
             {"homelab/penpot/data": {}},
             (absent_app_db, blank_data)),
            ("client secret is the empty string",
             {"homelab/penpot-client": {"clientSecret": ""}},
             ("vault kv put homelab/penpot oidc-client-secret=@FILE",
              "vault kv patch homelab/penpot-client clientSecret=@FILE")),
            ("both copies blank",
             {"homelab/penpot": {"postgres-password": "", "oidc-client-secret": ""},
              "homelab/penpot/data": {"password": ""},
              "homelab/penpot-client": {"clientSecret": ""}},
             ("vault kv patch homelab/penpot postgres-password=@FILE", blank_data)),
        )
        for label, seeded, commands in cases:
            with self.subTest(case=label):
                runs, state, _ = self.run_penpot_seed(seeded)
                self.assertNotEqual(0, runs[0].returncode, runs[0].stdout)
                self.assertIn("usable value", runs[0].stderr)
                for command in commands:
                    self.assertIn(command, runs[0].stderr)
                # Never `<path>/<field>=@FILE`, which the CLI would reject.
                self.assertNotRegex(runs[0].stderr, r"kv (put|patch) [^ ]+/[^ /]+=@")
                # Nothing was written, so nothing was half-created.
                self.assertEqual(seeded, state)

    def test_vault_penpot_seed_reports_a_copy_that_vanishes_mid_run(self):
        """A copy probed as usable, then empty on the read that copies it.

        That is a race with another writer, not "neither copy is usable", and
        the message must say so rather than tell the operator to seed a value
        that was there a moment ago.
        """
        seeded = {"homelab/penpot/data": {"password": "db-shared"}}
        runs, state, per_run_events = self.run_penpot_seed(
            seeded, drain="homelab/penpot/data:password")
        self.assertNotEqual(0, runs[0].returncode, runs[0].stdout)
        self.assertIn("homelab/penpot/data/password held a usable value", runs[0].stderr)
        self.assertIn("changed or was removed", runs[0].stderr)
        self.assertNotIn("neither", runs[0].stderr)
        self.assertEqual("", per_run_events[0], "nothing may be written on a race")

    def test_vault_penpot_seed_leaves_the_redis_uri_to_vso(self):
        """The seed block never manages a Redis URI.

        The shared Redis has one requirepass, at homelab/redis. Penpot's
        VaultStaticSecret renders the URI from that path, so a copy kept at
        homelab/penpot could only fall out of step on rotation. The block must
        not write one on a cold start, and must leave a redis-uri written by an
        older run exactly where it is -- unused, but not this block's to delete.
        run_penpot_seed additionally proves homelab/redis is never read.
        """
        runs, state, _ = self.run_penpot_seed({})
        self.assertEqual(0, runs[0].returncode, runs[0].stderr)
        self.assertEqual({"postgres-username", "postgres-password", "api-secret-key",
                          "oidc-client-secret"}, set(state["homelab/penpot"]))

        legacy = {"postgres-username": "penpot", "postgres-password": "shared-pw",
                  "redis-uri": "redis://:LegacyPw1234567890@redis.databases.svc"
                               ".cluster.local:6379/3",
                  "api-secret-key": "app-api", "oidc-client-secret": "shared-oidc"}
        seeded = {"homelab/penpot": legacy,
                  "homelab/penpot/data": {"password": "shared-pw"},
                  "homelab/penpot-client": {"clientSecret": "shared-oidc"}}
        runs, state, per_run_events = self.run_penpot_seed(seeded)
        self.assertEqual(0, runs[0].returncode, runs[0].stderr)
        self.assertEqual("", per_run_events[0], "a converged state must write nothing")
        self.assertEqual(seeded, state)

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
        # Grafana, the one in-cluster consumer of 8080 from `monitoring`, moved
        # to Grafana Cloud; only the metrics scrape on 9000 remains.
        self.assertNotIn(("monitoring", 8080), grants)
        self.assertIn(("keycloak", 8080), grants)
        self.assertIn(("portal", 8080), grants)
        self.assertIn(("nextcloud", 8080), grants)
        self.assertIn(("penpot", 8080), grants)
        self.assertEqual(6, len(grants))
        penpot_peer = next(peer for policy in policies
                           for ingress in policy["spec"].get("ingress", [])
                           for peer in ingress["from"]
                           if peer.get("namespaceSelector", {}).get("matchLabels", {}).get(
                               "kubernetes.io/metadata.name") == "penpot")
        # Chart 1.11.3 labels the backend pod app.kubernetes.io/name:
        # penpot-backend. `penpot` would match no pod and deny silently, and
        # grants above cannot see that because they key on namespace only.
        self.assertEqual({"app.kubernetes.io/name": "penpot-backend"},
                         penpot_peer["podSelector"]["matchLabels"])


if __name__ == "__main__":
    unittest.main()
