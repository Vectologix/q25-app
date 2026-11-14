import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from q25.hardware.commands import CommandError, bounded_output
from q25.hardware.probe import LinuxProbe, LocalHardwareProbe, NvidiaProbe
from tests.helpers import GIB


MEMINFO = """MemTotal:       16777216 kB
MemAvailable:    12582912 kB
SwapTotal:        8388608 kB
SwapFree:         4194304 kB
"""
CPUINFO = """processor: 0
physical id: 0
core id: 0

processor: 1
physical id: 0
core id: 0

"""


class FakeFiles:
    def __init__(self, extra=None):
        self.files = {"/proc/meminfo": MEMINFO, "/proc/cpuinfo": CPUINFO,
                      "/proc/sys/kernel/osrelease": "6.0-microsoft-WSL2",
                      "/proc/self/cgroup": "0::/\n",
                      "/sys/fs/cgroup/cgroup.controllers": "cpu memory",
                      "/sys/fs/cgroup/memory.max": "max",
                      "/sys/fs/cgroup/cpu.max": "max 100000"}
        self.files.update(extra or {})

    def __call__(self, path):
        if str(path) not in self.files:
            raise FileNotFoundError
        result = self.files[str(path)]
        if isinstance(result, Exception):
            raise result
        return result


class LinuxProbeTests(unittest.TestCase):
    def probe(self, extra=None):
        return LinuxProbe(read_text=FakeFiles(extra), cpu_count=lambda: 8,
                          affinity=lambda: {0, 1}).measure()

    def test_meminfo_units_and_cpu_affinity(self):
        value = self.probe()
        self.assertEqual(value.ram.total.value, 16 * GIB)
        self.assertEqual(value.ram.available.value, 12 * GIB)
        self.assertEqual(value.swap.available.value, 4 * GIB)
        self.assertEqual(value.cpu_logical.value, 2)
        self.assertEqual(value.cpu_physical.value, 1)
        self.assertTrue(value.is_wsl)

    def test_cgroup_limit_and_parent_headroom(self):
        value = self.probe({
            "/proc/self/cgroup": "0::/group/child",
            "/sys/fs/cgroup/group/child/memory.max": str(10 * GIB),
            "/sys/fs/cgroup/group/child/memory.current": str(GIB),
            "/sys/fs/cgroup/group/child/cpu.max": "max 100000",
            "/sys/fs/cgroup/group/memory.max": str(8 * GIB),
            "/sys/fs/cgroup/group/memory.current": str(6 * GIB),
            "/sys/fs/cgroup/group/cpu.max": "100000 100000",
        })
        self.assertEqual(value.ram.total.value, 8 * GIB)
        self.assertEqual(value.ram.available.value, 2 * GIB)
        self.assertEqual(value.cpu_logical.value, 1)

    def test_cgroup_usage_above_limit_has_no_headroom(self):
        value = self.probe({"/sys/fs/cgroup/memory.max": str(GIB),
                            "/sys/fs/cgroup/memory.current": str(2 * GIB)})
        self.assertEqual(value.ram.available.value, 0)

    def test_missing_or_invalid_meminfo_is_unknown(self):
        for raw in (PermissionError(), "MemTotal: -1 kB", "MemTotal: 1 MB", "garbage"):
            with self.subTest(raw=raw):
                value = self.probe({"/proc/meminfo": raw})
                self.assertIsNone(value.ram.available.value)
                self.assertTrue(value.ram.available.reason)

    def test_available_not_invented_from_memfree(self):
        value = self.probe({"/proc/meminfo": "MemTotal: 16 kB\nMemFree: 8 kB"})
        self.assertEqual(value.ram.total.value, 16 * 1024)
        self.assertIsNone(value.ram.available.value)

    def test_inconsistent_meminfo_fails_safely(self):
        value = self.probe({"/proc/meminfo": "MemTotal: 1 kB\nMemAvailable: 2 kB"})
        self.assertIsNone(value.ram.available.value)

    def test_unknown_cgroup_limits_do_not_overestimate_ram(self):
        for extra in ({"/sys/fs/cgroup/memory.max": PermissionError()},
                      {"/sys/fs/cgroup/memory.max": "invalid"},
                      {"/proc/self/cgroup": "0::/../../outside"},
                      {"/sys/fs/cgroup/memory.max": str(8 * GIB)}):
            with self.subTest(extra=extra):
                self.assertIsNone(self.probe(extra).ram.available.value)

    def test_cgroup_v1_is_explicitly_unknown(self):
        value = self.probe({"/proc/self/cgroup": "5:memory:/group"})
        self.assertIsNone(value.ram.available.value)

    def test_incomplete_cpu_topology_and_unknown_wsl(self):
        value = self.probe({"/proc/cpuinfo": "processor: 0", "/proc/sys/kernel/osrelease": PermissionError()})
        self.assertIsNone(value.cpu_physical.value)
        self.assertIsNone(value.is_wsl)


