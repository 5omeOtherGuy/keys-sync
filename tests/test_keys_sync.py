"""keys-sync tests. Every test runs with HOME (and XDG_CONFIG_HOME) pointed at a temp directory,
so neither the tool nor the p1 binary it calls can reach the real credential stores."""

import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TOOL = ROOT / "keys_sync.py"
REAL_P1 = Path(os.environ["KEYS_SYNC_TEST_P1_BIN"]) if os.environ.get("KEYS_SYNC_TEST_P1_BIN") else None
REAL_HOME = Path.home()

SECRETS = {
    "alpha": "oc_sk_ALPHA_secret_value_000000000000000000000",
    "beta": "sk_BETA_secret_value_1111111111111111111111111",
    "gamma": "sk_GAMMA_secret_value_22222222222222222222222",
}

FAKE_P1 = textwrap.dedent(
    """\
    #!/usr/bin/env python3
    # Stand-in for p1: `login --list` prints FAKE_P1_ROUTES; `login ROUTE` stores stdin in
    # $XDG_CONFIG_HOME/p1/auth.json the way p1 does ({"type": "api_key", "key": ...}).
    import json, os, sys
    from pathlib import Path
    routes = os.environ.get("FAKE_P1_ROUTES", "").split(",")
    if sys.argv[1:] == ["login", "--list"]:
        for r in routes:
            print(f"{r}  api-key  p1 store")
        sys.exit(0)
    if len(sys.argv) == 3 and sys.argv[1] == "login":
        route = sys.argv[2]
        if route not in routes:
            sys.exit(2)
        path = Path(os.environ["XDG_CONFIG_HOME"]) / "p1" / "auth.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        data = json.loads(path.read_text()) if path.exists() else {}
        data[route] = {"type": "api_key", "key": sys.stdin.read().strip()}
        path.write_text(json.dumps(data))
        os.chmod(path, 0o600)
        print(f"stored for {route}")
        sys.exit(0)
    sys.exit(64)
    """
)


class Sandbox:
    def __init__(self):
        self.dir = Path(tempfile.mkdtemp(prefix="keys-sync-test-"))
        self.home = self.dir / "home"
        self.keys = self.home / ".agents/keys"
        self.keys.mkdir(parents=True, mode=0o700)
        self.p1 = self.dir / "fake-p1"
        self.p1.write_text(FAKE_P1)
        self.p1.chmod(0o755)
        self.routes = "route-a,route-b"

    def env(self):
        env = {k: v for k, v in os.environ.items() if not k.startswith(("XDG_", "P1_"))}
        env["HOME"] = str(self.home)
        env["XDG_CONFIG_HOME"] = str(self.home / "should-not-be-used")
        env["FAKE_P1_ROUTES"] = self.routes
        return env

    def write_key(self, name, value, newline=True):
        path = self.keys / f"{name}.key"
        path.write_text(value + ("\n" if newline else ""))
        path.chmod(0o600)

    def write_json(self, rel, data):
        path = self.home / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data))
        path.chmod(0o600)
        return path

    def read_json(self, rel):
        return json.loads((self.home / rel).read_text())

    def config(self, body, p1_bin=None):
        (self.keys / "sync.toml").write_text(
            textwrap.dedent(
                f"""\
                [p1]
                bin = "{p1_bin or self.p1}"
                [[p1.stores]]
                name = "default"
                [[p1.stores]]
                name = "trial"
                xdg_config_home = "~/trial"
                """
            )
            + textwrap.dedent(body)
        )

    def run(self, *args):
        result = subprocess.run(
            [sys.executable, str(TOOL), *args], env=self.env(), capture_output=True, text=True, timeout=120
        )
        for secret in SECRETS.values():
            assert secret not in result.stdout and secret not in result.stderr, "a key value was printed"
        return result

    def backups(self):
        return sorted(p.name for p in self.home.rglob("*.bak-*"))

    def cleanup(self):
        shutil.rmtree(self.dir)


KEYS_BODY = """\
[[key]]
name = "alpha"
pi = ["alpha-pi"]
opencode = ["alpha-oc"]
p1 = ["route-a"]

[[key]]
name = "beta"
opencode = ["beta-oc"]
p1 = ["route-b", "route-missing"]
"""


