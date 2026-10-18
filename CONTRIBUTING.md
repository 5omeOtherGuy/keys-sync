# Contributing

Thanks for helping. Small fixes (a typo, a clearer error, a missing check) are
welcome directly as a pull request. For larger changes, such as a new store
type, a new command or a change to default paths, open an issue first so we
can agree on the approach.

## Before you open a pull request

Run the gate; it must pass:

```sh
scripts/gate
```

It syntax-checks the Bash scripts and Python files, runs the unit tests and
scans the repository for secrets. To have it run before every push, enable the
repository's hooks once per checkout:

```sh
git config core.hooksPath .githooks
```

Rules for changes:

- Never commit a real key or a credential-shaped string, not even in tests.
  Use placeholder values such as `example-alpha-value`, as the existing tests
  do.
- Tests must be hermetic: they run the tool with a temporary `HOME` and
  `XDG_CONFIG_HOME` and a fake p1, never against your real stores.
- The tool never prints, logs or passes a key value on a command line. Keep it
  that way.
- The tool uses only the Python standard library (3.11 or newer). Please do
  not add dependencies.
- Record user-visible changes under `## [Unreleased]` in
  [CHANGELOG.md](CHANGELOG.md).

Use [Conventional Commits](https://www.conventionalcommits.org/en/v1.0.0/) for
the pull request title, for example `fix: keep the entry type on import`.
Pull requests are squash-merged. Changes to credential handling are reviewed
independently before they are merged.

## Triage and review

The project is in `pre-release`: issues and pull requests are triaged and
reviewed on a best-effort basis.

## Security issues

Please do not report vulnerabilities in public issues; see
[SECURITY.md](SECURITY.md).

## Licence

Contributions are licensed under the repository's [MIT licence](LICENSE).
There is no contributor licence agreement.
