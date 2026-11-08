"""Hybrid embedding retrieval and the memory evaluation harness."""
import os
import sys
import unittest
from dataclasses import replace
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from test_memory_support import *  # noqa: F401,F403,E402

from agents.memory import embeddings as emb  # noqa: E402
from agents.memory import retrieve as ret  # noqa: E402
from agents.memory.config import RetrievalConfig  # noqa: E402
from evaluation import memory_eval as ev  # noqa: E402


class FakeEmbedder:
    """Maps texts containing 'alpha' to one axis and everything else to another, so similarity is fully controlled."""
    name = "fake"

    def __init__(self):
        self.calls = 0

    def embed(self, texts):
        self.calls += 1
        return [[1.0, 0.0] if "alpha" in t.lower() else [0.0, 1.0] for t in texts]


class FailingEmbedder:
    name = "fail"

    def embed(self, texts):
        raise emb.EmbeddingError("backend down")


class EmbedderTests(unittest.TestCase):
    def test_hash_embedder_is_deterministic_unit_length_and_matches_word_forms(self):
        h = emb.HashingEmbedder()
        a, b = h.embed(["retirement age"]), h.embed(["retirement age"])
        self.assertEqual(a, b)
        self.assertAlmostEqual(sum(x * x for x in a[0]), 1.0, places=6)
        q, near, far = h.embed(["when will he retire"]) + h.embed(["retiring at 63"]) + h.embed(["education funding for graduate school"])
        self.assertGreater(emb.cosine(q, near), emb.cosine(q, far))

    def test_cache_embeds_each_text_once(self):
        fake = FakeEmbedder()
        c = emb.CachedEmbedder(fake)
        c.embed(["alpha one", "beta"])
        c.embed(["alpha one", "beta"])
        self.assertEqual(fake.calls, 1)
        c.embed(["alpha one", "gamma"])
        self.assertEqual(fake.calls, 2)

    def test_default_backend_is_off_and_env_selects_one(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("CMF_EMBED_BACKEND", None)
            self.assertIsNone(emb.default_embedder())
        with mock.patch.dict(os.environ, {"CMF_EMBED_BACKEND": "hash"}):
            self.assertEqual(emb.default_embedder().name, "hash-512")
        with mock.patch.dict(os.environ, {"CMF_EMBED_BACKEND": "bogus"}):
            with self.assertRaises(emb.EmbeddingError):
                emb.default_embedder()

    def test_openai_embedder_requires_a_key_from_the_environment(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("OPENAI_API_KEY", None)
            with self.assertRaises(emb.EmbeddingError):
                emb.OpenAIEmbedder().embed(["x"])


class HybridRankingTests(unittest.TestCase):
    ROWS = [{"memory_id": "A", "content": "plain note", "subject": "s", "topic": "other", "certainty": "exploratory"},
            {"memory_id": "B", "content": "alpha note", "subject": "s", "topic": "other", "certainty": "exploratory"}]

    def test_relevance_default_matches_the_old_keyword_formula(self):
        r = self.ROWS[0]
        self.assertEqual(ret.relevance(r, set()), 0.5)
        self.assertEqual(ret.relevance(r, set(), sem=0.9, semantic_weight=0.0), 0.5)
        self.assertAlmostEqual(ret.relevance(r, set(), sem=0.8, semantic_weight=1.0), 1.3)
        self.assertEqual(ret.relevance(r, set(), sem=-0.5, semantic_weight=1.0), 0.5)          # negative similarity never penalises

    def test_semantic_scores_use_the_embedder_and_respect_weight_zero(self):
        cfg = RetrievalConfig(embedder=FakeEmbedder())
        sims, note = ret.semantic_scores("alpha question", self.ROWS, cfg)
        self.assertEqual((sims, note), ({"A": 0.0, "B": 1.0}, None))
        self.assertEqual(ret.semantic_scores("alpha question", self.ROWS, replace(cfg, semantic_weight=0.0)), ({}, None))

    def test_backend_failure_falls_back_to_keywords_with_a_note(self):
        sims, note = ret.semantic_scores("q", self.ROWS, RetrievalConfig(embedder=FailingEmbedder()))
        self.assertEqual(sims, {})
        self.assertIn("ranked by keywords only", note)


class EvalHarnessTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.rep = ev.run(ev.SEED_DB, [3, 8], emb.CachedEmbedder(emb.HashingEmbedder()))

    def test_seed_semantic_chains_match_gold(self):
        self.assertEqual(self.rep["chains"]["accuracy"], 1.0)

    def test_no_cross_client_leakage_and_values_are_correct_in_every_mode(self):
        for name, m in self.rep["retrieval"].items():
            self.assertEqual(m["leaks"], 0, name)
            self.assertEqual(m["value_acc"], 1.0, name)
            self.assertEqual(m["embedding_errors"], 0, name)

    def test_larger_cap_never_lowers_keyword_recall(self):
        r = self.rep["retrieval"]
        self.assertGreaterEqual(r["cap=8/keyword"]["episode_recall"], r["cap=3/keyword"]["episode_recall"])

    def test_extraction_has_no_unsupported_commitments_and_reports_every_kind(self):
        x = self.rep["extraction"]
        self.assertEqual(x["unsupported_commitments"]["count"], 0)
        self.assertEqual(set(x["recall_by_kind"]), set(ev.KIND_CERTAINTY))
        self.assertEqual(x["recall_by_kind"]["decision"]["recall"], 1.0)

    def test_overlap_metric(self):
        self.assertGreaterEqual(ev.overlap("to retire at 63, replacing the earlier plan of 65, because the role changed", "Kenji has decided to retire at 63."), 0.4)
        self.assertLess(ev.overlap("sign the investment policy statement", "Kenji has decided to retire at 63."), 0.4)

    def test_seed_db_is_not_modified(self):
        import hashlib
        before = hashlib.sha1(ev.SEED_DB.read_bytes()).hexdigest()
        ev.run(ev.SEED_DB, [3], None)
        self.assertEqual(hashlib.sha1(ev.SEED_DB.read_bytes()).hexdigest(), before)


if __name__ == "__main__":
    unittest.main()
