from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path


def _load_auditor():
    path = Path(__file__).parents[1] / "scripts" / "audit-public-readiness.py"
    spec = importlib.util.spec_from_file_location("public_readiness_audit", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("Could not load the public-readiness auditor")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class PublicReadinessAuditTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.auditor = _load_auditor()

    def test_allows_only_github_noreply_metadata(self) -> None:
        self.assertTrue(
            self.auditor._is_public_github_commit_identity(
                "GitHub", "noreply@github.com"
            )
        )
        self.assertTrue(
            self.auditor._is_public_github_commit_identity(
                "A public display name",
                "123456+public-owner@users.noreply.github.com",
            )
        )
        self.assertFalse(
            self.auditor._is_public_github_commit_identity(
                "A public display name", "person@example.com"
            )
        )
