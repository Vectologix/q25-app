from dataclasses import FrozenInstanceError, asdict, replace
import unittest

from q25.core.hardware import Capacity, Reading
from q25.hardware.catalog import load_catalog
from q25.hardware.memory import (
    MAX_PROFILES, MAX_TOKEN_COUNT, MemoryPlanner, TaskRequest, TokenBudget,
)
from q25.hardware.selection import BudgetPolicy, recommend
from tests.helpers import GIB, capacity, gpu, profile, snapshot


# Mesures inventées et indépendantes : aucune formule pour un modèle réel.
FIXTURE_CACHE = {4096:GIB,8192:2*GIB,16384:4*GIB,32768:8*GIB}


def contexts(values=(4096,8192,16384,32768),**changes):
    return tuple(profile(identifier="llm-"+str(context),context_tokens=context,
                         kv_cache_bytes=FIXTURE_CACHE[context],**changes) for context in values)


def request(*,source=1000,output=512,task="qa",preferred=None,ceiling=32768,**counts):
    return TaskRequest(TokenBudget(output_tokens=output,source_tokens=source,**counts),task,
                       preferred,ceiling)


def plan(req=None,models=None,hardware=None,**kwargs):
    return MemoryPlanner().plan(snapshot() if hardware is None else hardware,
                                contexts() if models is None else models,
                                request() if req is None else req,allow_synthetic=True,**kwargs)


class TokenContractTests(unittest.TestCase):
    def test_all_prompt_parts_and_generation_are_counted(self):
        tokens=TokenBudget(output_tokens=512,system_tokens=256,question_tokens=128,
                           history_tokens=128,source_tokens=2048)
        self.assertEqual(tokens.total_tokens,3072)
        self.assertEqual(tokens.non_source_tokens,1024)
        with self.assertRaises(FrozenInstanceError):tokens.source_tokens=0

    def test_invalid_values_and_aggregate_overflow_are_refused_neutrally(self):
        for field in ("output_tokens","system_tokens","question_tokens","history_tokens","source_tokens"):
            for value in (True,-1,1.5,float("nan"),float("inf"),None,"PRIVATE",MAX_TOKEN_COUNT+1):
                kwargs={"output_tokens":1,field:value}
                with self.subTest(field=field,kind=type(value).__name__),self.assertRaises(ValueError) as error:
                    TokenBudget(**kwargs)
                self.assertNotIn("PRIVATE",str(error.exception))
        with self.assertRaises(ValueError):TokenBudget(output_tokens=0)
        with self.assertRaises(ValueError):TokenBudget(output_tokens=MAX_TOKEN_COUNT,source_tokens=1)
        self.assertEqual(TokenBudget(output_tokens=MAX_TOKEN_COUNT).total_tokens,MAX_TOKEN_COUNT)

    def test_invalid_request_and_policy_are_refused(self):
        for kwargs in ({"task":"PRIVATE"},{"task":[]},{"preferred_context_tokens":True},
                       {"preferred_context_tokens":0},{"max_context_tokens":0},
                       {"max_context_tokens":MAX_TOKEN_COUNT+1}):
            with self.subTest(kwargs=tuple(kwargs)),self.assertRaises(ValueError):
                TaskRequest(TokenBudget(output_tokens=1),**kwargs)
        with self.assertRaises(ValueError):TaskRequest(None)
        with self.assertRaises(ValueError):MemoryPlanner(policy=None)

    def test_catalog_limit_and_fixture_flag_are_checked(self):
        one=contexts((4096,))[0]
        for models,flag in (([one],True),((object(),),True),((one,),"yes"),
                            (tuple(replace(one,id="id-"+str(i)) for i in range(MAX_PROFILES+1)),True)):
            with self.subTest(length=len(models)),self.assertRaises(ValueError):
                MemoryPlanner().plan(snapshot(),models,request(),allow_synthetic=flag)


