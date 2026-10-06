import re
from pathlib import Path
from unittest import TestCase


class CloudWorkflowTests(TestCase):
    def test_public_readiness_audits_the_pr_head_not_githubs_merge_commit(self):
        workflow = Path(".github/workflows/public-readiness.yml").read_text(
            encoding="utf-8"
        )

        self.assertIn(
            "ref: ${{ github.event.pull_request.head.sha || github.sha }}",
            workflow,
        )
        self.assertIn("--history --strict-metadata", workflow)

    def test_generation_secrets_exist_only_after_dependency_installation(self):
        workflow = Path(
            ".github/workflows/private-cloud-runner.yml"
        ).read_text(encoding="utf-8")
        probe_prepare = workflow.index("Materialize the queue-only credential")
        probe = workflow.index("Check the owner-only queue")
        probe_cleanup = workflow.index(
            "Remove the queue credential before installing dependencies"
        )
        install = workflow.index("Install the local generation runtime")
        generation_prepare = workflow.index(
            "Materialize encrypted generation credentials"
        )
        deploy_rules = workflow.index("Deploy the owner-only Firestore rules")
        generate = workflow.index("Generate and publish a protected queue batch")
        final_cleanup = workflow.index("Remove temporary credentials")
        self.assertLess(
            probe_prepare,
            probe,
        )
        self.assertLess(probe, probe_cleanup)
        self.assertLess(probe_cleanup, install)
        self.assertLess(install, generation_prepare)
        self.assertLess(generation_prepare, deploy_rules)
        self.assertLess(deploy_rules, generate)
        self.assertLess(generation_prepare, generate)
        self.assertLess(generate, final_cleanup)
        before_install = workflow[:install]
        for secret_name in (
            "TDN_GMAIL_TOKEN_JSON",
            "TDN_ANTIGRAVITY_KEYRING_JSON",
            "TDN_FIREBASE_DEPLOY_TOKEN",
            "TDN_FIREBASE_SECRET_PATH",
        ):
            self.assertNotIn(secret_name, before_install)

    def test_external_actions_are_commit_pinned_and_public_artifacts_are_absent(self):
        workflow = Path(
            ".github/workflows/private-cloud-runner.yml"
        ).read_text(encoding="utf-8")
        action_references = re.findall(r"^\s*uses:\s*(\S+)\s*$", workflow, re.MULTILINE)
        self.assertTrue(action_references)
        for reference in action_references:
            self.assertRegex(reference, r"^[^@]+@[0-9a-f]{40}$")
        self.assertNotIn("upload-artifact", workflow)
        self.assertIn("github.repository_visibility == 'private'", workflow)
        self.assertIn("useG1Credits", Path("src/audiodigest/cloud_runtime.py").read_text())

    def test_linux_audio_runtime_is_explicit_and_cpu_only(self):
        workflow = Path(
            ".github/workflows/private-cloud-runner.yml"
        ).read_text(encoding="utf-8")
        self.assertIn("gnome-keyring", workflow)
        self.assertIn("libsecret-tools", workflow)
        self.assertIn("ffmpeg", workflow)
        manifest = Path("pyproject.toml").read_text(encoding="utf-8")
        lock = Path("uv.lock").read_text(encoding="utf-8")
        self.assertIn("https://download.pytorch.org/whl/cpu", manifest)
        self.assertIn('"torch==2.13.0"', manifest)
        self.assertIn('version = "2.13.0+cpu"', lock)
        self.assertIn("uv sync --frozen --extra audio", workflow)
        self.assertNotIn('pip install --disable-pip-version-check ".[audio]"', workflow)

    def test_antigravity_runs_through_the_ephemeral_keyring_wrapper(self):
        workflow = Path(
            ".github/workflows/private-cloud-runner.yml"
        ).read_text(encoding="utf-8")
        wrapper = Path("scripts/run-private-cloud.sh").read_text(encoding="utf-8")
        self.assertIn("bash scripts/run-private-cloud.sh", workflow)
        self.assertNotIn("\n  schedule:", workflow)
        self.assertNotIn('cron: "17,47 * * * *"', workflow)
        self.assertIn("clock_schedule_id", workflow)
        self.assertIn("clock_schedule_date", workflow)
        self.assertIn("clock_source", workflow)
        self.assertIn("TDN_SCHEDULE_ID", workflow)
        self.assertIn("TDN_SCHEDULE_DATE", workflow)
        self.assertIn("--schedule-id", wrapper)
        self.assertIn("--schedule-date", wrapper)
        self.assertIn("dbus-run-session", wrapper)
        self.assertIn("gnome-keyring-daemon --unlock", wrapper)
        self.assertIn("service gemini username antigravity", wrapper)
        self.assertIn("secret-tool clear", wrapper)
        self.assertLess(
            wrapper.index('export XDG_RUNTIME_DIR='),
            wrapper.index("exec dbus-run-session"),
        )
        self.assertIn("daily-nexus-ephemeral-runner", wrapper)
        self.assertNotIn('eval "$keyring_environment"', wrapper)
        self.assertIn("batch_limit=1", wrapper)
        self.assertIn("batch_budget_seconds", wrapper)

    def test_generic_queue_continuation_uses_only_the_ephemeral_actions_token(self):
        workflow = Path(
            ".github/workflows/private-cloud-runner.yml"
        ).read_text(encoding="utf-8")
        cleanup = workflow.index("Remove temporary credentials")
        continuation_prepare = workflow.index(
            "Materialize the continuation queue-only credential"
        )
        continuation_probe = workflow.index("Check for a remaining generic queue batch")
        continuation_cleanup = workflow.index("Remove continuation queue credential")
        dispatch = workflow.index("Dispatch a remaining generic queue batch")

        self.assertLess(cleanup, continuation_prepare)
        self.assertLess(continuation_prepare, continuation_probe)
        self.assertLess(continuation_probe, continuation_cleanup)
        self.assertLess(continuation_cleanup, dispatch)
        self.assertIn("actions: write", workflow)
        self.assertIn("GH_TOKEN: ${{ github.token }}", workflow)
        self.assertIn("gh api --method POST", workflow)
        self.assertIn("/dispatches", workflow)
        self.assertIn("always() && steps.probe.outputs.run == 'true'", workflow)
        self.assertIn("always() && steps.continuation_prepare.outcome == 'success'", workflow)
        self.assertIn("steps.continuation_probe.outputs.run == 'true'", workflow)
        self.assertIn("steps.generate.outcome == 'success'", workflow)
        self.assertIn("--phase probe", workflow[continuation_prepare:dispatch])
        self.assertIn(
            "PYTHONPATH=src python -m audiodigest.cloud_probe",
            workflow[continuation_probe:continuation_cleanup],
        )

    def test_terminal_task_failure_can_dispatch_continuation_before_reporting(self):
        workflow = Path(
            ".github/workflows/private-cloud-runner.yml"
        ).read_text(encoding="utf-8")
        dispatch = workflow[workflow.index("Dispatch a remaining generic queue batch"):]
        report = workflow.index("Report a terminal generation failure")
        self.assertIn("steps.generate.outcome == 'success'", dispatch)
        self.assertIn("steps.generate.outputs.task_failed == 'true'", workflow)
        self.assertLess(workflow.index("Dispatch a remaining generic queue batch"), report)

    def test_wrapper_distinguishes_terminal_task_failure_from_infrastructure_failure(self):
        wrapper = Path("scripts/run-private-cloud.sh").read_text(encoding="utf-8")
        self.assertIn("status == 20", wrapper)
        self.assertIn("task_failed=true", wrapper)
        self.assertIn("return \"$status\"", wrapper)
        self.assertIn("task_failed=%s", wrapper)

    def test_observability_keeps_the_hard_limit_and_filters_child_output(self):
        workflow = Path(".github/workflows/private-cloud-runner.yml").read_text()
        wrapper = Path("scripts/run-private-cloud.sh").read_text()
        self.assertIn("timeout-minutes: 60", workflow)
        self.assertIn("PIPESTATUS", wrapper)
        self.assertIn("python -u -m audiodigest.progress", wrapper)
        self.assertNotIn('output="$("$@" 2>&1)"', wrapper)

    def test_clock_runs_continue_with_a_fresh_generic_dispatch(self):
        workflow = Path(
            ".github/workflows/private-cloud-runner.yml"
        ).read_text(encoding="utf-8")
        continuation_marker = "Materialize the continuation queue-only credential"
        continuation = workflow[workflow.index(continuation_marker):]
        self.assertNotIn("inputs.clock_schedule_id == ''", continuation)
        self.assertIn("a fresh generic workflow without clock inputs", workflow)
        self.assertIn('-f "ref=${GH_REF}"', continuation)
        self.assertNotIn("clock_schedule_id", continuation)

    def test_private_hosting_release_reports_only_a_safe_phase_on_failure(self):
        release = Path("scripts/firebase-clone-deploy.cjs").read_text(encoding="utf-8")
        self.assertIn("let releasePhase", release)
        self.assertIn("Private Firebase release failed (${name} // ${releasePhase})", release)
        self.assertNotIn("error.message", release)

    def test_cloud_clock_url_is_release_only_and_csp_is_exact(self):
        workflow = Path(
            ".github/workflows/private-cloud-runner.yml"
        ).read_text(encoding="utf-8")
        release = Path("scripts/firebase-clone-deploy.cjs").read_text(encoding="utf-8")
        publisher = Path("src/audiodigest/publisher.py").read_text(encoding="utf-8")
        self.assertIn("TDN_CLOUD_CLOCK_URL", workflow)
        self.assertGreater(
            workflow.index("TDN_CLOUD_CLOCK_URL"),
            workflow.index("Install the local generation runtime"),
        )
        self.assertIn("cloudClockOrigin", release)
        self.assertIn("connect-src ${clockOrigin}", release)
        self.assertIn("_write_cloud_clock_config", publisher)
        self.assertIn("https://{parsed.hostname}", publisher)
