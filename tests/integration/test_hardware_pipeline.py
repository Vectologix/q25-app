from dataclasses import asdict
from pathlib import Path
import socket
import tempfile
import unittest
from unittest.mock import patch

from q25.hardware.catalog import load_catalog
from q25.hardware.probe import LinuxProbe, LocalHardwareProbe, NvidiaProbe
from q25.hardware.selection import recommend
from tests.unit.test_probe import FakeFiles


class HardwarePipelineTests(unittest.TestCase):
    def test_local_probe_catalog_and_recommendation_without_network_or_report(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            with patch.object(socket, "socket", side_effect=AssertionError("réseau interdit")), \
                 patch.object(socket, "create_connection", side_effect=AssertionError("réseau interdit")):
                snapshot = LocalHardwareProbe().probe(root)
                catalog = load_catalog()
                result = recommend(snapshot, catalog)
            self.assertEqual(result.fallback, "sans_llm")
            self.assertEqual(len(result.decisions), 4)
            self.assertTrue(all(not item.eligible for item in result.decisions))
            self.assertFalse(result.plans)
            self.assertEqual(list(root.iterdir()), [])
            data = asdict(snapshot)
            self.assertFalse({"hostname", "username", "serial", "uuid"} & data.keys())
            self.assertNotIn(folder, repr(data))

    def test_deterministic_end_to_end_with_synthetic_files_and_no_process(self):
        linux = LinuxProbe(read_text=FakeFiles(), cpu_count=lambda: 8, affinity=lambda: {0, 1})
        nvidia = NvidiaProbe(runner=lambda args: "8192, 4096")
        with tempfile.TemporaryDirectory() as folder, \
             patch("subprocess.Popen", side_effect=AssertionError("processus interdit")):
            snapshot = LocalHardwareProbe(system=lambda: "Linux", architecture=lambda: "x86_64",
                                         linux=linux, nvidia=nvidia).probe(Path(folder))
            result = recommend(snapshot, load_catalog())
        self.assertTrue(snapshot.is_wsl)
        self.assertEqual(snapshot.cpu_logical.value, 2)
        self.assertEqual(result.fallback, "sans_llm")