class ContextSelectionTests(unittest.TestCase):
    def test_standard_does_not_choose_largest_available_context(self):
        result=plan()
        self.assertEqual(result.context_limit,8192)
        self.assertEqual(result.selected_context_tokens,8192)
        self.assertEqual(result.selected_profile_ids,("llm-8192",))
        self.assertIsNone(result.fallback)
        by_id={d.profile_id:d for d in result.decisions}
        self.assertFalse(by_id['llm-32768'].eligible)

    def test_task_presets_are_limited_to_proven_profiles(self):
        for task,wanted in [("extraction",4096),("qa",8192),("redaction_context",8192),
                            ("summary",16384),("comparison",32768)]:
            with self.subTest(task=task):
                self.assertEqual(plan(request(task=task)).selected_context_tokens,wanted)

    def test_manual_preference_and_global_ceiling_take_precedence(self):
        result=plan(request(task="comparison",preferred=16384,ceiling=8192))
        self.assertEqual(result.context_limit,8192)
        self.assertEqual(result.selected_context_tokens,8192)
        self.assertTrue(any("plafond global" in note for note in result.notes))
        self.assertEqual(plan(request(preferred=4096)).selected_context_tokens,4096)

    def test_no_interpolation_or_automatic_context_increase(self):
        reduced=plan(request(preferred=7000))
        self.assertEqual(reduced.selected_context_tokens,4096)
        self.assertTrue(any("réduit" in note for note in reduced.notes))
        only_large=plan(models=contexts((32768,)))
        self.assertIsNone(only_large.selected_context_tokens)
        self.assertEqual(only_large.fallback,"sans_llm")
        self.assertFalse(only_large.plans)

    def test_inclusive_limit_preserves_generation_reserve(self):
        kwargs=dict(source=2560,output=512,system_tokens=512,question_tokens=256,history_tokens=256,preferred=4096)
        exact=plan(request(**kwargs))
        self.assertEqual(exact.tokens.total_tokens,4096)
        self.assertEqual(exact.selected_context_tokens,4096)
        kwargs['source']+=1
        over=plan(request(**kwargs))
        self.assertIsNone(over.selected_context_tokens)
        self.assertEqual(over.tokens.total_tokens,4097)
        self.assertEqual(over.fallback,"segmentation_required")

    def test_long_sources_are_not_silently_truncated_or_sent_to_larger_context(self):
        req=request(source=20000,output=512)
        before=asdict(req)
        result=plan(req)
        self.assertEqual(result.fallback,"segmentation_required")
        self.assertEqual(result.selected_profile_ids,())
        self.assertFalse(result.plans)
        self.assertEqual(result.tokens,req.tokens)
        self.assertEqual(asdict(req),before)

    def test_splitting_sources_cannot_fix_an_oversized_fixed_budget(self):
        for kwargs in ({"source":100,"output":4096},
                       {"source":100,"output":512,"history_tokens":4000},
                       {"source":0,"output":4097}):
            with self.subTest(kwargs=tuple(kwargs)):
                result=plan(request(preferred=4096,**kwargs))
                self.assertEqual(result.fallback,"sans_llm")
                self.assertFalse(result.plans)

    def test_recommendation_is_stable_under_catalog_reordering(self):
        profiles=contexts()
        self.assertEqual(plan(models=profiles),plan(models=tuple(reversed(profiles))))

    def test_models_and_quantizations_are_options_without_quality_ranking(self):
        first=profile(identifier="model-a-q4",context_tokens=8192,ram=4*GIB,quantization="fixture-q4")
        second=profile(identifier="model-b-q8",context_tokens=8192,ram=8*GIB,quantization="fixture-q8")
        result=plan(models=(second,first))
        self.assertEqual(result.selected_profile_ids,("model-a-q4","model-b-q8"))
        self.assertEqual(len(result.plans),2)
        self.assertTrue(all(p.strategy=="single" for p in result.plans))

    def test_old_short_context_profile_remains_usable_without_schema_change(self):
        result=plan(models=(profile(),))
        self.assertEqual(result.selected_context_tokens,2048)
        self.assertEqual(result.selected_profile_ids,("synthetic-llm",))


