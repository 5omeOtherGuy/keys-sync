"""Integration tests with isolated HOME and XDG_CONFIG_HOME."""
import json
import os
import stat
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

TOOL = Path(__file__).resolve().parent.parent / "keys_sync.py"
REAL_P1 = Path(os.environ["KEYS_SYNC_TEST_P1_BIN"]) if os.environ.get("KEYS_SYNC_TEST_P1_BIN") else None
KEY_A = "example-alpha-value"
KEY_B = "example-beta-value"
FAKE_P1 = textwrap.dedent("""\
    #!/usr/bin/env python3
    import json, os, sys
    from pathlib import Path
    routes = os.environ.get("FAKE_P1_ROUTES", "").split(",")
    if sys.argv[1:] == ["login", "--list"]:
        for route in routes:
            print(route)
        sys.exit(0)
    if len(sys.argv) == 3 and sys.argv[1] == "login" and sys.argv[2] in routes:
        path = Path(os.environ["XDG_CONFIG_HOME"]) / "p1/auth.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        data = json.loads(path.read_text()) if path.exists() else {}
        data[sys.argv[2]] = {"type": "api_key", "key": sys.stdin.read().strip()}
        path.write_text(json.dumps(data))
        path.chmod(0o600)
        sys.exit(0)
    sys.exit(64)
""")


class SandboxTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="keys-sync-test-", dir=TOOL.parent)
        self.home = Path(self.tmp.name) / "home"
        self.keys = self.home / ".config/keys"
        self.keys.mkdir(parents=True)
        self.fake = Path(self.tmp.name) / "fake-p1"
        self.fake.write_text(FAKE_P1)
        self.fake.chmod(0o755)
        self.put_key("alpha", KEY_A)
        self.put_key("beta", KEY_B)
        self.configure('''\
[[key]]
name = "alpha"
pi = ["alpha-pi"]
opencode = ["alpha-oc"]
p1 = ["route-a"]
[[key]]
name = "beta"
opencode = ["beta-oc"]
p1 = ["route-missing"]
''')
        self.put_json(".pi/agent/auth.json", {
            "alpha-pi": {"type": "api_key", "key": "old"},
            "oauth": {"type": "oauth", "access": "dummy"},
        })

    def tearDown(self):
        self.tmp.cleanup()

    def env(self):
        env = {k: v for k, v in os.environ.items() if not k.startswith(("XDG_", "P1_"))}
        env.update(HOME=str(self.home), XDG_CONFIG_HOME=str(self.home / ".config"),
                   FAKE_P1_ROUTES="route-a")
        return env

    def run_cli(self, *args):
        result = subprocess.run([sys.executable, str(TOOL), *args], env=self.env(),
                                capture_output=True, text=True, timeout=120)
        for value in (KEY_A, KEY_B):
            self.assertNotIn(value, result.stdout + result.stderr)
        return result

    def configure(self, body, binary=None):
        (self.keys / "sync.toml").write_text(
            f'[p1]\nbin = "{binary or self.fake}"\n' + textwrap.dedent(body))

    def put_key(self, name, value):
        path = self.keys / f"{name}.key"
        path.write_text(value + "\n")
        path.chmod(0o600)

    def put_json(self, rel, data):
        path = self.home / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data))
        return path

    def get_json(self, rel):
        return json.loads((self.home / rel).read_text())

    def test_check_and_dry_run_are_read_only(self):
        before = (self.home / ".pi/agent/auth.json").read_text()
        check = self.run_cli("--check")
        self.assertEqual(check.returncode, 1)
        self.assertIn("pi:alpha-pi", check.stdout)
        self.assertIn("STALE", check.stdout)
        self.assertIn("no-route", check.stdout)
        self.assertNotIn("pi:oauth", check.stdout)
        self.assertIn("would-fix", self.run_cli("--dry-run").stdout)
        self.assertEqual((self.home / ".pi/agent/auth.json").read_text(), before)
        self.assertFalse((self.home / ".config/p1/auth.json").exists())

    def backups(self):
        return sorted(str(path.relative_to(self.home)) for path in self.home.rglob("*.bak*"))

    def test_sync_permissions_no_backups_rotation_and_import(self):
        result = self.run_cli()
        self.assertEqual(result.returncode, 0, result.stderr)
        pi = self.get_json(".pi/agent/auth.json")
        self.assertEqual(pi["alpha-pi"]["key"], KEY_A)
        self.assertEqual(pi["oauth"]["access"], "dummy")
        self.assertEqual(self.get_json(".config/p1/auth.json")["route-a"]["key"], KEY_A)
        self.assertEqual(self.get_json(".local/share/opencode/auth.json")["alpha-oc"]["type"], "api")
        path = self.home / ".pi/agent/auth.json"
        self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
        self.assertEqual(self.backups(), [])  # no copy of the replaced key is left behind
        self.assertEqual(self.run_cli("--check").returncode, 0)
        self.assertNotIn("fixed", self.run_cli().stdout)
        self.put_key("alpha", KEY_B)
        self.assertIn("STALE", self.run_cli("--check").stdout)
        self.assertEqual(self.run_cli().returncode, 0)
        self.assertEqual(self.get_json(".config/p1/auth.json")["route-a"]["key"], KEY_B)
        self.assertEqual(self.backups(), [])
        self.put_key("alpha", KEY_A)
        self.run_cli()
        self.assertEqual(self.run_cli("import", "pi:alpha-pi", "copied").returncode, 0)
        self.assertEqual((self.keys / "copied.key").read_text().strip(), KEY_A)
        self.assertNotEqual(self.run_cli("import", "pi:alpha-pi", "../escape").returncode, 0)

    def test_missing_key_duplicate_mapping_and_models_refs(self):
        (self.keys / "beta.key").unlink()
        self.assertIn("NO-KEYFILE", self.run_cli("--check").stdout)
        self.put_json(".pi/agent/models.json", {"providers": {
            "absent": {"apiKey": f"!cat {self.keys}/absent.key"}}})
        self.assertIn("MISSING-FILE", self.run_cli("--check").stdout)
        self.configure('''\
[[key]]
name = "alpha"
pi = ["shared"]
[[key]]
name = "beta"
pi = ["shared"]
''')
        self.assertIn("mapped by both", self.run_cli("--check").stderr)

    def test_custom_store_pointer_and_validation(self):
        self.configure('''\
[json_stores.other]
path = "~/other/store.json"
pointer = "/credentials/api~1key"
type = "custom"
[[key]]
name = "alpha"
targets = { other = ["account"] }
''')
        self.assertEqual(self.run_cli().returncode, 0)
        self.assertEqual(self.get_json("other/store.json")["account"],
                         {"type": "custom", "credentials": {"api/key": KEY_A}})
        self.assertEqual(self.run_cli("--check").returncode, 0)
        self.assertEqual(self.run_cli("import", "other:account", "copy").returncode, 0)
        self.configure('[[key]]\nname = "alpha"\ntargets = { missing = ["x"] }\n')
        self.assertIn("unknown JSON store", self.run_cli("--check").stderr)

    def test_agents_keys_store_is_preferred_and_stores_never_mix(self):
        agents = self.home / ".agents/keys"
        agents.mkdir(parents=True)
        (agents / "alpha.key").write_text(KEY_B + "\n")
        (agents / "sync.toml").write_text('[[key]]\nname = "alpha"\npi = ["alpha-pi"]\n')
        result = self.run_cli()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("is ignored", result.stderr)
        self.assertEqual(self.get_json(".pi/agent/auth.json")["alpha-pi"]["key"], KEY_B)
        self.assertFalse((self.home / ".local/share/opencode/auth.json").exists())
        # an explicit XDG config reads the XDG keys, not the preferred store's
        self.assertEqual(self.run_cli("--config", str(self.keys / "sync.toml")).returncode, 0)
        self.assertEqual(self.get_json(".pi/agent/auth.json")["alpha-pi"]["key"], KEY_A)
        # a config elsewhere without keys_dir reads the preferred store
        other = Path(self.tmp.name) / "other.toml"
        other.write_text('[[key]]\nname = "alpha"\npi = ["alpha-pi"]\n')
        self.assertIn("STALE", self.run_cli("--check", "--config", str(other)).stdout)
        (agents / "sync.toml").unlink()
        fallback = self.run_cli("--check")
        self.assertNotIn("is ignored", fallback.stderr)
        self.assertRegex(fallback.stdout, r"pi:alpha-pi\s+ok")

    def test_p1_named_config_dirs_and_multiple_stores(self):
        config_dir = Path(self.tmp.name) / "routes"
        config_dir.mkdir()
        self.configure(f'''\
[p1.config_dirs]
other = "{config_dir}"
[[p1.stores]]
name = "primary"
[[p1.stores]]
name = "secondary"
xdg_config_home = "~/separate"
[[key]]
name = "alpha"
p1 = ["route-a@other", "route-missing"]
''')
        out = self.run_cli()
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertIn("p1-primary:route-missing", out.stdout)
        self.assertIn("no-route", out.stdout)
        self.assertEqual(self.get_json("separate/p1/auth.json")["route-a"]["key"], KEY_A)
        self.configure('[[key]]\nname = "alpha"\np1 = ["route-a@unknown"]\n')
        self.assertIn("no [p1.config_dirs] entry", self.run_cli("--check").stderr)


