import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from unittest import TestCase


class FirebaseCloneDeployTests(TestCase):
    def test_release_wrapper_never_prints_raw_exception_details(self):
        source = Path("scripts/firebase-clone-deploy.cjs").read_text(encoding="utf-8")
        self.assertNotIn("DEBUG CLONE STACK", source)
        self.assertNotIn("error.stack", source)
        self.assertNotIn("error.message", source)

    def test_server_side_clone_reuses_media_and_retires_selected_paths(self):
        node = shutil.which("node")
        if not node:
            self.skipTest("Node.js is not installed")
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as name:
            root = Path(name)
            modules = root / "node_modules" / "firebase-tools"
            (modules / "lib" / "hosting").mkdir(parents=True)
            (modules / "lib" / "deploy" / "hosting").mkdir(parents=True)
            (modules / "package.json").write_text(
                json.dumps({"version": "15.24.0"}),
                encoding="utf-8",
            )
            log_path = root / "firebase-stub-log.jsonl"
            recorder = (
                "const fs=require('node:fs');"
                "function r(v){fs.appendFileSync(process.env.TDN_TEST_LOG,"
                "JSON.stringify(v)+'\\n');}"
            )
            (modules / "lib" / "hosting" / "api.js").write_text(
                recorder
                + "exports.getChannel=async()=>({release:{version:{name:"
                "'sites/example-private-project/versions/current'}}});"
                "exports.cloneVersion=async()=>{r(['clone']);return {response:{name:"
                "'sites/example-private-project/versions/cloned'}}};"
                "exports.createVersion=async()=>{throw new Error('unexpected create')};"
                "exports.updateVersion=async(...a)=>{r(['update',a[1],a[2].status])};"
                "exports.createRelease=async(...a)=>{r(['release',a[1],a[2]])};",
                encoding="utf-8",
            )
            (modules / "lib" / "deploy" / "hosting" / "uploader.js").write_text(
                recorder
                + "exports.Uploader=class{constructor(v){this.v=v}"
                "async start(){r(['upload',this.v.version,this.v.files])}};",
                encoding="utf-8",
            )
            (modules / "lib" / "apiv2.js").write_text(
                recorder
                + "exports.setRefreshToken=()=>r(['authenticated']);"
                "exports.Client=class{async post(p,b){r(['populate',p,b]);"
                "return {body:{}}}};",
                encoding="utf-8",
            )
            (modules / "lib" / "api.js").write_text(
                "exports.hostingApiOrigin=()=>"
                "'https://firebasehosting.googleapis.com';",
                encoding="utf-8",
            )
            public = root / "public"
            public.mkdir()
            (public / "index.html").write_text("private app", encoding="utf-8")
            removal = root / "remove.json"
            retired = (
                "/p/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa/audio/"
                "2026-07-01-retired.mp3"
            )
            removal.write_text(json.dumps([retired]), encoding="utf-8")
            environment = dict(os.environ)
            environment.update(
                {
                    "FIREBASE_TOKEN": "test-firebase-refresh-token",
                    "NODE_PATH": str(root / "node_modules"),
                    "TDN_TEST_LOG": str(log_path),
                }
            )
            completed = subprocess.run(
                [
                    node,
                    "scripts/firebase-clone-deploy.cjs",
                    "--project",
                    "example-private-project",
                    "--public",
                    str(public),
                    "--remove-manifest",
                    str(removal),
                ],
                cwd=Path.cwd(),
                env=environment,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(0, completed.returncode, completed.stderr)
            self.assertNotIn(environment["FIREBASE_TOKEN"], completed.stdout)
            operations = [
                json.loads(line)
                for line in log_path.read_text(encoding="utf-8").splitlines()
            ]
            self.assertIn(["authenticated"], operations)
            self.assertIn(["clone"], operations)
            self.assertTrue(
                any(
                    item[0] == "populate"
                    and item[2]["files"].get(retired) == ""
                    for item in operations
                )
            )
            self.assertTrue(any(item[0] == "upload" for item in operations))
            self.assertTrue(any(item[0] == "release" for item in operations))

    def test_clone_failure_falls_back_to_creating_new_version(self):
        node = shutil.which("node")
        if not node:
            self.skipTest("Node.js is not installed")
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as name:
            root = Path(name)
            modules = root / "node_modules" / "firebase-tools"
            (modules / "lib" / "hosting").mkdir(parents=True)
            (modules / "lib" / "deploy" / "hosting").mkdir(parents=True)
            (modules / "package.json").write_text(
                json.dumps({"version": "15.24.0"}),
                encoding="utf-8",
            )
            log_path = root / "firebase-stub-log.jsonl"
            recorder = (
                "const fs=require('node:fs');"
                "function r(v){fs.appendFileSync(process.env.TDN_TEST_LOG,"
                "JSON.stringify(v)+'\\n');}"
            )
            (modules / "lib" / "hosting" / "api.js").write_text(
                recorder
                + "exports.getChannel=async()=>({release:{version:{name:"
                "'sites/example-private-project/versions/stale'}}});"
                "exports.cloneVersion=async()=>{throw new Error('clone failed')};"
                "exports.createVersion=async()=>{r(['created']);return "
                "'sites/example-private-project/versions/fallback'};"
                "exports.updateVersion=async(...a)=>{r(['update',a[1],a[2].status])};"
                "exports.createRelease=async(...a)=>{r(['release',a[1],a[2]])};",
                encoding="utf-8",
            )
            (modules / "lib" / "deploy" / "hosting" / "uploader.js").write_text(
                recorder
                + "exports.Uploader=class{constructor(v){this.v=v}"
                "async start(){r(['upload',this.v.version,this.v.files])}};",
                encoding="utf-8",
            )
            (modules / "lib" / "apiv2.js").write_text(
                recorder
                + "exports.setRefreshToken=()=>r(['authenticated']);"
                "exports.Client=class{async post(p,b){r(['populate',p,b]);"
                "return {body:{}}}};",
                encoding="utf-8",
            )
            (modules / "lib" / "api.js").write_text(
                "exports.hostingApiOrigin=()=>"
                "'https://firebasehosting.googleapis.com';",
                encoding="utf-8",
            )
            public = root / "public"
            public.mkdir()
            (public / "index.html").write_text("private app", encoding="utf-8")
            removal = root / "remove.json"
            removal.write_text(json.dumps([]), encoding="utf-8")
            environment = dict(os.environ)
            environment.update(
                {
                    "FIREBASE_TOKEN": "test-firebase-refresh-token",
                    "NODE_PATH": str(root / "node_modules"),
                    "TDN_TEST_LOG": str(log_path),
                }
            )
            completed = subprocess.run(
                [
                    node,
                    "scripts/firebase-clone-deploy.cjs",
                    "--project",
                    "example-private-project",
                    "--public",
                    str(public),
                    "--remove-manifest",
                    str(removal),
                ],
                cwd=Path.cwd(),
                env=environment,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(0, completed.returncode, completed.stderr)
            operations = [
                json.loads(line)
                for line in log_path.read_text(encoding="utf-8").splitlines()
            ]
            self.assertIn(["created"], operations)
            self.assertTrue(any(item[0] == "release" for item in operations))
