"""Validate a personal application's Argo, rendered, and Ingress manifests."""

from pathlib import Path
import re
import subprocess

import yaml


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
IMAGE = re.compile(r"^[^\s@]+:(?:v)?\d+\.\d+\.\d+(?:[-+][A-Za-z0-9_.-]+)?@sha256:([0-9a-f]{64})$")
RELEASE = re.compile(r"^(?:v)?\d+\.\d+\.\d+(?:[-+][A-Za-z0-9_.-]+)?$")
COMMIT = re.compile(r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$")
POD_SECURITY = ("enforce", "audit", "warn")
UNSAFE_PORT_NAMES = {"operations", "metrics", "health", "admin"}


def _map(value):
    return value if isinstance(value, dict) else {}


def _checked_in(path: Path) -> bool:
    try:
        result = subprocess.run(
            ["git", "-C", str(REPOSITORY_ROOT), "ls-files", "--error-unmatch", "--", str(path.relative_to(REPOSITORY_ROOT))],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
    except OSError:
        return False
    return result.returncode == 0


def _documents(path: Path, errors: list[str]) -> list[dict]:
    try:
        with path.open(encoding="utf-8") as stream:
            documents = list(yaml.safe_load_all(stream))
    except (OSError, UnicodeError, yaml.YAMLError) as exc:
        errors.append(f"cannot parse {path}: {type(exc).__name__}")
        return []
    if any(not isinstance(document, dict) for document in documents):
        errors.append(f"{path} contains a non-mapping YAML document")
    return [document for document in documents if isinstance(document, dict)]


def _image_is_immutable(image: object) -> bool:
    if not isinstance(image, str):
        return False
    match = IMAGE.fullmatch(image)
    return bool(match and set(match.group(1)) != {"0"})


def _pod_specs(document: dict) -> list[dict]:
    kind = document.get("kind")
    spec = _map(document.get("spec"))
    if kind in {"Deployment", "StatefulSet", "DaemonSet", "ReplicaSet", "Job"}:
        return [_map(_map(spec.get("template")).get("spec"))]
    if kind == "CronJob":
        job = _map(_map(spec.get("jobTemplate")).get("spec"))
        return [_map(_map(job.get("template")).get("spec"))]
    if kind == "Pod":
        return [spec]
    return []


def _default_denies(documents: list[dict], namespace: str) -> set[str]:
    denied = set()
    for document in documents:
        if document.get("kind") != "NetworkPolicy":
            continue
        if _map(document.get("metadata")).get("namespace") != namespace:
            continue
        spec = _map(document.get("spec"))
        if spec.get("podSelector") != {}:
            continue
        for direction, rule in (("Ingress", "ingress"), ("Egress", "egress")):
            if direction in (spec.get("policyTypes") or []) and not spec.get(rule):
                denied.add(direction)
    return denied


def _unrestricted_peer(peer: object) -> bool:
    peer = _map(peer)
    if not peer:
        return True
    if _map(peer.get("ipBlock")).get("cidr") in {"0.0.0.0/0", "::/0"}:
        return True
    selectors = [_map(peer[name]) for name in ("namespaceSelector", "podSelector") if name in peer]
    return bool(selectors) and all(
        not selector.get("matchLabels") and not selector.get("matchExpressions")
        for selector in selectors
    )


def _unrestricted_port(port: object) -> bool:
    port = _map(port)
    return port.get("port") is None or (port.get("port") == 1 and port.get("endPort") == 65535)


def _has_unrestricted_policy(documents: list[dict], namespace: str) -> bool:
    for document in documents:
        if document.get("kind") != "NetworkPolicy" or _map(document.get("metadata")).get("namespace") != namespace:
            continue
        spec = _map(document.get("spec"))
        for direction, peer_field in (("ingress", "from"), ("egress", "to")):
            for rule in spec.get(direction) or []:
                rule = _map(rule)
                ports = rule.get("ports") or []
                peers = rule.get(peer_field) or []
                if (not ports or any(_unrestricted_port(port) for port in ports)
                        or not peers or any(_unrestricted_peer(peer) for peer in peers)):
                    return True
    return False


def _ingress_backends(ingress: dict) -> list[dict]:
    spec = _map(ingress.get("spec"))
    backends = [_map(spec.get("defaultBackend"))]
    for rule in spec.get("rules") or []:
        for path in _map(_map(rule).get("http")).get("paths") or []:
            backends.append(_map(_map(path).get("backend")))
    return [_map(backend.get("service")) for backend in backends if backend]


def _exposes_operations_port(backend: dict, namespace: str, documents: list[dict]) -> bool:
    reference = _map(backend.get("port"))
    name, number = reference.get("name"), reference.get("number")
    if name in UNSAFE_PORT_NAMES or number == 9000:
        return True
    for document in documents:
        if document.get("kind") != "Service":
            continue
        metadata = _map(document.get("metadata"))
        if metadata.get("namespace") != namespace or metadata.get("name") != backend.get("name"):
            continue
        for declared in _map(document.get("spec")).get("ports") or []:
            declared = _map(declared)
            if (name and declared.get("name") == name) or (number is not None and declared.get("port") == number):
                return (
                    declared.get("name") in UNSAFE_PORT_NAMES
                    or declared.get("targetPort") in UNSAFE_PORT_NAMES
                    or declared.get("port") == 9000
                    or declared.get("targetPort") == 9000
                )
    return False


def validate_application(
    application_path: Path, rendered_paths: list[Path], ingress_path: Path
) -> list[str]:
    """Return stable, human-readable contract violations; an empty list passes."""
    errors: list[str] = []
    applications = _documents(application_path, errors)
    rendered = [doc for path in rendered_paths for doc in _documents(path, errors)]
    ingresses = _documents(ingress_path, errors)
    if len(applications) != 1 or applications[0].get("kind") != "Application":
        errors.append("application input must contain one Argo Application")
        return sorted(set(errors))

    application = applications[0]
    annotations = _map(_map(application.get("metadata")).get("annotations"))
    required = {
        "homelab.io/onboarding-contract": "v1",
        "homelab.io/owner": "personal-applications",
    }
    for key, expected in required.items():
        if annotations.get(key) != expected:
            errors.append(f"application requires {key}: {expected}")

    wave = annotations.get("argocd.argoproj.io/sync-wave")
    if not isinstance(wave, (str, int)) or not re.fullmatch(r"\d+", str(wave)):
        errors.append("application requires a numeric sync wave")

    state = annotations.get("homelab.io/state")
    if not isinstance(state, str) or state not in {"stateless", "durable"}:
        errors.append("application requires homelab.io/state: stateless or durable")
    if state == "durable":
        runbook = annotations.get("backup.homelab.io/restore-runbook")
        if not isinstance(runbook, str) or not runbook.strip():
            errors.append("durable state requires backup.homelab.io/restore-runbook")
        else:
            path = Path(runbook)
            candidate = (REPOSITORY_ROOT / path).resolve()
            if (path.is_absolute() or not candidate.is_relative_to(REPOSITORY_ROOT)
                    or not candidate.is_file() or not _checked_in(candidate)):
                errors.append("restore runbook must be a checked-in repository path")
    elif any(
        doc.get("kind") == "PersistentVolumeClaim"
        or (doc.get("kind") == "StatefulSet" and _map(doc.get("spec")).get("volumeClaimTemplates"))
        for doc in rendered
    ):
        errors.append("durable state requires homelab.io/state: durable")

    spec = _map(application.get("spec"))
    namespace = _map(spec.get("destination")).get("namespace")
    if not isinstance(namespace, str) or not namespace:
        errors.append("application requires a destination namespace")
        namespace = ""
    sources = spec.get("sources") or [spec.get("source")]
    for source in sources:
        source = _map(source)
        revision = source.get("targetRevision")
        repo = source.get("repoURL")
        if not isinstance(repo, str) or not repo or not isinstance(revision, str) or not revision:
            errors.append("application requires a source with an immutable revision")
            continue
        immutable = isinstance(revision, str) and (
            RELEASE.fullmatch(revision)
            or (COMMIT.fullmatch(revision) and set(revision) != {"0"})
        )
        if repo and "homelab.git" not in repo and not immutable:
            errors.append("external source must use an immutable release revision")

    matching_namespaces = [
        doc for doc in rendered if doc.get("kind") == "Namespace" and _map(doc.get("metadata")).get("name") == namespace
    ]
    if not matching_namespaces:
        errors.append("rendered inputs must include the application namespace")
    else:
        labels = _map(_map(matching_namespaces[0].get("metadata")).get("labels"))
        if any(
            labels.get(f"pod-security.kubernetes.io/{mode}") != "restricted"
            or labels.get(f"pod-security.kubernetes.io/{mode}-version") != "v1.36"
            for mode in POD_SECURITY
        ):
            errors.append("namespace must have restricted Pod Security labels")

    image_count = 0
    for document in rendered:
        if document.get("kind") == "Secret":
            errors.append("rendered application must not contain a Secret manifest")
        for pod_spec in _pod_specs(document):
            for group in ("containers", "initContainers", "ephemeralContainers"):
                for container in pod_spec.get(group) or []:
                    image_count += 1
                    if not _image_is_immutable(_map(container).get("image")):
                        errors.append("deployment image must use an immutable sha256 digest")
        if document.get("kind") == "VaultStaticSecret":
            destination = _map(_map(document.get("spec")).get("destination"))
            if _map(destination.get("transformation")).get("excludeRaw") is not True:
                errors.append("VaultStaticSecret must set destination.transformation.excludeRaw: true")
    if image_count == 0:
        errors.append("rendered application requires a workload image")

    if _default_denies(rendered, namespace) != {"Ingress", "Egress"}:
        errors.append("namespace requires default-deny ingress and egress NetworkPolicy")
    if _has_unrestricted_policy(rendered, namespace):
        errors.append("network policy must not allow all ingress or egress")

    matching_ingresses = [
        doc for doc in ingresses if doc.get("kind") == "Ingress" and _map(doc.get("metadata")).get("namespace") == namespace
    ]
    if not matching_ingresses:
        errors.append("centralized ingress input must include an application Ingress")
    for ingress in matching_ingresses:
        ingress_annotations = _map(_map(ingress.get("metadata")).get("annotations"))
        if _map(ingress.get("spec")).get("ingressClassName") != "tailscale":
            errors.append("ingress must use the tailscale class")
        if ingress_annotations.get("tailscale.com/proxy-group") != "ingress":
            errors.append("ingress must use tailscale.com/proxy-group: ingress")
        for backend in _ingress_backends(ingress):
            if _exposes_operations_port(backend, namespace, rendered):
                errors.append("ingress must not expose an operations port")
        if ingress_annotations.get("portal.homelab.io/enabled") == "true":
            access = ingress_annotations.get("portal.homelab.io/access")
            if access not in {"public", "authenticated", "groups", "admin"}:
                errors.append("published ingress requires portal.homelab.io/access")
            if access == "groups" and not str(ingress_annotations.get("portal.homelab.io/groups", "")).strip():
                errors.append("group-restricted ingress requires portal.homelab.io/groups")
            if access != "groups" and ingress_annotations.get("portal.homelab.io/groups"):
                errors.append("non-group ingress must not set portal.homelab.io/groups")
            for field in ("name", "description", "category", "icon", "order"):
                if not str(ingress_annotations.get(f"portal.homelab.io/{field}", "")).strip():
                    errors.append(f"published ingress requires portal.homelab.io/{field}")

    return sorted(set(errors))
