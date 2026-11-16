"""Exact, deterministic lookup of numbered COLREG rules."""

from __future__ import annotations

import json
from functools import cache
from pathlib import Path
from typing import Any, Iterable


DEFAULT_CORPUS = Path(__file__).with_name("colregs.json")


class ColregsRepository:
    def __init__(self, path: Path = DEFAULT_CORPUS) -> None:
        with path.open(encoding="utf-8") as stream:
            corpus = json.load(stream)
        self.source_metadata = corpus.get("sources", [])
        self._rules: dict[int, dict[str, Any]] = {}
        for part in corpus.get("parts", []):
            candidates = list(part.get("rules", []))
            for section in part.get("sections", []):
                candidates.extend(section.get("rules", []))
            for rule in candidates:
                number = int(rule["number"])
                if number in self._rules:
                    raise ValueError(f"duplicate COLREG rule number {number}")
                self._rules[number] = rule

    def retrieve(self, numbers: Iterable[int]) -> list[dict[str, Any]]:
        requested = sorted(set(int(number) for number in numbers))
        missing = [number for number in requested if number not in self._rules]
        if missing:
            raise KeyError(f"unknown COLREG rule number(s): {missing}")
        return [self._rules[number] for number in requested]


@cache
def default_repository() -> ColregsRepository:
    return ColregsRepository()
