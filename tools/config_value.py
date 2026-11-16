#!/usr/bin/env python3
"""Print one value from the repository's project.ini."""

from __future__ import annotations

import argparse
import configparser
import os
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG = PROJECT_ROOT / "project.ini"

# for python scripts
_MISSING = object()


def get(section: str, key: str, fallback=_MISSING):
    config = configparser.ConfigParser(interpolation=None)
    config_path = Path(os.environ.get("MOOS_LLM_CONFIG", DEFAULT_CONFIG))
    if not config.read(config_path, encoding="utf-8"):
        raise FileNotFoundError(f"configuration file not found: {config_path}")

    try:
        return config[section][key]
    except KeyError:
        if fallback is not _MISSING:
            return fallback
        raise LookupError(f"missing configuration value: [{section}] {key}")


# for bash to read .ini
def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("section")
    parser.add_argument("key")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    args = parser.parse_args()

    config = configparser.ConfigParser(interpolation=None)
    if not config.read(args.config, encoding="utf-8"):
        parser.error(f"configuration file not found: {args.config}")

    try:
        print(config[args.section][args.key])
    except KeyError:
        parser.error(f"missing configuration value: [{args.section}] {args.key}")


if __name__ == "__main__":
    main()
