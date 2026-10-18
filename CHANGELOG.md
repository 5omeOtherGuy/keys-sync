# Changelog

All notable changes to this project are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions will
follow [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Changed

- Without `--config`, `keys-sync` reads `~/.agents/keys/sync.toml` when it
  exists and `$XDG_CONFIG_HOME/keys/sync.toml` otherwise; when both exist it
  uses the first and says so on standard error. Without an explicit
  `keys_dir`, a `sync.toml` inside either directory reads keys only from its
  own directory
  ([#2](https://github.com/5omeOtherGuy/keys-sync/pull/2)).
- Sync no longer writes timestamped `.bak` copies of credential stores (JSON
  stores or p1). Backups made by older versions are not removed
  ([#2](https://github.com/5omeOtherGuy/keys-sync/pull/2)).
