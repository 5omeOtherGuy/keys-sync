# keys-sync

Copies API keys from local key files into the credential stores of your agent CLIs.

Status: pre-release. Others may try it, but nothing is promised yet: configuration, commands and output can change without notice.

[![gate](https://github.com/5omeOtherGuy/keys-sync/actions/workflows/gate.yml/badge.svg?branch=main)](https://github.com/5omeOtherGuy/keys-sync/actions/workflows/gate.yml)
[![licence: MIT](https://img.shields.io/badge/licence-MIT-blue.svg)](LICENSE)

## What it does

You keep each API key in one file in a key directory. `keys-sync` copies
those keys into the JSON credential stores of the tools you configure, so
rotating a key means editing one file and running one command. It has
built-in defaults for pi and opencode and optional support for p1 routes.
It never prints key values.

### Limits and safety

Only API keys are handled; OAuth credentials are not synced. JSON stores must
be top-level objects containing entries that are objects; pointers traverse
object keys, not arrays. pi `models.json` `!cat` file references are checked
for existence. `--check` is read-only; `--dry-run` does not write. Sync writes
JSON stores atomically with mode 600 and creates no backup copies (backups made
by older versions are not removed); it does not encrypt keys. Protect the source
directory, and inspect your target tool's store format before use. A missing
p1 route is reported `no-route`, not created.

## Install

Requires Python 3.11 or newer (standard library only). Optional: `p1` for p1
routes. Clone the repository, then run `python3 keys_sync.py --help` from it.
To run it from elsewhere, use the absolute path to `keys_sync.py` or put the
script on your `PATH`. No pip packages are required.

## Usage

Set `XDG_CONFIG_HOME` to your configuration directory (defaults to
`$HOME/.config`). Create `$XDG_CONFIG_HOME/keys` with mode 700, put your
API key in `$XDG_CONFIG_HOME/keys/example.key` with mode 600, and create
`$XDG_CONFIG_HOME/keys/sync.toml`:

```toml
[json_stores.mytool]
path = "~/.local/share/mytool/credentials.json"
pointer = "/key"
type = "api_key"

[[key]]
name = "example"
targets = { mytool = ["example-provider"] }
```

Run `python3 keys_sync.py --check` to see whether the destination is stale
(exit 1), `python3 keys_sync.py --dry-run` to preview, then
`python3 keys_sync.py` to sync. The destination becomes
`{"example-provider": {"type": "api_key", "key": "..."}}`. Rotating the key
file and running sync updates the entry. The actual key is never shown.

## Configuration

`--config PATH` selects the TOML file. Without it, `keys-sync` uses
`~/.agents/keys/sync.toml` if that file exists and
`$XDG_CONFIG_HOME/keys/sync.toml` otherwise; when both exist it uses the first
and says so on stderr. `keys_dir` selects the source directory. Without it,
a configuration file in one of those two directories reads keys from its own
directory; any other configuration file reads them from the directory chosen
as above.
Paths beginning with `~/` resolve using `$HOME`; other relative paths resolve
against the current directory. If `XDG_CONFIG_HOME` is unset, the default
configuration directory is `$HOME/.config`.

Each `[[key]]` has a `name` (source `<keys_dir>/<name>.key`) and a `targets`
map from JSON-store names to lists of top-level entry names. A `[json_stores.NAME]`
section specifies `path`, optional `pointer` (JSON pointer to a field inside
each entry, default `/key`, object keys only), and optional `type` for new
entries (default `api_key`). For example `/credentials/api~1key` addresses
`credentials["api/key"]`. Existing fields and types are preserved. pi and
opencode are available without definitions, at `~/.pi/agent/auth.json`
(`type = "api_key"`) and `~/.local/share/opencode/auth.json` (`type = "api"`).
Override either using `[json_stores.pi]` or `[json_stores.opencode]`. The older
`pi = ["entry"]` and `opencode = ["entry"]` key fields and `[stores]` path
overrides also work.

Optional p1 support: set `[p1] bin = "/path/to/p1"` and use `p1 = ["ROUTE"]`
on a key. `[[p1.stores]] name = "default"` is the default store; each store
can set `xdg_config_home` (default `$XDG_CONFIG_HOME`). `[p1.config_dirs]` maps
names to directories; use `ROUTE@NAME` to select a config directory or
`p1_config_dir` on a key for its plain routes. p1 routes must already exist.
`keys-sync` passes keys on stdin to `p1 login`; it does not write p1 stores
directly.

`python3 keys_sync.py import mytool:example-provider copied` copies an existing
JSON entry into `<keys_dir>/copied.key` (or use `p1-STORE:ROUTE` for p1).
Imports refuse to overwrite a different key.

## Compatibility

- Python 3.11 or newer, standard library only. The gate runs on Python 3.11
  on Ubuntu 24.04 (last verified 2026-10-18).
- Linux. Other POSIX systems are untested. The tool relies on POSIX file
  modes (600 for stores and key files), so Windows is not supported.

## Development

Run the gate before every change; CI runs the same command:

```sh
scripts/gate
```

It syntax-checks the Bash scripts and Python files, runs the unit tests and
scans the repository for secrets, in a temporary `HOME` and
`XDG_CONFIG_HOME`. Run one test with:

```sh
python3 -m unittest tests.test_keys_sync.SandboxTest.test_check_and_dry_run_are_read_only -v
```

Tests use a fake p1. Real-p1 sandbox tests skip unless
`KEYS_SYNC_TEST_P1_BIN` points to a locally installed binary; the gate
always skips them.

To run the gate and a secret scan of the outgoing commits before every push,
enable the repository's hooks once per checkout:

```sh
git config core.hooksPath .githooks
```

See [CONTRIBUTING.md](CONTRIBUTING.md) for how to propose changes.

## Contributing, support and security

- Bugs and feature requests: [issues](https://github.com/5omeOtherGuy/keys-sync/issues).
- Contributions: [CONTRIBUTING.md](CONTRIBUTING.md).
- Vulnerabilities: report privately as described in [SECURITY.md](SECURITY.md).

## Licence

[MIT](LICENSE).
