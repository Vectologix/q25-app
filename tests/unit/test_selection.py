import unittest
from dataclasses import replace

from q25.core.hardware import Reading
from q25.hardware.selection import BudgetPolicy, recommend
from tests.helpers import GIB, capacity, gpu, profile, snapshot


class SelectionTests(unittest.TestCase):
    def choose(self, hw=None, profiles=None, **options):
        return recommend(hw or snapshot(), profiles or (profile(),), allow_synthetic=True, **options)

    def test_ram_profiles_and_exact_budget_boundary(self):
        for ram in (8, 16, 24, 32, 64):
            with self.subTest(ram=ram):
                hw = snapshot(ram=ram * GIB)
                budget = (ram - 4) * GIB
                fitting = profile(ram=budget)
                self.assertTrue(self.choose(hw, (fitting,)).decisions[0].eligible)
                too_large = replace(fitting, ram_bytes=budget + 1)
                self.assertFalse(self.choose(hw, (too_large,)).decisions[0].eligible)

    def test_free_memory_not_total_and_swap_is_not_added(self):
        self.assertTrue(self.choose(snapshot(available=12 * GIB)).decisions[0].eligible)
        self.assertFalse(self.choose(snapshot(available=5 * GIB)).decisions[0].eligible)

    def test_missing_ram_or_disk_fails_conservatively(self):
        missing = Reading.unknown("indisponible", "fixture")
        for changes in ({"ram": capacity(0, 0)},
                        {"ram": replace(snapshot().ram, available=missing)},
                        {"disk": capacity(100 * GIB, 0)}):
            with self.subTest(changes=changes):
                result = self.choose(replace(snapshot(), **changes))
                self.assertFalse(result.decisions[0].eligible)
                self.assertEqual(result.fallback, "sans_llm")
                self.assertTrue(result.decisions[0].reasons)

    def test_unverified_experimental_missing_proofs_and_wrong_platform(self):
        for changes in ({"status": "unverified"}, {"status": "experimental"},
                        {"license": None}, {"model_version": None},
                        {"backend_version": None}, {"evidence": ()},
                        {"context_tokens": None}, {"ram_bytes": None},
                        {"platforms": ("Windows",)}, {"architectures": ("aarch64",)}):
            with self.subTest(changes=changes):
                self.assertFalse(self.choose(profiles=(profile(**changes),)).decisions[0].eligible)

    def test_synthetic_profiles_excluded_by_default(self):
        result = recommend(snapshot(), (profile(),))
        self.assertFalse(result.decisions[0].eligible)

    def test_individual_gpu_fit_without_summing_vram(self):
        model = profile(ram=GIB, memory_mode="dedicated", weights_bytes=5 * GIB,
                        vram_bytes=5 * GIB, disk_bytes=6 * GIB, artifact_bytes=5 * GIB)
        hw = snapshot(gpus=(gpu(0, available=3 * GIB), gpu(1, available=3 * GIB)))
        self.assertFalse(self.choose(hw, (model,)).decisions[0].eligible)
        hw = snapshot(gpus=(gpu(0, available=3 * GIB), gpu(1, available=6 * GIB)))
        result = self.choose(hw, (model,))
        self.assertTrue(result.decisions[0].eligible)
        self.assertEqual(result.decisions[0].gpu_index, 1)

    def test_unknown_gpu_and_unified_memory_do_not_imply_compatibility(self):
        model = profile(memory_mode="dedicated", vram_bytes=4 * GIB)
        hw = snapshot(gpus=None, gpu_reason="sonde absente")
        self.assertFalse(self.choose(hw, (model,)).decisions[0].eligible)
        model = profile(memory_mode="unified")
        self.assertFalse(self.choose(snapshot(), (model,)).decisions[0].eligible)
        self.assertTrue(self.choose(snapshot(unified=True), (model,)).decisions[0].eligible)

    def test_unified_footprint_not_double_counted(self):
        model = profile(ram=8 * GIB, memory_mode="unified")
        self.assertTrue(self.choose(snapshot(ram=12 * GIB, unified=True), (model,)).decisions[0].eligible)
        invalid = profile(ram=GIB, memory_mode="unified", vram_bytes=8 * GIB)
        self.assertFalse(self.choose(snapshot(unified=True), (invalid,)).decisions[0].eligible)

    def test_context_cache_is_reserved_in_correct_memory_pool(self):
        model = profile(ram=4 * GIB, kv_cache_bytes=GIB)
        self.assertFalse(self.choose(snapshot(ram=8 * GIB), (model,)).decisions[0].eligible)

    def test_full_weights_not_only_active_parameters(self):
        model = profile(weights_bytes=20 * GIB, ram=4 * GIB)
        self.assertFalse(self.choose(profiles=(model,)).decisions[0].eligible)

    def test_sequential_and_concurrent_embedding_llm_plans(self):
        models = (profile("embedding", "embedding", ram=5 * GIB), profile(ram=6 * GIB))
        result = self.choose(snapshot(ram=12 * GIB), models)
        pair = [p for p in result.plans if len(p.profile_ids) == 2]
        self.assertEqual(pair[0].strategy, "sequential")
        result = self.choose(snapshot(ram=16 * GIB), models)
        self.assertEqual([p for p in result.plans if len(p.profile_ids) == 2][0].strategy, "concurrent")

    def test_pair_checks_combined_disk_even_when_sequential(self):
        models = (profile("embedding", "embedding", ram=5 * GIB), profile(ram=6 * GIB))
        result = self.choose(snapshot(disk=capacity(20 * GIB, 10 * GIB)), models)
        self.assertTrue(all(d.eligible for d in result.decisions))
        self.assertFalse(any(len(p.profile_ids) == 2 for p in result.plans))
        self.assertTrue(result.notes)

    def test_gpu_pair_uses_sequential_when_shared_vram_is_too_small(self):
        common = dict(ram=GIB, memory_mode="dedicated", weights_bytes=3 * GIB,
                      vram_bytes=3 * GIB, artifact_bytes=3 * GIB, disk_bytes=3 * GIB)
        models = (profile("embedding", "embedding", **common), profile(**common))
        result = self.choose(snapshot(gpus=(gpu(available=6 * GIB),)), models)
        pairs = [p for p in result.plans if len(p.profile_ids) == 2]
        self.assertEqual(pairs[0].strategy, "sequential")

    def test_duplicate_profile_ids_are_rejected(self):
        with self.assertRaises(ValueError):
            self.choose(profiles=(profile(), profile()))

    def test_policy_validation_and_empty_catalog(self):
        with self.assertRaises(ValueError):
            BudgetPolicy(system_headroom_bytes=-1)
        result = recommend(snapshot(), ())
        self.assertEqual(result.fallback, "sans_llm")
        self.assertFalse(result.plans)