class KeysSyncTest(unittest.TestCase):
    def setUp(self):
        self.sb = Sandbox()
        self.sb.write_key("alpha", SECRETS["alpha"], newline=False)
        self.sb.write_key("beta", SECRETS["beta"])
        self.sb.config(KEYS_BODY)
        self.sb.write_json(".pi/agent/auth.json", {
            "alpha-pi": {"type": "api_key", "key": "stale"},
            "oauth-thing": {"type": "oauth", "access": "x", "refresh": "y"},
        })
        self.sb.write_json(".local/share/opencode/auth.json", {
            "beta-oc": {"type": "api", "key": SECRETS["beta"] + "\n"},
            "stray": {"type": "api", "key": SECRETS["gamma"]},
        })

    def tearDown(self):
        self.sb.cleanup()

    def statuses(self, out):
        rows = {}
        for line in out.splitlines():
            parts = line.split()
            if len(parts) >= 3 and ":" in parts[1]:
                rows[(parts[0], parts[1])] = " ".join(parts[2:])
        return rows

    def test_check_reports_each_target_without_writing(self):
        result = self.sb.run("--check")
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        rows = self.statuses(result.stdout)
        self.assertEqual(rows[("alpha", "pi:alpha-pi")], "STALE")
        self.assertEqual(rows[("alpha", "opencode:alpha-oc")], "MISSING")
        self.assertEqual(rows[("beta", "opencode:beta-oc")], "ok")  # whitespace does not count
        self.assertEqual(rows[("alpha", "p1-default:route-a")], "MISSING")
        self.assertEqual(rows[("beta", "p1-trial:route-missing")], "no-route")
        self.assertIn("unmanaged API-key entries", result.stdout)
        self.assertIn("opencode:stray", result.stdout)
        self.assertNotIn("oauth-thing", result.stdout)  # OAuth grants are out of scope
        self.assertEqual(self.sb.backups(), [])
        self.assertEqual(self.sb.read_json(".pi/agent/auth.json")["alpha-pi"]["key"], "stale")

    def test_dry_run_changes_nothing(self):
        before = (self.sb.home / ".pi/agent/auth.json").read_text()
        result = self.sb.run("--dry-run")
        self.assertIn("would-fix (stale)", result.stdout)
        self.assertEqual((self.sb.home / ".pi/agent/auth.json").read_text(), before)
        self.assertFalse((self.sb.home / ".config/p1/auth.json").exists())
        self.assertEqual(self.sb.backups(), [])

    def test_sync_fixes_everything_and_is_idempotent(self):
        result = self.sb.run()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        pi = self.sb.read_json(".pi/agent/auth.json")
        self.assertEqual(pi["alpha-pi"], {"type": "api_key", "key": SECRETS["alpha"]})
        self.assertEqual(pi["oauth-thing"], {"type": "oauth", "access": "x", "refresh": "y"})
        oc = self.sb.read_json(".local/share/opencode/auth.json")
        self.assertEqual(oc["alpha-oc"], {"type": "api", "key": SECRETS["alpha"]})
        self.assertEqual(oc["stray"]["key"], SECRETS["gamma"])  # unmanaged entries are left alone
        for rel in (".config/p1/auth.json", "trial/p1/auth.json"):
            store = self.sb.read_json(rel)
            self.assertEqual(store["route-a"]["key"], SECRETS["alpha"])
            self.assertEqual(store["route-b"]["key"], SECRETS["beta"])
        self.assertFalse((self.sb.home / "should-not-be-used").exists())
        for rel in (".pi/agent/auth.json", ".local/share/opencode/auth.json", ".config/p1/auth.json"):
            self.assertEqual(stat.S_IMODE((self.sb.home / rel).stat().st_mode), 0o600)
        # a changed store is replaced atomically; no copy of an old key is left behind
        self.assertEqual(self.sb.backups(), [])

        again = self.sb.run()
        self.assertEqual(again.returncode, 0)
        self.assertNotIn("fixed", again.stdout)
        self.assertEqual(self.sb.backups(), [])
        self.assertEqual(self.sb.run("--check").returncode, 0)

    def test_check_flags_stale_after_key_rotation(self):
        self.sb.run()
        self.sb.write_key("alpha", SECRETS["gamma"])
        rows = self.statuses(self.sb.run("--check").stdout)
        self.assertEqual(rows[("alpha", "pi:alpha-pi")], "STALE")
        self.assertEqual(rows[("alpha", "p1-trial:route-a")], "STALE")
        self.assertEqual(rows[("beta", "p1-trial:route-b")], "ok")

    def test_missing_key_file(self):
        (self.sb.keys / "beta.key").unlink()
        result = self.sb.run()
        self.assertEqual(result.returncode, 1)
        self.assertEqual(self.statuses(result.stdout)[("beta", "opencode:beta-oc")], "NO-KEYFILE")

    def test_import_copies_store_entry_into_key_file(self):
        result = self.sb.run("import", "opencode:stray", "gamma")
        self.assertEqual(result.returncode, 0, result.stderr)
        path = self.sb.keys / "gamma.key"
        self.assertEqual(path.read_text().strip(), SECRETS["gamma"])
        self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
        self.assertEqual(self.sb.run("import", "opencode:stray", "gamma").returncode, 0)  # same value: no-op
        conflict = self.sb.run("import", "opencode:beta-oc", "gamma")
        self.assertNotEqual(conflict.returncode, 0)
        self.assertEqual(path.read_text().strip(), SECRETS["gamma"])
        self.assertNotEqual(self.sb.run("import", "opencode:nope", "x").returncode, 0)
        self.assertNotEqual(self.sb.run("import", "opencode:stray", "../escape").returncode, 0)

    def test_duplicate_mapping_is_rejected(self):
        self.sb.config(KEYS_BODY + '\n[[key]]\nname = "gamma"\npi = ["alpha-pi"]\n')
        result = self.sb.run("--check")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("mapped by both", result.stderr)

    def test_models_json_file_refs(self):
        self.sb.write_json(".pi/agent/models.json", {"providers": {
            "good": {"apiKey": f"!cat {self.sb.keys}/alpha.key"},
            "bad": {"apiKey": f"!cat {self.sb.keys}/nothing.key"},
        }})
        out = self.sb.run("--check").stdout
        self.assertRegex(out, r"pi-models:good\s+ok")
        self.assertRegex(out, r"pi-models:bad\s+MISSING-FILE")


