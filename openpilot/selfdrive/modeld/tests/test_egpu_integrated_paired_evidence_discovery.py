"""Hardware-free tests for read-only paired-evidence discovery."""
from __future__ import annotations

import json
from pathlib import Path
import tempfile
import shutil
import unittest
from unittest.mock import patch

from tools import egpu_integrated_paired_evidence_discovery as discovery

HEAD = "a" * 40
BRANCH = "carrot-wip-integrated-v6"


class TestPairedEvidenceDiscovery(unittest.TestCase):
  def setUp(self):
    self.temporary = tempfile.TemporaryDirectory()
    self.addCleanup(self.temporary.cleanup)
    self.root = Path(self.temporary.name)

  def report(self, root: Path | None = None, *, verify: bool = True):
    return discovery.build_report([root or self.root], expected_head=HEAD, expected_branch=BRANCH,
                                  max_depth=8, max_files=10000, verify_paired=verify)

  def assert_no_authority(self, report):
    for key, value in discovery.FALSE_BOUNDARY.items():
      self.assertIs(report[key], value)

  def test_inaccessible_root_is_reported_without_claiming_absence_everywhere(self):
    report = self.report(self.root / "missing")
    self.assertEqual(report["state"], "NO_ACCESSIBLE_ROOTS")
    self.assertFalse(report["roots"][0]["accessible"])
    self.assert_no_authority(report)

  def test_raw_route_only_is_not_promoted_to_paired_evidence(self):
    route = self.root / "routes" / "car" / "route--0"
    route.mkdir(parents=True)
    (route / "rlog.zst").write_bytes(b"not-a-real-log")
    report = self.report()
    self.assertEqual(report["state"], "RAW_ROUTE_ONLY_FOUND")
    self.assertEqual(len(report["roots"][0]["rawRouteLogs"]), 1)
    self.assertEqual(report["roots"][0]["verifiedPaired"], [])
    self.assert_no_authority(report)

  def test_loose_small_big_pair_requires_provenance(self):
    pair = self.root / "experiment"
    pair.mkdir()
    (pair / "small_actions.jsonl").write_text('{}\n', encoding="utf-8")
    (pair / "big_actions.jsonl").write_text('{}\n', encoding="utf-8")
    report = self.report()
    self.assertEqual(report["state"], "LOOSE_PAIRED_CANDIDATE_FOUND")
    self.assertEqual(report["roots"][0]["loosePaired"][0]["status"], "PROVENANCE_REQUIRED")
    self.assert_no_authority(report)

  def test_shadow_output_without_active_counterpart_is_separate_hold_state(self):
    shadow = self.root / "shadow.jsonl"
    shadow.write_text(json.dumps({"type": "shadow_output", "frameId": 1, "shadowBackend": "small", "activeBackend": "big"}) + "\n", encoding="utf-8")
    report = self.report()
    self.assertEqual(report["state"], "SHADOW_OUTPUT_ONLY_FOUND")
    self.assertEqual(report["roots"][0]["shadowOutputs"], [str(shadow)])
    self.assert_no_authority(report)

  def test_exact_adapter_directory_is_verified_through_existing_adapter(self):
    candidate = self.root / "paired"
    candidate.mkdir()
    for name in discovery.ADAPTER_OUTPUT_FILES:
      (candidate / name).write_text("x\n", encoding="utf-8")
    verified = {"status": "VERIFIED_OFFLINE_CONSISTENCY", "rows": 3, **discovery.FALSE_BOUNDARY}
    with patch.object(discovery, "verify_adapter_output", return_value=verified) as check:
      report = self.report()
    self.assertEqual(report["state"], "VERIFIED_PAIRED_EVIDENCE_FOUND")
    self.assertEqual(report["roots"][0]["verifiedPaired"][0]["verification"]["rows"], 3)
    check.assert_called_once_with(candidate, expected_source_head=HEAD, expected_source_branch=BRANCH)
    self.assert_no_authority(report)

  def test_exact_adapter_directory_without_verification_is_reported_separately(self):
    candidate = self.root / "paired"
    candidate.mkdir()
    for name in discovery.ADAPTER_OUTPUT_FILES:
      (candidate / name).write_text("x\n", encoding="utf-8")
    with patch.object(discovery, "verify_adapter_output") as check:
      report = self.report(verify=False)
    self.assertEqual(report["schemaVersion"], 2)
    self.assertEqual(report["state"], "PAIRED_EVIDENCE_FOUND_NOT_VERIFIED")
    self.assertEqual(report["roots"][0]["unverifiedPaired"], [{"path": str(candidate), "status": "FOUND_NOT_VERIFIED"}])
    self.assertEqual(report["roots"][0]["invalidPaired"], [])
    check.assert_not_called()
    self.assert_no_authority(report)

  def test_failed_adapter_verification_is_not_downgraded_to_loose_candidate(self):
    candidate = self.root / "paired"
    candidate.mkdir()
    for name in discovery.ADAPTER_OUTPUT_FILES:
      (candidate / name).write_text("x\n", encoding="utf-8")
    with patch.object(discovery, "verify_adapter_output", side_effect=ValueError("receipt mismatch")):
      report = self.report()
    self.assertEqual(report["state"], "INVALID_PAIRED_EVIDENCE_FOUND")
    self.assertIn("receipt mismatch", report["roots"][0]["invalidPaired"][0]["reason"])
    self.assertEqual(report["roots"][0]["loosePaired"], [])

  def test_file_limit_is_reported_as_truncated(self):
    for index in range(3):
      (self.root / f"file-{index}.txt").write_text("x", encoding="utf-8")
    report = discovery.build_report([self.root], expected_head=HEAD, expected_branch=BRANCH,
                                    max_depth=8, max_files=2, verify_paired=False)
    self.assertTrue(report["roots"][0]["truncated"])
    self.assertEqual(report["roots"][0]["filesScanned"], 2)

  def test_symlink_directory_is_not_followed(self):
    outside = Path(self.temporary.name + "-outside")
    outside.mkdir()
    self.addCleanup(lambda: shutil.rmtree(outside, ignore_errors=True))
    (outside / "rlog.zst").write_bytes(b"x")
    link = self.root / "linked"
    try:
      link.symlink_to(outside, target_is_directory=True)
    except (OSError, NotImplementedError):
      self.skipTest("symlinks unavailable")
    report = self.report()
    self.assertEqual(report["state"], "NO_PAIRED_EVIDENCE_FOUND")
    self.assertIn(str(link), report["roots"][0]["skipped"])
    self.assertEqual(report["roots"][0]["rawRouteLogs"], [])