class MemorySafetyTests(unittest.TestCase):
    def test_free_ram_and_context_specific_cache_drive_reduction(self):
        hardware=snapshot(ram=64*GIB,available=9*GIB)
        result=plan(hardware=hardware)
        self.assertEqual(result.budgets.ram_bytes,5*GIB)
        self.assertEqual(result.selected_context_tokens,4096)
        by_id={d.profile_id:d for d in result.decisions}
        self.assertEqual(by_id['llm-8192'].ram_required,6*GIB)
        self.assertFalse(by_id['llm-8192'].eligible)
        self.assertEqual(result.budgets,recommend(hardware,contexts(),allow_synthetic=True).budgets)

    def test_memory_shortage_can_require_segmentation_without_dropping_sources(self):
        result=plan(request(source=5000),hardware=snapshot(ram=64*GIB,available=9*GIB))
        self.assertEqual(result.fallback,"segmentation_required")
        self.assertEqual(result.tokens.source_tokens,5000)

    def test_unknown_ram_never_becomes_an_estimated_budget(self):
        hardware=replace(snapshot(),ram=Capacity(snapshot().ram.total,Reading.unknown("inconnue","fixture")))
        result=plan(hardware=hardware)
        self.assertIsNone(result.budgets.ram_bytes)
        self.assertEqual(result.fallback,"sans_llm")
        self.assertFalse(result.plans)

    def test_dedicated_gpu_uses_vram_and_cache_without_summing_devices(self):
        common=dict(ram=GIB,memory_mode="dedicated",weights_bytes=5*GIB,vram_bytes=5*GIB,
                    disk_bytes=5*GIB,artifact_bytes=5*GIB)
        profiles=contexts((4096,8192),**common)
        reduced=plan(models=profiles,hardware=snapshot(gpus=(gpu(available=7*GIB),)))
        self.assertEqual(reduced.selected_context_tokens,4096)
        self.assertEqual(plan(models=profiles,hardware=snapshot(gpus=(gpu(available=8*GIB),))).selected_context_tokens,8192)
        insufficient=plan(models=profiles,hardware=snapshot(gpus=(gpu(0,available=4*GIB),gpu(1,available=4*GIB))))
        self.assertEqual(insufficient.fallback,"sans_llm")
        self.assertEqual(plan(models=profiles,hardware=snapshot(gpus=None,gpu_reason="inconnue")).fallback,"sans_llm")

    def test_unified_memory_is_counted_once_and_requires_confirmed_topology(self):
        profiles=contexts((4096,8192),memory_mode="unified")
        result=plan(models=profiles,hardware=snapshot(ram=16*GIB,available=10*GIB,unified=True))
        self.assertEqual(result.selected_context_tokens,8192)
        self.assertEqual(result.decisions[1].vram_required,0)
        unknown=replace(snapshot(),unified_memory=Reading.unknown("inconnue","fixture","boolean"))
        self.assertEqual(plan(models=profiles,hardware=unknown).fallback,"sans_llm")

    def test_full_weights_and_declared_quantization_footprint_still_matter(self):
        profiles=(profile(identifier="small",context_tokens=8192,ram=4*GIB),
                  profile(identifier="large",context_tokens=8192,ram=14*GIB,quantization="fixture-q8"),
                  profile(identifier="bad-moe",context_tokens=8192,ram=4*GIB,weights_bytes=20*GIB))
        self.assertEqual(plan(models=profiles).selected_profile_ids,("small",))

    def test_separate_embedding_limit_and_original_loading_strategies_are_preserved(self):
        profiles=(profile(identifier="encoder",role="embedding",context_tokens=512,ram=5*GIB),
                  profile(identifier="llm",context_tokens=8192,ram=6*GIB))
        for ram,strategy in [(12,"sequential"),(16,"concurrent")]:
            with self.subTest(ram=ram):
                result=plan(request(source=6000),models=profiles,hardware=snapshot(ram=ram*GIB))
                self.assertEqual(result.selected_profile_ids,("llm",))
                pairs=[p for p in result.plans if len(p.profile_ids)==2]
                self.assertEqual(pairs[0].strategy,strategy)
                self.assertFalse(any(p.profile_ids==("encoder",) for p in result.plans))

    def test_combined_disk_rule_still_blocks_a_pair(self):
        profiles=(profile(identifier="encoder",role="embedding",context_tokens=512,ram=5*GIB),
                  profile(identifier="llm",context_tokens=8192,ram=6*GIB))
        result=plan(models=profiles,hardware=snapshot(disk=capacity(20*GIB,10*GIB)))
        self.assertEqual(result.selected_profile_ids,("llm",))
        self.assertFalse(any(len(p.profile_ids)==2 for p in result.plans))
        self.assertTrue(result.notes)

    def test_only_selected_llm_contexts_are_in_execution_plans(self):
        profiles=contexts()+(profile(identifier="encoder",role="embedding",context_tokens=512,ram=GIB),)
        result=plan(models=profiles)
        for candidate in result.plans:
            self.assertIn("llm-8192",candidate.profile_ids)
            self.assertTrue(set(candidate.profile_ids)<={"llm-8192","encoder"})

    def test_unsupported_profiles_and_missing_proofs_remain_refused(self):
        for changes in ({"status":"unverified"},{"status":"experimental"},{"evidence":()},
                        {"context_tokens":None},{"platforms":("Windows",)},
                        {"architectures":("aarch64",)},{"license":None}):
            with self.subTest(changes=tuple(changes)):
                model=replace(contexts((8192,))[0],**changes)
                self.assertEqual(plan(models=(model,)).fallback,"sans_llm")

    def test_real_catalog_and_synthetic_profiles_do_not_gain_automatic_approval(self):
        for profiles in (load_catalog(),contexts()):
            result=MemoryPlanner().plan(snapshot(),profiles,request())
            self.assertEqual(result.fallback,"sans_llm")
            self.assertFalse(result.selected_profile_ids)
            self.assertFalse(result.plans)

    def test_no_generator_no_memory_or_empty_catalog_cannot_suggest_a_usable_plan(self):
        for profiles,hardware in [((),snapshot()),
                                  ((profile(role="embedding"),),snapshot()),
                                  (contexts(),snapshot(available=3*GIB))]:
            with self.subTest(length=len(profiles)):
                result=plan(models=profiles,hardware=hardware)
                self.assertEqual(result.fallback,"sans_llm")
                self.assertFalse(result.plans)

    def test_duplicate_ids_and_old_measurements_are_handled_explicitly(self):
        with self.assertRaises(ValueError):plan(models=(profile(),profile()))
        hardware=snapshot()
        result=plan(hardware=hardware)
        self.assertEqual(result.hardware_measured_at,hardware.measured_at)
        self.assertTrue(result.requires_resource_recheck)

    def test_budget_policy_is_not_reimplemented_or_ignored(self):
        policy=BudgetPolicy(system_headroom_bytes=0,application_bytes=0,margin_bytes=0)
        result=MemoryPlanner(policy).plan(snapshot(available=6*GIB),contexts(),request(),allow_synthetic=True)
        self.assertEqual(result.budgets.ram_bytes,6*GIB)
        self.assertEqual(result.selected_context_tokens,8192)