@unittest.skipUnless(REAL_P1 is not None and REAL_P1.exists(), "set KEYS_SYNC_TEST_P1_BIN to a built p1 binary")
class RealP1Test(unittest.TestCase):
    """The real p1 binary, confined to the sandbox HOME, a sandbox P1_CONFIG_DIR and sandbox stores."""

    def setUp(self):
        self.sb = Sandbox()
        cfg = self.sb.dir / "p1cfg"
        (cfg / "environments").mkdir(parents=True)
        (cfg / "routes").mkdir()
        (cfg / "routes/ks-test-route.toml").write_text(textwrap.dedent("""\
            id           = "ks-test-route"
            origin_route = "openai-chat/ks-test-route"
            adapter      = "openai-chat"
            endpoint     = "https://example.invalid/v1/chat/completions"
            [credential]
            kind       = "api-key"
            store_only = true
            env        = "KS_TEST_ROUTE_KEY"
            borrow     = []
            [adapter_settings]
            dialect = "thinking-with-reasoning-alias"
            """))
        self.sb.write_key("alpha", SECRETS["alpha"])
        self.sb.config(f'[[key]]\nname = "alpha"\np1 = ["ks-test-route"]\np1_config_dir = "{cfg}"\n', p1_bin=REAL_P1)

    def tearDown(self):
        self.sb.cleanup()

    def test_route_at_named_config_dir(self):
        cfg = self.sb.dir / "p1cfg"
        body = f'[p1.config_dirs]\nws = "{cfg}"\n\n[[key]]\nname = "alpha"\np1 = ["ks-test-route@ws", "ks-absent"]\n'
        self.sb.config("", p1_bin=REAL_P1)
        text = (self.sb.keys / "sync.toml").read_text().replace("[[p1.stores]]", body.split("\n\n")[0] + "\n[[p1.stores]]", 1)
        (self.sb.keys / "sync.toml").write_text(text + "\n" + body.split("\n\n")[1])
        result = self.sb.run()
        self.assertEqual(self.sb.read_json(".config/p1/auth.json")["ks-test-route"]["key"], SECRETS["alpha"])
        self.assertRegex(result.stdout, r"p1-default:ks-absent\s+no-route")
        self.sb.config('[[key]]\nname = "alpha"\np1 = ["x@nowhere"]\n', p1_bin=REAL_P1)
        self.assertIn("no [p1.config_dirs] entry", self.sb.run("--check").stderr)

    def test_sync_through_real_p1_login(self):
        real_store = REAL_HOME / ".config/p1/auth.json"
        before = real_store.stat().st_mtime_ns if real_store.exists() else None
        result = self.sb.run()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        for rel in (".config/p1/auth.json", "trial/p1/auth.json"):
            self.assertEqual(self.sb.read_json(rel)["ks-test-route"]["key"], SECRETS["alpha"])
        self.assertEqual(self.sb.run("--check").returncode, 0)
        after = real_store.stat().st_mtime_ns if real_store.exists() else None
        self.assertEqual(before, after, "the real p1 store was touched")


if __name__ == "__main__":
    unittest.main()
