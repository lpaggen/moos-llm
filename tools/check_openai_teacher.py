#!/usr/bin/env python3
"""Fail-fast authentication and model-access check for a paid teacher campaign."""

from __future__ import annotations

import argparse
import json
import os


def validate_key_shape(value: str | None) -> str:
    key = (value or "").strip()
    lowered = key.lower()
    if (
        len(key) < 20
        or "your-key" in lowered
        or "your_key" in lowered
        or "actual-key" in lowered
        or key in {"...", "<key>", "<api-key>"}
    ):
        raise ValueError(
            "OPENAI_API_KEY is missing or still contains an example placeholder"
        )
    return key


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", required=True)
    args = parser.parse_args()
    try:
        key = validate_key_shape(os.environ.get("OPENAI_API_KEY"))
    except ValueError as error:
        parser.error(str(error))

    from openai import OpenAI

    try:
        model = OpenAI(api_key=key).models.retrieve(args.model)
    except Exception as error:
        parser.error(
            f"OpenAI authentication/model preflight failed for {args.model}: "
            f"{type(error).__name__}: {error}"
        )
    print(json.dumps({"authenticated": True, "model": model.id}))


if __name__ == "__main__":
    main()
