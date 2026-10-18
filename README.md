# keys-sync

One source of truth for API keys on this machine: `~/.agents/keys/<name>.key` (mode 600) plus the
register `~/.agents/keys/KEYS.md`. `~/.agents/keys/sync.toml` maps each key file to the tool-store
entries it feeds (pi `auth.json`, opencode `auth.json`, p1 routes in every configured p1 store).

    keys-sync --check           # read-only; ok / STALE / MISSING / NO-KEYFILE / no-route; exit 1 if anything is stale
    keys-sync --dry-run         # what a sync would change
    keys-sync                   # sync: rewrites each changed store atomically, keeps 0600, leaves no backup copies
    keys-sync import SRC NAME   # copy a store entry (pi:E, opencode:E, p1-<store>:ROUTE) into ~/.agents/keys/NAME.key

Rotating a key: replace the file, run `keys-sync`. JSON stores are rewritten atomically (other entries and
fields untouched); p1 stores go through `p1 login ROUTE` with the key on stdin. p1 entries may name a
route in another config dir as `ROUTE@DIRNAME` (`[p1.config_dirs]`). A route p1 does not know yet is
reported `no-route` and filled automatically once its route file exists.

Never prints a key value. OAuth grants (Claude Code, Codex, pi openai-codex) and `~/.config/brain-tools`
keys are out of scope.

Tests (temp HOME, fake p1, plus one test with the real p1 binary in a sandbox):

    python3 -m unittest discover -s tests
