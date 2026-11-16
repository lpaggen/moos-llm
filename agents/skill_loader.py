from functools import cache
from pathlib import Path

SKILLS_DIR = Path(__file__).parent / "skills"


@cache
def load_skill(name: str) -> str:
    path = SKILLS_DIR / f"{name}.md"
    if not path.is_file():
        raise FileNotFoundError(f"Skill not found: {name}")

    text = path.read_text(encoding="utf-8")
    body = text

    if text.startswith("---\n"):
        _, separator, body = text.partition("---\n")
        _, separator, body = body.partition("---\n")
        if not separator:
            raise ValueError(f"Invalid frontmatter in {path}")

    body = body.strip()
    if not body:
        raise ValueError(f"Skill has no instructions: {path}")

    return body
