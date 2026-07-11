"""
One-time migration: logicore.toml -> .env

Logicore retired TOML configuration in favor of a single `.env` file owned by
the user (see logicore/config/env.py). This helper reads an existing
`logicore.toml` (if present) and prints the equivalent `.env` keys so they can
be pasted into `.env`. It makes no changes on its own.

TOML section/key          ->  .env variable
[storage] root            ->  LOGICORE_STORAGE_ROOT
[storage] db_url          ->  LOGICORE_STORAGE_DB_URL
[storage] snapshot_enabled->  LOGICORE_STORAGE_SNAPSHOT_ENABLED
[storage] media_root      ->  LOGICORE_STORAGE_MEDIA_ROOT
[deployment] mode         ->  LOGICORE_MODE
[deployment] debug        ->  LOGICORE_DEBUG
[server] host             ->  LOGICORE_HOST
[server] port             ->  LOGICORE_PORT
[embedding] provider      ->  LOGICORE_EMBEDDING_PROVIDER
[embedding] model         ->  LOGICORE_EMBEDDING_MODEL
[embedding] ollama_url    ->  LOGICORE_OLLAMA_URL
[agent] default_provider  ->  LOGICORE_DEFAULT_PROVIDER
[agent] default_model     ->  LOGICORE_DEFAULT_MODEL
[runtime] max_turns       ->  LOGICORE_MAX_TURNS
... (runtime.* -> LOGICORE_* as documented in .env.example)
"""
from __future__ import annotations

import sys
from pathlib import Path


# TOML section/key -> .env variable name.
_MAPPING = {
    ("storage", "root"): "LOGICORE_STORAGE_ROOT",
    ("storage", "db_url"): "LOGICORE_STORAGE_DB_URL",
    ("storage", "snapshot_enabled"): "LOGICORE_STORAGE_SNAPSHOT_ENABLED",
    ("storage", "media_root"): "LOGICORE_STORAGE_MEDIA_ROOT",
    ("deployment", "mode"): "LOGICORE_MODE",
    ("deployment", "debug"): "LOGICORE_DEBUG",
    ("deployment", "environment"): "LOGICORE_ENVIRONMENT",
    ("server", "host"): "LOGICORE_HOST",
    ("server", "port"): "LOGICORE_PORT",
    ("server", "frontend_port"): "LOGICORE_FRONTEND_PORT",
    ("embedding", "provider"): "LOGICORE_EMBEDDING_PROVIDER",
    ("embedding", "model"): "LOGICORE_EMBEDDING_MODEL",
    ("embedding", "ollama_url"): "LOGICORE_OLLAMA_URL",
    ("agent", "default_provider"): "LOGICORE_DEFAULT_PROVIDER",
    ("agent", "default_model"): "LOGICORE_DEFAULT_MODEL",
    ("agent", "max_iterations"): "LOGICORE_MAX_TURNS",
}


def main() -> int:
    repo_root = Path(__file__).parent.parent
    toml_path = repo_root / "logicore.toml"
    if not toml_path.exists():
        print(f"No logicore.toml found at {toml_path}; nothing to migrate.")
        return 0

    try:
        import tomllib

        with open(toml_path, "rb") as f:
            data = tomllib.load(f)
    except ImportError:
        try:
            import toml as tomllib  # type: ignore

            with open(toml_path, "r", encoding="utf-8") as f:
                data = tomllib.load(f)
        except ImportError:
            print("Cannot read TOML (need Python 3.11+ or `pip install toml`).")
            return 1

    lines = ["# Migrated from logicore.toml", ""]
    found = False
    for (section, key), env_name in _MAPPING.items():
        if section in data and key in data[section]:
            lines.append(f"{env_name}={data[section][key]}")
            found = True

    if not found:
        print("logicore.toml found but contained no recognized keys.")
        return 0

    out = "\n".join(lines) + "\n"
    print(out)
    print("Paste the above into your .env file, then delete logicore.toml.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
