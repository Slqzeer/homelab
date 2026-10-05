"""The metric allowlist is applied at scrape time on every ServiceMonitor.

One definition -- the &metricAllowlist anchor in
observability/monitoring/values.yaml -- and every scrape and the remote_write
must keep exactly it. A ServiceMonitor that scrapes without the keep puts its
whole cardinality back into the Prometheus agent's memory; one that carries
a stale copy silently drops a metric the remote_write would ship.
"""

from pathlib import Path
import subprocess
import sys
import unittest

import yaml

ROOT = Path(__file__).resolve().parents[1]
VALUES = yaml.safe_load((ROOT / "observability/monitoring/values.yaml").read_text(encoding="utf-8"))
ALLOWLIST = VALUES["kubelet"]["serviceMonitor"]["metricRelabelings"][0]["regex"]

# Every chart-rendered ServiceMonitor endpoint this cluster scrapes, by values
# path. kubeApiServer is absent on purpose: it is not scraped at all.
CHART_PATHS = (
    ("kubelet", "serviceMonitor", "metricRelabelings"),
    ("kubelet", "serviceMonitor", "cAdvisorMetricRelabelings"),
    ("kubelet", "serviceMonitor", "probesMetricRelabelings"),
    ("coreDns", "serviceMonitor", "metricRelabelings"),
    ("prometheusOperator", "serviceMonitor", "metricRelabelings"),
    ("prometheus", "serviceMonitor", "metricRelabelings"),
    ("kube-state-metrics", "prometheus", "monitor", "http", "metricRelabelings"),
    ("prometheus-node-exporter", "prometheus", "monitor", "metricRelabelings"),
)


def is_allowlist_keep(entry):
    return (
        entry.get("action") == "keep"
        and entry.get("sourceLabels") == ["__name__"]
        and entry.get("regex") == ALLOWLIST
    )


class MetricAllowlistTests(unittest.TestCase):
    def test_chart_service_monitors_keep_the_allowlist_first(self):
        for path in CHART_PATHS:
            with self.subTest(path=".".join(path)):
                node = VALUES
                for key in path:
                    node = node[key]
                self.assertTrue(is_allowlist_keep(node[0]))

    def test_api_server_is_not_scraped(self):
        self.assertIs(False, VALUES["kubeApiServer"]["enabled"])

    def test_remote_write_keeps_the_same_allowlist(self):
        remote_write = VALUES["prometheus"]["prometheusSpec"]["remoteWrite"]
        self.assertEqual(1, len(remote_write))
        self.assertTrue(is_allowlist_keep(remote_write[0]["writeRelabelConfigs"][0]))

    def test_every_target_endpoint_keeps_the_allowlist_first(self):
        endpoints = 0
        for path in sorted((ROOT / "observability/monitoring/targets").glob("*.yaml")):
            for doc in yaml.safe_load_all(path.read_text(encoding="utf-8")):
                if not doc or doc.get("kind") != "ServiceMonitor":
                    continue
                for endpoint in doc["spec"]["endpoints"]:
                    endpoints += 1
                    with self.subTest(file=path.name, endpoint=endpoint.get("port")):
                        relabelings = endpoint.get("metricRelabelings") or [{}]
                        self.assertTrue(
                            is_allowlist_keep(relabelings[0]),
                            "run: python3 scripts/sync_metric_allowlist.py",
                        )
        self.assertGreater(endpoints, 0)

    def test_sync_script_reports_nothing_stale(self):
        run = subprocess.run(
            [sys.executable, str(ROOT / "scripts/sync_metric_allowlist.py"), "--check"],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(0, run.returncode, run.stdout + run.stderr)


if __name__ == "__main__":
    unittest.main()
