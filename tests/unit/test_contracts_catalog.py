import unittest
from dataclasses import replace

from q25.core.hardware import Capacity, Reading
from q25.hardware.catalog import CatalogError, load_catalog, parse_catalog
from tests.helpers import capacity, profile, reading, snapshot


class ContractTests(unittest.TestCase):
    def test_unknown_requires_reason_and_known_has_no_reason(self):
        with self.assertRaises(ValueError):
            Reading(None, "fixture", "bytes")
        with self.assertRaises(ValueError):
            Reading(1, "fixture", "bytes", "inconnu")
        self.assertIsNone(Reading.unknown("indisponible", "fixture").value)

    def test_invalid_capacities_are_rejected(self):
        for total, free in ((-1, 0), (1, 2), (True, 0), (1.2, 0)):
            with self.subTest(total=total, free=free), self.assertRaises(ValueError):
                capacity(total, free)

    def test_partial_capacity_is_allowed(self):
        value = Capacity(reading(10), Reading.unknown("permission refusée", "fixture"))
        self.assertIsNone(value.available.value)

    def test_snapshot_requires_aware_time_and_valid_cpu(self):
        with self.assertRaises(ValueError):
            snapshot(measured_at=snapshot().measured_at.replace(tzinfo=None))
        with self.assertRaises(ValueError):
            snapshot(cpu_logical=reading(0, "count"))

    def test_gpu_unknown_requires_reason(self):
        with self.assertRaises(ValueError):
            snapshot(gpus=None)

    def test_invalid_metadata_and_mutable_gpu_list_are_rejected(self):
        for args in ((1, 2, "bytes"), (None, "fixture", "bytes", 1),
                     (1, "fixture", "bytes", "")):
            with self.subTest(args=args), self.assertRaises(ValueError):
                Reading(*args)
        with self.assertRaises(ValueError):
            snapshot(gpus=[])


class CatalogTests(unittest.TestCase):
    def test_shipped_catalog_is_explicitly_unverified(self):
        catalog = load_catalog()
        self.assertEqual(len(catalog), 4)
        self.assertTrue(all(p.status == "unverified" for p in catalog))
        self.assertTrue(all(p.missing_proofs() for p in catalog))
        self.assertTrue(all(p.sha256 is None for p in catalog))

    def test_invalid_toml_and_schema(self):
        for raw in ('broken = [', 'schema_version = 2', 'schema_version = true',
                    'schema_version = 1\nprofiles = "wrong"',
                    'schema_version = 1\nextra = 1'):
            with self.subTest(raw=raw), self.assertRaises(CatalogError):
                parse_catalog(raw)

    def test_duplicate_ids_and_unknown_keys(self):
        entry = '\n[[profiles]]\nid="one"\nmodel="fictif"\nrole="llm"\n'
        with self.assertRaises(CatalogError):
            parse_catalog('schema_version=1' + entry + entry)
        with self.assertRaises(CatalogError):
            parse_catalog('schema_version=1' + entry + 'executable="evil"\n')

    def test_wrong_types_and_values(self):
        for changes in ({"ram_bytes": -1}, {"context_tokens": True},
                        {"status": "approved"}, {"role": "script"},
                        {"sha256": "bad"}, {"platforms": ("Linux", 1)},
                        {"source_url": "file:///private"},
                        {"evidence": "not-a-list"}, {"synthetic": "false"}):
            with self.subTest(changes=changes), self.assertRaises(CatalogError):
                replace(profile(), **changes)

    def test_missing_proofs_are_reported(self):
        value = profile(model_version=None, license=None, evidence=())
        self.assertIn("model_version", value.missing_proofs())
        self.assertIn("license", value.missing_proofs())
        self.assertIn("evidence", value.missing_proofs())

    def test_minimal_unverified_entry(self):
        value, = parse_catalog('schema_version=1\n[[profiles]]\nid="a"\nmodel="fictif"\nrole="embedding"')
        self.assertEqual(value.status, "unverified")
        self.assertIsNone(value.ram_bytes)

    def test_catalog_size_is_bounded(self):
        with self.assertRaises(CatalogError):
            parse_catalog(" " * (1024 * 1024 + 1))
