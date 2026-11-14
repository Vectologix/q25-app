from dataclasses import asdict
from pathlib import Path
import socket
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from q25.hardware.memory import MemoryPlanner, TaskRequest, TokenBudget
from q25.hardware.probe import LinuxProbe, LocalHardwareProbe, NvidiaProbe
from tests.helpers import GIB
from tests.unit.test_memory import contexts
from tests.unit.test_probe import FakeFiles


class MemoryPipelineTests(unittest.TestCase):
    def probe(self, files):
        return LocalHardwareProbe(system=lambda:"Linux",architecture=lambda:"x86_64",
                                  linux=LinuxProbe(read_text=files,cpu_count=lambda:8,affinity=lambda:{0,1}),
                                  nvidia=NvidiaProbe(runner=lambda args:"8192,4096"))

    def test_probe_to_task_budget_is_offline_and_creates_no_report(self):
        probe=self.probe(FakeFiles())
        request=TaskRequest(TokenBudget(output_tokens=512,system_tokens=256,
                                        question_tokens=128,history_tokens=128,source_tokens=2048))
        with tempfile.TemporaryDirectory() as folder:
            with self.assertNoLogs(), \
                 patch.object(socket,"socket",side_effect=AssertionError("réseau interdit")), \
                 patch.object(socket,"create_connection",side_effect=AssertionError("réseau interdit")), \
                 patch("subprocess.Popen",side_effect=AssertionError("processus interdit")), \
                 patch("q25.hardware.probe.shutil.disk_usage",
                       return_value=SimpleNamespace(total=100*GIB,free=100*GIB)):
                measures=probe.probe(Path(folder))
                before=asdict(measures)
                with patch.object(Path,"open",side_effect=AssertionError("aucune lecture/écriture du planificateur")):
                    result=MemoryPlanner().plan(measures,contexts(),request,allow_synthetic=True)
            self.assertEqual(result.selected_context_tokens,8192)
            self.assertEqual(result.tokens.total_tokens,3072)
            self.assertEqual(result.budgets.ram_bytes,8*GIB)
            self.assertTrue(measures.is_wsl)
            self.assertEqual(asdict(measures),before)
            self.assertEqual(list(Path(folder).iterdir()),[])
            self.assertNotIn(folder,repr(result))

    def test_one_planner_rechecks_new_snapshots_instead_of_caching_available_memory(self):
        files=FakeFiles();probe=self.probe(files);planner=MemoryPlanner()
        request=TaskRequest(TokenBudget(output_tokens=512,source_tokens=1000))
        with tempfile.TemporaryDirectory() as folder, \
             patch("subprocess.Popen",side_effect=AssertionError("processus interdit")), \
             patch("q25.hardware.probe.shutil.disk_usage",return_value=SimpleNamespace(total=100*GIB,free=100*GIB)):
            first=probe.probe(Path(folder))
            original=asdict(first)
            high=planner.plan(first,contexts(),request,allow_synthetic=True)
            files.files['/proc/meminfo']=files.files['/proc/meminfo'].replace('12582912','9437184')
            second=probe.probe(Path(folder))
            low=planner.plan(second,contexts(),request,allow_synthetic=True)
            self.assertEqual(high.selected_context_tokens,8192)
            self.assertEqual(low.selected_context_tokens,4096)
            self.assertEqual(asdict(first),original)
            self.assertTrue(high.requires_resource_recheck and low.requires_resource_recheck)
            self.assertEqual(list(Path(folder).iterdir()),[])
