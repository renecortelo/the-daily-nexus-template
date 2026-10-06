import importlib.util
import json
import tomllib
from pathlib import Path
from unittest import TestCase
from unittest.mock import patch

spec = importlib.util.spec_from_file_location(
    "dependency_audit", Path("scripts/audit-dependencies.py")
)
audit_module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(audit_module)


class ToolchainTests(TestCase):
    def test_firebase_version_matches_installer_publisher_and_lock(self):
        manifest = json.loads(Path("package.json").read_text())
        lock = json.loads(Path("package-lock.json").read_text())
        version = manifest["devDependencies"]["firebase-tools"]
        self.assertEqual(version, lock["packages"]["node_modules/firebase-tools"]["version"])
        self.assertIn(f'EXPECTED_FIREBASE_TOOLS = "{version}"',
                      Path("scripts/firebase-clone-deploy.cjs").read_text())
        setup = Path("scripts/setup-windows.ps1").read_text()
        self.assertIn("npm ci", setup)
        self.assertIn("package-lock.json", setup)
        self.assertIn("sync --frozen", setup)

    def test_antigravity_is_pinned_verified_and_does_not_inherit_customizations(self):
        agent = Path("config/antigravity-agent.md").read_text()
        for setting in ("inheritCustomizations: false", "inheritMcp: false",
                        "commandExecutionPolicy: off", "subagent: false"):
            self.assertIn(setting, agent)
        workflow = Path(".github/workflows/private-cloud-runner.yml").read_text()
        install = Path("scripts/install-antigravity.ps1").read_text()
        self.assertIn("/download/1.3.0/", workflow)
        self.assertIn("$Version = '1.3.0'", install)
        self.assertIn("sha256sum --check --strict", workflow)
        self.assertIn("Get-FileHash", install)

    def test_audit_includes_audio_and_all_supported_python_resolutions(self):
        packages = audit_module.locked_packages(Path("uv.lock"))
        self.assertIn(("torch", "2.13.0"), packages)
        self.assertIn(("kokoro", "0.9.4"), packages)
        self.assertTrue(any(name == "pillow" for name, _ in packages))
        with Path("uv.lock").open("rb") as handle:
            lock = tomllib.load(handle)
        self.assertTrue(all("hash" in artifact for package in lock["package"]
                            for artifact in package.get("wheels", [])))

    def test_audit_fails_closed_on_service_errors_without_printing_details(self):
        with patch.object(audit_module, "audit", side_effect=ValueError("private details")):
            with patch("builtins.print") as output:
                self.assertEqual(audit_module.main(), 2)
        self.assertNotIn("private details", str(output.call_args_list))

    def test_audit_rejects_incomplete_results(self):
        with patch.object(audit_module.urllib.request, "urlopen") as request:
            request.return_value.__enter__.return_value.read.return_value = b'{"results": []}'
            with self.assertRaises(ValueError):
                audit_module.audit([("pillow", "12.3.0")])