class NvidiaTests(unittest.TestCase):
    def test_csv_memory_only_no_device_identifiers(self):
        seen = []
        def run(args):
            seen.append(args)
            return "8192, 4096\n16384, 8192\n"
        gpus, reason = NvidiaProbe(runner=run).measure()
        self.assertIsNone(reason)
        self.assertEqual(gpus[1].memory.available.value, 8 * GIB)
        self.assertEqual(gpus[0].index, 0)
        self.assertEqual(seen[0][1], "--query-gpu=memory.total,memory.free")
        self.assertNotIn("uuid", " ".join(seen[0]))

    def test_unavailable_timeout_invalid_or_empty_remain_unknown(self):
        for value in (FileNotFoundError(), subprocess.TimeoutExpired("fixture", 1),
                      CommandError("timeout"), "", "8192, N/A", "1, 2", "8, 4, 1", "nan, 0"):
            def run(args):
                if isinstance(value, Exception):
                    raise value
                return value
            with self.subTest(value=value):
                gpus, reason = NvidiaProbe(runner=run).measure()
                self.assertIsNone(gpus)
                self.assertTrue(reason)


@unittest.skipUnless(os.name == "posix", "Capture bornée POSIX ; pas une validation Windows")
class BoundedCommandTests(unittest.TestCase):
    def test_success_and_no_shell(self):
        output = bounded_output((sys.executable, "-c", "print('synthetic')"), timeout=2)
        self.assertEqual(output.strip(), "synthetic")

    def test_timeout_output_limit_and_nonzero_exit(self):
        for code, options in (("import time; time.sleep(2)", {"timeout": 0.05}),
                              ("print('x'*10000)", {"max_bytes": 32}),
                              ("raise SystemExit(1)", {})):
            with self.subTest(code=code), self.assertRaises(CommandError):
                bounded_output((sys.executable, "-c", code), **options)


class LocalProbeTests(unittest.TestCase):
    def test_windows_macos_and_unknown_os_preserve_unknown_memory(self):
        for os_name in ("Windows", "Darwin", "Other"):
            with self.subTest(os_name=os_name), tempfile.TemporaryDirectory() as folder:
                value = LocalHardwareProbe(system=lambda: os_name).probe(Path(folder))
                self.assertEqual(value.os_name, os_name)
                self.assertIsNone(value.ram.available.value)
                self.assertIsNone(value.gpus)
                self.assertIsNotNone(value.disk.available.value)

    def test_disk_permission_error_does_not_leak_path(self):
        with patch("q25.hardware.probe.shutil.disk_usage", side_effect=PermissionError("private")):
            value = LocalHardwareProbe(system=lambda: "Other").probe(Path("/private-placeholder"))
        self.assertIsNone(value.disk.available.value)
        self.assertNotIn("private", value.disk.available.reason)