@unittest.skipUnless(REAL_P1 is not None and REAL_P1.is_file(),
                     "set KEYS_SYNC_TEST_P1_BIN to an existing p1 binary for sandbox integration tests")
class RealP1Test(unittest.TestCase):
    def setUp(self):
        self.sb = SandboxTest(methodName="test_check_and_dry_run_are_read_only")
        self.sb.setUp()
        cfg = Path(self.sb.tmp.name) / "p1cfg"
        (cfg / "environments").mkdir(parents=True)
        (cfg / "routes").mkdir()
        (cfg / "routes/ks-test-route.toml").write_text(textwrap.dedent("""\
            id = "ks-test-route"
            origin_route = "openai-chat/ks-test-route"
            adapter = "openai-chat"
            endpoint = "https://example.invalid/v1/chat/completions"
            [credential]
            kind = "api-key"
            store_only = true
            env = "KS_TEST_ROUTE_KEY"
            borrow = []
            [adapter_settings]
            dialect = "thinking-with-reasoning-alias"
        """))
        self.sb.configure(f'[[key]]\nname = "alpha"\np1 = ["ks-test-route"]\np1_config_dir = "{cfg}"\n',
                          binary=REAL_P1)

    def tearDown(self):
        self.sb.tearDown()

    def test_real_p1_in_sandbox(self):
        result = self.sb.run_cli()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.sb.get_json(".config/p1/auth.json")["ks-test-route"]["key"], KEY_A)
        self.assertEqual(self.sb.run_cli("--check").returncode, 0)


if __name__ == "__main__":
    unittest.main()
