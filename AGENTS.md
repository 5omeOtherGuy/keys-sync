# AGENTS.md

## Profile
- Purpose: copy API keys from local key files into the JSON credential stores of agent CLIs (and p1 routes) without printing them
- Stage: pre-release (repository standard 2026-10-18)
- Gate: `scripts/gate` (CI job `gate`)
- Landing: direct to main after a green gate; agents land: yes
- Release: none; agents release: no
- Outside issues and PRs: agents triage; outside PRs merge: owner approves
- Sensitive paths: `keys_sync.py`, `tests/`, `tools/`, `scripts/gate`, `.githooks/`, `.agents/`, `.github/workflows/`, `.github/dependabot.yml`, `AGENTS.md`

Deliberate deviations from the standard:

- The gate has no format check, linter or type checker. The tool and its tests use only the Python standard library, and adding a tool such as ruff or mypy would be the repository's first dependency. The gate compiles every tracked Python file instead.
- `keys_sync.py` is credential handling as a whole, so in practice every code change lands through a reviewed PR (Landing step 4).

## Map

- `keys_sync.py`: the whole tool (single file, standard library only). Commands: sync (default), `--check`, `--dry-run`, `import SRC NAME`.
- `tests/test_keys_sync.py`: the test suite. It runs the tool as a subprocess with a temporary `HOME` and `XDG_CONFIG_HOME` and a fake p1. `test_real_p1_in_sandbox` runs only when `KEYS_SYNC_TEST_P1_BIN` names a p1 binary; the gate unsets it.
- `tools/secret-scan.sh`: the repository's secret scanner (tree, `--stdin`, `--names`; exit 0 clean, 1 hit, 2 error).
- `tools/hooks/pre-push`: scans every outgoing commit's patch and file names with the scanner, failing closed.
- `scripts/gate`: the gate. It runs `bash -n` on every tracked Bash script, compiles every tracked Python file, runs the unit tests and the tree secret scan, in a temporary `HOME` and XDG tree.
- `.githooks/pre-push`: the development hook. It runs the gate, then `tools/hooks/pre-push`.
- `.agents/setup`: orb setup. It checks for Python 3.11 or newer and enables `.githooks`.
- `.github/workflows/gate.yml`: CI. It runs `scripts/gate` in job `gate` on Python 3.11, the oldest supported version.

## Landing (pre-release)

1. Enable the hooks once per checkout: `git config core.hooksPath .githooks` (`.agents/setup` does it).
2. `git fetch origin && git rebase origin/main`, then `scripts/gate`.
3. `git push origin HEAD:main`. The pre-push hook runs the gate again. If the push is rejected, repeat from step 2.
4. Use a PR instead for public-interface changes (commands, options, exit codes, configuration keys, default paths, store formats, output that callers parse), CI or release configuration, changes to the sensitive paths in the profile, documentation changes that alter rules or executable instructions (for example the commands in `README.md` or the rules in `CONTRIBUTING.md` and `SECURITY.md`), work spanning several sessions, and anything another person sends. Squash-merge it once the `gate` run on the head commit concluded `success`: `gh pr merge <n> --squash --delete-branch --match-head-commit <sha>`. Credential-handling, CI, release, public-interface, sensitive-path changes and documentation changes that alter rules or executable instructions need an independent review first, recorded on the PR against the reviewed head; a later push needs a new one.
5. Record user-visible changes under `## [Unreleased]` in `CHANGELOG.md`.
6. Commit subjects follow Conventional Commits. Commit as `5omeOtherGuy <268151429+5omeOtherGuy@users.noreply.github.com>`. Personal addresses and `Co-authored-by` trailers carrying them must not reach this public repository, including squash commits (pass `--author-email 268151429+5omeOtherGuy@users.noreply.github.com` to `gh pr merge` for the owner's PRs).

## Rules

- Never print, log or pass a key value on a command line. Keys reach p1 only on stdin. Error messages name files and entries, never values.
- Keep the store locations: `~/.agents/keys` when it holds a `sync.toml`, else `$XDG_CONFIG_HOME/keys`; without an explicit `keys_dir`, a `sync.toml` inside either store reads keys only from its own directory. Changing them is a public-interface change.
- Sync writes JSON stores atomically with mode 600 and makes no backup copies (no `.bak` files). Do not reintroduce backups: they leave stale key copies behind.
- Paths starting with `~` resolve against `$HOME`, so a test with a temporary `HOME` cannot reach real stores. Keep every test hermetic: temporary `HOME` and XDG directories, the fake p1, no network, no real credentials.
- Never commit a credential-shaped literal, not even in tests. Use placeholder values such as `example-alpha-value`, or build credential-shaped vectors from parts at runtime.
- OAuth credentials (Claude Code, Codex, pi openai-codex) are out of scope; never read or write them.
- Stay standard-library only. A runtime or test dependency needs the owner's approval.

## Focused checks

- One test: `python3 -m unittest tests.test_keys_sync.SandboxTest.test_check_and_dry_run_are_read_only -v`.
- Everything except the gate's wrapping: `python3 -m unittest discover -s tests -v` and `bash tools/secret-scan.sh .`.
