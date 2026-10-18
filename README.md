# keys-sync

`keys-sync` copies local API-key files into JSON credential stores for tools you
configure. It has built-in defaults for pi and opencode and optional support for
p1 routes. It does not print key values.

## Install

Requires Python 3.11 or newer (standard library only). Optional: `p1` for p1
routes. Clone the repository, then run `python3 keys_sync.py --help` from it.
To run it from elsewhere, use the absolute path to `keys_sync.py` or put the
script on your `PATH`. No pip packages are required.

## Example

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

## Limits and safety

Only API keys are handled; OAuth credentials are not synced. JSON stores must
be top-level objects containing entries that are objects; pointers traverse
object keys, not arrays. pi `models.json` `!cat` file references are checked
for existence. `--check` is read-only; `--dry-run` does not write. Sync writes
JSON stores atomically with mode 600 and leaves no backup copies, so no old key
lingers in a `.bak` file; it does not encrypt keys. Protect the source
directory, and inspect your target tool's store format before use. A missing
p1 route is reported `no-route`, not created.

## Tests and local scan

```sh
python3 -m unittest discover -s tests -v
bash tools/secret-scan.sh .
```

Tests use a temporary HOME and XDG_CONFIG_HOME and a fake p1. Real-p1 sandbox
tests skip unless `KEYS_SYNC_TEST_P1_BIN` points to a locally installed binary.
To install the optional pre-push scan hook in this worktree:

```sh
cp tools/hooks/pre-push "$(git rev-parse --git-path hooks)/pre-push"
chmod +x "$(git rev-parse --git-path hooks)/pre-push"
```
