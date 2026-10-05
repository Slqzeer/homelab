#!/usr/bin/env python3
"""Copy the metric allowlist into every ServiceMonitor endpoint in targets/.

The allowlist is defined once, as the YAML anchor &metricAllowlist in
observability/monitoring/values.yaml. Chart ServiceMonitors reuse it by
alias; the hand-written ServiceMonitors in observability/monitoring/targets/
are separate manifests, so each endpoint carries a verbatim copy as the
FIRST entry of its metricRelabelings. This script writes or refreshes those
copies; tests/test_metric_allowlist.py fails CI when one has drifted.

    python3 scripts/sync_metric_allowlist.py          # rewrite in place
    python3 scripts/sync_metric_allowlist.py --check  # exit 1 if stale

Edits are textual so that comments and layout survive: only the managed
block -- the marker comment and the keep entry right after it -- is ever
written.
"""

from pathlib import Path
import re
import sys

import yaml

ROOT = Path(__file__).resolve().parents[1]
VALUES = ROOT / "observability/monitoring/values.yaml"
TARGETS = ROOT / "observability/monitoring/targets"
BEGIN = "# BEGIN metric allowlist (scripts/sync_metric_allowlist.py)"
WIDTH = 66


def allowlist() -> str:
    values = yaml.safe_load(VALUES.read_text(encoding="utf-8"))
    return values["kubelet"]["serviceMonitor"]["metricRelabelings"][0]["regex"]


def block(regex: str, indent: int) -> str:
    """The managed metricRelabelings entry, as YAML at `indent` spaces."""
    parts, current = [], ""
    for token in regex.split("|"):
        piece = ("|" if current or parts else "") + token
        if current and len(current) + len(piece) > WIDTH:
            parts.append(current)
            current = piece
        else:
            current += piece
    parts.append(current)
    pad = " " * indent
    quoted = ('\\\n' + pad + "    ").join(parts)
    return (
        f"{pad}{BEGIN}\n"
        f"{pad}- action: keep\n"
        f"{pad}  sourceLabels: [__name__]\n"
        f'{pad}  regex: "{quoted}"\n'
    )


# The marker, the keep entry, and its quoted regex up to the closing quote.
MANAGED = re.compile(
    r"^(?P<pad>[ ]*)" + re.escape(BEGIN) + r'\n.*?^[ ]*regex: "[^"]*"\n',
    re.M | re.S,
)
# An endpoint list item, e.g. "    - port: metrics", and its continuation lines.
ENDPOINT = re.compile(r"^(?P<pad>[ ]*)- (?:port|targetPort): .*\n(?:(?P=pad)  .*\n)*", re.M)


def render(text: str, regex: str) -> str:
    def endpoint(match: re.Match) -> str:
        item = match.group(0)
        pad = len(match.group("pad")) + 2
        if BEGIN in item:
            return MANAGED.sub(lambda m: block(regex, len(m.group("pad"))), item)
        if re.search(rf"^{' ' * pad}metricRelabelings:\n", item, re.M):
            # Existing list: prepend the keep as its first entry.
            return re.sub(
                rf"^({' ' * pad}metricRelabelings:\n)",
                lambda m: m.group(1) + block(regex, pad + 2),
                item,
                count=1,
                flags=re.M,
            )
        return item + f"{' ' * pad}metricRelabelings:\n" + block(regex, pad + 2)

    return ENDPOINT.sub(endpoint, text)


def main() -> int:
    check = "--check" in sys.argv[1:]
    regex = allowlist()
    stale = []
    for path in sorted(TARGETS.glob("*.yaml")):
        text = path.read_text(encoding="utf-8")
        if "kind: ServiceMonitor" not in text:
            continue
        updated = render(text, regex)
        if updated != text:
            stale.append(path.relative_to(ROOT))
            if not check:
                path.write_text(updated, encoding="utf-8")
    for path in stale:
        print(f"{'stale' if check else 'updated'}: {path}")
    return 1 if check and stale else 0


if __name__ == "__main__":
    sys.exit(main())
