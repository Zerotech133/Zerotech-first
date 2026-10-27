from __future__ import annotations

import hashlib
import importlib
import tempfile
import unittest
from pathlib import Path

rag_baseline = importlib.import_module("AI.111111.rag_baseline")
build_manifest = rag_baseline.build_manifest
build_title_cases = rag_baseline.build_title_cases
run_retrieval_eval = rag_baseline.run_retrieval_eval


class FakeService:
    def search(self, question, top_k=None):
        if "太阳" in question:
            return [{"source": "facts.md", "score": 0.92, "text": "太阳从东方升起"}]
        return []


class RagBaselineTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / "facts.md").write_text("# 太阳知识\n太阳从东方升起。", encoding="utf-8")
        (self.root / "risk.txt").write_text("# 风险\n先核验来源。", encoding="utf-8")
        (self.root / "skip.json").write_text("{}", encoding="utf-8")

    def tearDown(self):
        self.tmp.cleanup()

    def test_manifest_is_conservative_and_hashes_supported_documents(self):
        manifest = build_manifest(self.root)
        self.assertEqual(manifest["counts"], {"total": 2, "training_eligible": 0, "evaluation_only": 2})
        facts = next(item for item in manifest["records"] if item["source"] == "facts.md")
        expected = hashlib.sha256((self.root / "facts.md").read_bytes()).hexdigest()
        self.assertEqual(facts["sample_sha256"], expected)
        self.assertTrue(facts["evaluation_only"])
        self.assertFalse(facts["training_eligible"])
        self.assertIn("license", " ".join(facts["eligibility_reasons"]))
        self.assertIn("offline_review_receipt", " ".join(facts["eligibility_reasons"]))

    def test_unverified_receipt_cannot_enable_training(self):
        source = self.root / "facts.md"
        metadata = {
            "facts.md": {
                "license_id": "L-1",
                "license_subject": "本人",
                "purpose": "评测",
                "valid_from": "2026-01-01",
                "valid_until": "2027-01-01",
                "commercial_scope": "禁止",
                "source_reference": "本地归档",
                "split": "evaluation",
                "split_group": "facts",
                "sample_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
                "license_approved": True,
                "permission_snapshot": {"id": "P-1"},
                "offline_review_receipt": {
                    "signature": "sig",
                    "proof_sha256": "proof",
                    "verified": False,
                },
            }
        }
        manifest = build_manifest(self.root, metadata=metadata)
        facts = next(item for item in manifest["records"] if item["source"] == "facts.md")
        self.assertFalse(facts["training_eligible"])
        self.assertTrue(facts["evaluation_only"])
        self.assertIn("offline_review_receipt_verified", " ".join(facts["eligibility_reasons"]))
    def test_missing_directory_is_rejected(self):
        with self.assertRaises(ValueError):
            build_manifest(self.root / "missing")

    def test_title_cases_preserve_relative_source(self):
        cases = build_title_cases(self.root)
        self.assertEqual(
            cases,
            [
                {"id": "facts", "question": "太阳知识", "expected_sources": ["facts.md"]},
                {"id": "risk", "question": "风险", "expected_sources": ["risk.txt"]},
            ],
        )

    def test_retrieval_eval_reports_hits_and_misses(self):
        cases = [
            {"id": "hit", "question": "太阳从哪里升起？", "expected_sources": ["facts.md"]},
            {"id": "miss", "question": "没有对应资料的问题", "expected_sources": ["risk.txt"]},
        ]
        report = run_retrieval_eval(FakeService(), cases, top_k=2)
        self.assertEqual(report["summary"], {"total": 2, "hits": 1, "misses": 1, "hit_rate": 0.5})
        self.assertTrue(report["cases"][0]["citation_complete"])
        self.assertFalse(report["cases"][1]["citation_complete"])


class OfflineRegressionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / "facts.md").write_text("# 太阳知识\n太阳从东方升起。", encoding="utf-8")
        (self.root / "risk.txt").write_text("# 风险\n回答前先核验来源。", encoding="utf-8")

    def tearDown(self):
        self.tmp.cleanup()

    def test_offline_regression_reports_all_metrics(self):
        import json
        rb = importlib.import_module("AI.111111.rag_baseline")
        cases = self.root / "evaluation-cases.jsonl"
        cases.write_text(
            "\n".join([
                json.dumps({"id": "sun-semantic", "question": "太阳每天从哪里升起？", "expected_sources": ["facts.md"], "split": "evaluation_only"}, ensure_ascii=False),
                json.dumps({"id": "risk-semantic", "question": "回答前如何核验来源？", "expected_sources": ["risk.txt"], "split": "evaluation_only"}, ensure_ascii=False),
                json.dumps({"id": "unknown", "question": "火星基地的虚构规则是什么？", "expected_sources": [], "expected_decision": "refuse_missing_evidence", "split": "evaluation_only"}, ensure_ascii=False),
            ]) + "\n", encoding="utf-8")
        report_path = self.root / "regression.json"
        report = rb.run_offline_regression(self.root, cases_path=cases, report_out=report_path)
        self.assertEqual(report["metrics"]["title_retrieval"]["hit_rate"], 1.0)
        self.assertEqual(report["metrics"]["semantic_retrieval"]["hit_rate"], 1.0)
        self.assertEqual(report["metrics"]["source_hit_rate"], 1.0)
        self.assertEqual(report["metrics"]["unknown_refusal"]["refusal_rate"], 1.0)
        self.assertTrue(report["evaluation_only"])
        self.assertFalse(report["training_eligible"])
        self.assertEqual(report["model_calls"], 0)
        self.assertEqual(report["training_steps"], 0)
        self.assertEqual(json.loads(report_path.read_text(encoding="utf-8")), report)

    def test_offline_regression_does_not_touch_ollama(self):
        import urllib.request
        rb = importlib.import_module("AI.111111.rag_baseline")
        original = urllib.request.urlopen
        urllib.request.urlopen = lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("offline regression must not call Ollama"))
        try:
            report = rb.run_offline_regression(self.root)
        finally:
            urllib.request.urlopen = original
        self.assertEqual(report["mode"], "offline_deterministic")


if __name__ == "__main__":
    unittest.main()

class OfflineRegressionV2Tests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / "facts.md").write_text("# 太阳知识\n太阳从东方升起。", encoding="utf-8")
        (self.root / "risk.txt").write_text("# 风险\n回答前先核验来源。", encoding="utf-8")

    def tearDown(self):
        self.tmp.cleanup()

    def test_offline_regression_reports_all_metrics(self):
        import json
        rb = importlib.import_module("AI.111111.rag_baseline")
        cases = self.root / "evaluation-cases.jsonl"
        cases.write_text(
            "\n".join([
                json.dumps({"id": "sun-semantic", "question": "太阳每天从哪里升起？", "expected_sources": ["facts.md"], "split": "evaluation_only"}, ensure_ascii=False),
                json.dumps({"id": "risk-semantic", "question": "回答前如何核验来源？", "expected_sources": ["risk.txt"], "split": "evaluation_only"}, ensure_ascii=False),
                json.dumps({"id": "unknown", "question": "火星基地的虚构规则是什么？", "expected_sources": [], "expected_decision": "refuse_missing_evidence", "split": "evaluation_only"}, ensure_ascii=False),
            ]) + "\n", encoding="utf-8")
        report_path = self.root / "regression.json"
        report = rb.run_offline_regression(self.root, cases_path=cases, report_out=report_path)
        self.assertEqual(report["metrics"]["title_retrieval"]["hit_rate"], 1.0)
        self.assertEqual(report["metrics"]["semantic_retrieval"]["hit_rate"], 1.0)
        self.assertEqual(report["metrics"]["source_hit_rate"], 1.0)
        self.assertEqual(report["metrics"]["unknown_refusal"]["refusal_rate"], 1.0)
        self.assertTrue(report["evaluation_only"])
        self.assertFalse(report["training_eligible"])
        self.assertEqual(report["model_calls"], 0)
        self.assertEqual(report["training_steps"], 0)
        self.assertEqual(json.loads(report_path.read_text(encoding="utf-8")), report)

    def test_offline_regression_does_not_touch_ollama(self):
        import urllib.request
        rb = importlib.import_module("AI.111111.rag_baseline")
        original = urllib.request.urlopen
        urllib.request.urlopen = lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("offline regression must not call Ollama"))
        try:
            report = rb.run_offline_regression(self.root)
        finally:
            urllib.request.urlopen = original
        self.assertEqual(report["mode"], "offline_deterministic")
