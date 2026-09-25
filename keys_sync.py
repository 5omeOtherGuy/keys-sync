#!/usr/bin/env python3
"""keys-sync — copy API keys from local files into configured credential stores.

Canonical store: ``$XDG_CONFIG_HOME/keys/<name>.key`` (one key per file, mode 600),
mapping ``$XDG_CONFIG_HOME/keys/sync.toml``. JSON targets are configurable; pi and
opencode have built-in defaults. Optional p1 routes use `p1 login` (key on stdin).

Commands:
  keys-sync --check            report ok / STALE / MISSING / no-route per target; exit 1 if anything is stale
  keys-sync [--dry-run]        make every target match its key file (backs up each store it changes)
  keys-sync import SRC NAME    copy one existing store entry into the keys directory
                               SRC = STORE:ENTRY | p1-<store>:ROUTE

Never prints, logs or passes a key value on a command line. Paths starting with ``~`` resolve
against $HOME, so a test run with HOME=<tmp> cannot reach the real stores. OAuth grants
(Claude Code, Codex, pi openai-codex) are out of scope and never touched.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

JSON_STORES = {
    "pi": ("~/.pi/agent/auth.json", "api_key"),
    "opencode": ("~/.local/share/opencode/auth.json", "api"),
}


def home() -> Path:
    value = os.environ.get("HOME")
    if not value:
        sys.exit("keys-sync: HOME is not set")
    return Path(value)


def expand(path: str) -> Path:
    if path == "~" or path.startswith("~/"):
        return home() / path[2:]
    return Path(path)


def config_home() -> Path:
    return Path(os.environ["XDG_CONFIG_HOME"]) if os.environ.get("XDG_CONFIG_HOME") else home() / ".config"


def read_key(path: Path) -> str:
    return path.read_text().strip()


# --- configuration ---------------------------------------------------------------------------


@dataclass
class P1Store:
    name: str
    config_home: Path  # the XDG_CONFIG_HOME p1 runs with; its store is <config_home>/p1/auth.json

    @property
    def auth_path(self) -> Path:
        return self.config_home / "p1" / "auth.json"


@dataclass
class KeySpec:
    name: str
    targets: dict[str, list[str]] = field(default_factory=dict)
    p1: list[str] = field(default_factory=list)  # "ROUTE" or "ROUTE@DIRNAME" ([p1.config_dirs])
    p1_config_dir: Path | None = None  # P1_CONFIG_DIR for this key's plain "ROUTE" entries

    def p1_targets(self, config_dirs: dict[str, Path]) -> list[tuple[str, Path | None]]:
        out = []
        for item in self.p1:
            route, _, dirname = item.partition("@")
            if dirname and dirname not in config_dirs:
                raise SystemExit(f"keys-sync: key '{self.name}': no [p1.config_dirs] entry '{dirname}'")
            out.append((route, config_dirs[dirname] if dirname else self.p1_config_dir))
        return out


@dataclass
class Config:
    keys_dir: Path
    p1_bin: Path | None
    p1_stores: list[P1Store]
    keys: list[KeySpec]
    json_stores: dict[str, tuple[Path, str, tuple[str, ...]]]
    p1_config_dirs: dict[str, Path] = field(default_factory=dict)

    def key_file(self, spec: KeySpec) -> Path:
        return self.keys_dir / f"{spec.name}.key"


def load_config(path: Path) -> Config:
    raw = tomllib.loads(path.read_text())
    keys_dir = expand(raw["keys_dir"]) if "keys_dir" in raw else config_home() / "keys"
    p1 = raw.get("p1", {})
    p1_bin = expand(p1["bin"]) if "bin" in p1 else None
    stores = []
    for store in p1.get("stores", [{"name": "default"}]):
        store_home = expand(store["xdg_config_home"]) if "xdg_config_home" in store else config_home()
        stores.append(P1Store(store["name"], store_home))
    config_dirs = {name: expand(value) for name, value in p1.get("config_dirs", {}).items()}
    json_stores = {name: (expand(default), kind, ("key",)) for name, (default, kind) in JSON_STORES.items()}
    for name, value in raw.get("stores", {}).items():
        if name not in json_stores:
            raise SystemExit(f"keys-sync: unknown store '{name}' in {path}")
        _, kind, pointer = json_stores[name]
        json_stores[name] = (expand(value), kind, pointer)
    for name, store in raw.get("json_stores", {}).items():
        if not isinstance(store, dict) or "path" not in store:
            raise SystemExit(f"keys-sync: json_stores.{name} needs a path")
        pointer = store.get("pointer", "/key")
        if not isinstance(pointer, str) or not pointer.startswith("/") or pointer == "/":
            raise SystemExit(f"keys-sync: json_stores.{name} needs a JSON pointer to a field")
        parts = pointer[1:].split("/")
        if any(not part or "~" in part.replace("~0", "").replace("~1", "") for part in parts):
            raise SystemExit(f"keys-sync: invalid pointer for json_stores.{name}")
        json_stores[name] = (expand(store["path"]), store.get("type", "api_key"),
                             tuple(part.replace("~1", "/").replace("~0", "~") for part in parts))
    keys, seen = [], set()
    for entry in raw.get("key", []):
        unknown = set(entry) - {"name", "pi", "opencode", "targets", "p1", "p1_config_dir", "note"}
        if unknown:
            raise SystemExit(f"keys-sync: key '{entry.get('name')}' has unknown fields {sorted(unknown)}")
        if entry["name"] in seen:
            raise SystemExit(f"keys-sync: key '{entry['name']}' is listed twice")
        seen.add(entry["name"])
        targets = {name: list(entries) for name, entries in entry.get("targets", {}).items()}
        for name in ("pi", "opencode"):
            if name in entry:
                targets.setdefault(name, []).extend(entry[name])
        for name in targets:
            if name not in json_stores:
                raise SystemExit(f"keys-sync: key '{entry['name']}': unknown JSON store '{name}'")
        keys.append(
            KeySpec(
                name=entry["name"],
                targets=targets,
                p1=list(entry.get("p1", [])),
                p1_config_dir=expand(entry["p1_config_dir"]) if "p1_config_dir" in entry else None,
            )
        )
    for store in json_stores:
        owners: dict[str, str] = {}
        for spec in keys:
            for entry in spec.targets.get(store, []):
                if entry in owners:
                    raise SystemExit(f"keys-sync: {store}:{entry} is mapped by both '{owners[entry]}' and '{spec.name}'")
                owners[entry] = spec.name
    cfg = Config(keys_dir, p1_bin, stores, keys, json_stores, config_dirs)
    for spec in keys:
        spec.p1_targets(config_dirs)  # validates every @DIRNAME now
    return cfg


# --- stores ----------------------------------------------------------------------------------


def load_json(path: Path) -> dict:
    if not path.exists():
        return {}
    return json.loads(path.read_text() or "{}")


def write_json_atomic(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as handle:
            json.dump(data, handle, indent=2)
            handle.write("\n")
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


class Backups:
    """Back up each store file once per run, before its first change."""

    def __init__(self, dry_run: bool):
        self.stamp = time.strftime("%Y%m%d-%H%M%S")
        self.done: set[Path] = set()
        self.dry_run = dry_run

    def before_change(self, path: Path) -> None:
        if path in self.done or self.dry_run or not path.exists():
            self.done.add(path)
            return
        backup = path.with_name(f"{path.name}.bak-{self.stamp}")
        shutil.copy2(path, backup)
        os.chmod(backup, 0o600)
        self.done.add(path)


def p1_env(store: P1Store, config_dir: Path | None) -> dict:
    env = dict(os.environ)
    env["XDG_CONFIG_HOME"] = str(store.config_home)
    if config_dir is not None:
        env["P1_CONFIG_DIR"] = str(config_dir)
    else:
        env.pop("P1_CONFIG_DIR", None)
    return env


_route_cache: dict[tuple, set[str]] = {}


def p1_routes(cfg: Config, store: P1Store, config_dir: Path | None) -> set[str]:
    cache_key = (str(store.config_home), str(config_dir))
    if cache_key not in _route_cache:
        if cfg.p1_bin is None or not cfg.p1_bin.exists():
            _route_cache[cache_key] = set()
        else:
            result = subprocess.run(
                [str(cfg.p1_bin), "login", "--list"],
                env=p1_env(store, config_dir),
                capture_output=True,
                text=True,
                timeout=60,
            )
            _route_cache[cache_key] = {line.split()[0] for line in result.stdout.splitlines() if line.strip()}
    return _route_cache[cache_key]


# --- check / sync ----------------------------------------------------------------------------


@dataclass
class Row:
    key: str
    target: str
    status: str  # ok | STALE | MISSING | NO-KEYFILE | no-route | fixed | would-fix | FAILED


def entry_value(store: dict, entry: str, pointer: tuple[str, ...] = ("key",)) -> str | None:
    value = store.get(entry)
    for part in pointer:
        if not isinstance(value, dict):
            return None
        value = value.get(part)
    return value.strip() if isinstance(value, str) else None


def set_entry(store: dict, entry: str, pointer: tuple[str, ...], kind: str, value: str) -> None:
    if not isinstance(store.get(entry), dict):
        store[entry] = {"type": kind}
    record = store[entry]
    for part in pointer[:-1]:
        if not isinstance(record.get(part), dict):
            record[part] = {}
        record = record[part]
    record[pointer[-1]] = value


def run(cfg: Config, mode: str) -> list[Row]:
    """mode: 'check' | 'dry-run' | 'sync'."""
    rows: list[Row] = []
    backups = Backups(dry_run=(mode != "sync"))
    json_data = {name: load_json(path) for name, (path, _, _) in cfg.json_stores.items()}
    json_dirty: set[str] = set()

    for spec in cfg.keys:
        key_path = cfg.key_file(spec)
        value = read_key(key_path) if key_path.exists() else None

        for store, entries in spec.targets.items():
            _, kind, pointer = cfg.json_stores[store]
            for entry in entries:
                target = f"{store}:{entry}"
                if value is None:
                    rows.append(Row(spec.name, target, "NO-KEYFILE"))
                    continue
                current = entry_value(json_data[store], entry, pointer)
                if current == value:
                    rows.append(Row(spec.name, target, "ok"))
                    continue
                status = "MISSING" if current is None else "STALE"
                if mode == "check":
                    rows.append(Row(spec.name, target, status))
                    continue
                rows.append(Row(spec.name, target, f"would-fix ({status.lower()})" if mode == "dry-run" else "fixed"))
                if mode == "sync":
                    set_entry(json_data[store], entry, pointer, kind, value)
                    json_dirty.add(store)

        for route, config_dir in spec.p1_targets(cfg.p1_config_dirs):
            for store in cfg.p1_stores:
                target = f"p1-{store.name}:{route}"
                if value is None:
                    rows.append(Row(spec.name, target, "NO-KEYFILE"))
                    continue
                current = entry_value(load_json(store.auth_path), route)
                if current == value:
                    rows.append(Row(spec.name, target, "ok"))
                    continue
                if route not in p1_routes(cfg, store, config_dir):
                    rows.append(Row(spec.name, target, "no-route"))
                    continue
                status = "MISSING" if current is None else "STALE"
                if mode == "check":
                    rows.append(Row(spec.name, target, status))
                elif mode == "dry-run":
                    rows.append(Row(spec.name, target, f"would-fix ({status.lower()})"))
                else:
                    backups.before_change(store.auth_path)
                    result = subprocess.run(
                        [str(cfg.p1_bin), "login", route],
                        input=value + "\n",
                        env=p1_env(store, config_dir),
                        capture_output=True,
                        text=True,
                        timeout=60,
                    )
                    ok = result.returncode == 0 and entry_value(load_json(store.auth_path), route) == value
                    rows.append(Row(spec.name, target, "fixed" if ok else "FAILED"))

    for store in sorted(json_dirty):
        backups.before_change(cfg.json_stores[store][0])
        write_json_atomic(cfg.json_stores[store][0], json_data[store])
    return rows


def unmanaged(cfg: Config) -> list[str]:
    """API-key entries that no key in sync.toml maps (OAuth grants are not API keys and are skipped)."""
    mapped = {(s, e) for spec in cfg.keys for s, entries in spec.targets.items() for e in entries}
    routes = {r for spec in cfg.keys for r, _ in spec.p1_targets(cfg.p1_config_dirs)}
    out = []
    for store, (path, _, pointer) in cfg.json_stores.items():
        for entry, record in load_json(path).items():
            if entry_value({entry: record}, entry, pointer) is not None and (store, entry) not in mapped:
                out.append(f"{store}:{entry}")
    for store in cfg.p1_stores:
        for route, record in load_json(store.auth_path).items():
            if isinstance(record, dict) and "key" in record and route not in routes:
                out.append(f"p1-{store.name}:{route}")
    return out


def file_refs(cfg: Config) -> list[tuple[str, str]]:
    """pi models.json providers whose apiKey is `!cat <file>`: does the file exist?"""
    models = home() / ".pi/agent/models.json"
    out = []
    for name, provider in load_json(models).get("providers", {}).items():
        api_key = provider.get("apiKey", "")
        if isinstance(api_key, str) and api_key.startswith("!cat "):
            ref = expand(api_key[5:].strip())
            out.append((f"pi-models:{name}", "ok" if ref.exists() else "MISSING-FILE"))
    return out


# --- import ----------------------------------------------------------------------------------


def import_entry(cfg: Config, source: str, name: str) -> str:
    if "/" in name or not name or name.startswith("."):
        raise SystemExit("keys-sync: NAME must be a plain file stem")
    store, _, entry = source.partition(":")
    if store in cfg.json_stores:
        path, _, pointer = cfg.json_stores[store]
        value = entry_value(load_json(path), entry, pointer)
    elif store.startswith("p1-"):
        matches = [s for s in cfg.p1_stores if s.name == store[3:]]
        if not matches:
            raise SystemExit(f"keys-sync: unknown p1 store '{store[3:]}'")
        value = entry_value(load_json(matches[0].auth_path), entry)
    else:
        raise SystemExit("keys-sync: SRC must be STORE:ENTRY or p1-<store>:ROUTE")
    if value is None:
        raise SystemExit(f"keys-sync: {source} holds no API key")
    cfg.keys_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(cfg.keys_dir, 0o700)
    dest = cfg.keys_dir / f"{name}.key"
    if dest.exists():
        if read_key(dest) == value:
            return f"{dest.name}: already holds the key from {source}"
        raise SystemExit(f"keys-sync: {dest} exists with a different key; not overwritten")
    fd = os.open(dest, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as handle:
        handle.write(value + "\n")
    return f"{dest.name}: imported from {source}"


# --- CLI -------------------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="keys-sync", description=__doc__.split("\n\n")[0])
    parser.add_argument("--config", help="TOML mapping (default: $XDG_CONFIG_HOME/keys/sync.toml)")
    parser.add_argument("--check", action="store_true", help="report only; exit 1 if any target is stale")
    parser.add_argument("--dry-run", action="store_true", help="show what a sync would change")
    parser.add_argument("command", nargs="*", help="`import SRC NAME`")
    args = parser.parse_args(argv)
    os.umask(0o077)
    cfg = load_config(expand(args.config) if args.config else config_home() / "keys" / "sync.toml")

    if args.command:
        if args.command[0] != "import" or len(args.command) != 3:
            parser.error("the only command is: import SRC NAME")
        print(import_entry(cfg, args.command[1], args.command[2]))
        return 0

    mode = "check" if args.check else "dry-run" if args.dry_run else "sync"
    rows = run(cfg, mode)
    width = max((len(r.key) for r in rows), default=4)
    twidth = max((len(r.target) for r in rows), default=6)
    for row in rows:
        print(f"{row.key:<{width}}  {row.target:<{twidth}}  {row.status}")
    for target, status in file_refs(cfg):
        print(f"{'(file ref)':<{width}}  {target:<{twidth}}  {status}")
    extra = unmanaged(cfg)
    if extra:
        print(f"unmanaged API-key entries (not in sync.toml): {', '.join(extra)}")
    bad = {"STALE", "MISSING", "NO-KEYFILE", "FAILED", "MISSING-FILE"}
    failed = any(r.status in bad for r in rows) or any(s in bad for _, s in file_refs(cfg))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
