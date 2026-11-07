"""Exercise the installer with a signed disposable helper, never release binaries."""

import hashlib
import json
import os
from pathlib import Path
import selectors
import subprocess
import tempfile
import time
import unittest


ROOT = Path(__file__).resolve().parents[1]


class InstallTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.release_tmp = tempfile.TemporaryDirectory(prefix="mastermem-release-")
        cls.addClassCleanup(cls.release_tmp.cleanup)
        cls.release = Path(cls.release_tmp.name)
        key = cls.release / "signing-key"
        subprocess.run(
            ["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", str(key)],
            check=True,
        )
        public_key = key.with_suffix(".pub").read_text().split()[:2]
        cls.installer = (ROOT / "install.sh").read_text()
        signer = next(line for line in cls.installer.splitlines() if line.startswith("signer="))
        cls.installer = cls.installer.replace(
            signer, "signer='mastermem-release " + " ".join(public_key) + "'"
        )
        # A harmless stand-in for the signed release helper. No real login or token.
        helper = cls.release / "helper"
        helper.write_text(
            '#!/bin/sh\ncat >/dev/null\nprintf "%s\\n" "$@" >"$HOME/helper-args"\n'
            'exit "${TEST_HELPER_EXIT:-0}"\n'
        )
        digest = hashlib.sha256(helper.read_bytes()).hexdigest()
        sums = cls.release / "SHA256SUMS"
        sums.write_text(
            f"{digest}  plugins/mastermem/hooks/bin/linux-amd64/mastermem-hook\n"
        )
        subprocess.run(
            ["ssh-keygen", "-q", "-Y", "sign", "-f", str(key),
             "-n", "mastermem-release", str(sums)],
            check=True, capture_output=True,
        )

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="mastermem-install-")
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.home = self.base / "user home"
        self.home.mkdir()
        self.workspace = self.base / "workspace-one"
        self.workspace.mkdir()
        self.bin = self.base / "bin"
        self.bin.mkdir()
        # Replace only transport and platform detection. Signature and digest checks
        # use the real system tools, with a key generated exclusively for this test.
        self.command("uname", '#!/bin/sh\ncase "$1" in -s) echo Linux;; -m) echo x86_64;; esac\n')
        self.command("curl", '''#!/bin/sh
case "$3" in
  */SHA256SUMS) source=SHA256SUMS;;
  */SHA256SUMS.sig) source=SHA256SUMS.sig;;
  */mastermem-hook) source=helper;;
  *) exit 1;;
esac
cp "$TEST_RELEASE/$source" "$2"
if [ "$source" = helper ] && [ "${TEST_TAMPER:-0}" = 1 ]; then
  printf 'tampered' >>"$2"
fi
''')
        self.env = {
            **os.environ,
            "HOME": str(self.home),
            "PATH": str(self.bin) + os.pathsep + os.environ["PATH"],
            "TEST_RELEASE": str(self.release),
            "MASTERMEM_RELEASE_BASE": "https://release.invalid/v0.9.0",
            "MASTERMEM_TOKEN": "",
        }
        self.env.pop("CODEX_HOME", None)

    def command(self, name, body):
        path = self.bin / name
        path.write_text(body)
        path.chmod(0o755)

    def install(self, client="codex"):
        return subprocess.run(
            ["sh", "-s", "--", client, "https://mastermem.example"],
            input=self.installer, text=True, capture_output=True,
            cwd=self.workspace, env=self.env,
        )

    @property
    def skill(self):
        return self.home / ".agents/skills/mastermem/SKILL.md"

    def test_codex_installs_for_all_workspaces_and_is_repeatable(self):
        result = self.install()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(self.skill.is_file(), "mastermem must be installed at user scope")
        content = self.skill.read_text()
        self.assertIn("name: mastermem", content)
        self.assertIn("memory_search", content)
        self.assertEqual(list(self.workspace.iterdir()), [])

        self.workspace = self.base / "workspace-two"
        self.workspace.mkdir()
        result = self.install()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.skill.read_text(), content)
        self.assertEqual(list(self.workspace.iterdir()), [])

    def test_skill_is_shared_even_with_workspace_codex_home(self):
        self.env["CODEX_HOME"] = str(self.workspace / ".codex")
        result = self.install()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(self.skill.is_file())
        self.assertFalse((self.workspace / ".codex/skills").exists())

    def test_cursor_does_not_install_a_codex_skill(self):
        result = self.install("cursor")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(self.skill.exists())

    def test_failed_helper_does_not_install_a_skill(self):
        self.env["TEST_HELPER_EXIT"] = "42"
        result = self.install()
        self.assertEqual(result.returncode, 42, result.stderr)
        self.assertFalse(self.skill.exists())

    def test_tampered_download_is_never_executed(self):
        self.env["TEST_TAMPER"] = "1"
        result = self.install()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("does not match the signed release", result.stderr)
        self.assertFalse((self.home / "helper-args").exists())
        self.assertFalse(self.skill.exists())

    @unittest.skipUnless(os.environ.get("MASTERMEM_CODEX_E2E") == "1", "opt-in Codex app-server check")
    def test_codex_discovers_user_skill_in_two_workspaces(self):
        result = self.install()
        self.assertEqual(result.returncode, 0, result.stderr)
        codex_home = self.home / ".codex"
        codex_home.mkdir()
        second = self.base / "workspace-two"
        second.mkdir()
        process = subprocess.Popen(
            ["codex", "app-server", "--stdio"],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL, text=True,
            cwd=self.workspace,
            env={**self.env, "CODEX_HOME": str(codex_home)},
        )

        def rpc(ident, method, params):
            process.stdin.write(json.dumps({"id": ident, "method": method, "params": params}) + "\n")
            process.stdin.flush()
            deadline = time.monotonic() + 20
            with selectors.DefaultSelector() as selector:
                selector.register(process.stdout, selectors.EVENT_READ)
                while time.monotonic() < deadline:
                    if not selector.select(max(0, deadline - time.monotonic())):
                        break
                    line = process.stdout.readline()
                    self.assertTrue(line, "Codex app-server exited before responding")
                    answer = json.loads(line)
                    if answer.get("id") == ident:
                        self.assertNotIn("error", answer)
                        return answer["result"]
            self.fail(f"Codex app-server timed out: {method}")

        try:
            rpc(1, "initialize", {
                "clientInfo": {"name": "mastermem-install-test", "version": "1.0"},
                "capabilities": {"experimentalApi": True},
            })
            result = rpc(2, "skills/list", {
                "cwds": [str(self.workspace), str(second)], "forceReload": True,
            })
            self.assertEqual(len(result["data"]), 2)
            for entry in result["data"]:
                skills = [skill for skill in entry["skills"] if skill["name"] == "mastermem"]
                self.assertEqual(len(skills), 1)
                self.assertEqual(skills[0]["scope"], "user")
                self.assertEqual(Path(skills[0]["path"]), self.skill)
                self.assertTrue(skills[0]["enabled"])
        finally:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
            process.stdin.close()
            process.stdout.close()


if __name__ == "__main__":
    unittest.main()
