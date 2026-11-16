import json

INPUT = "colregs.json"
OUTPUT = "colregs_compact.txt"

with open(INPUT, "r", encoding="utf-8") as f:
    data = json.load(f)

rules = {}

def walk(obj):
    if isinstance(obj, dict):
        n = obj.get("number")
        title = obj.get("title")
        text = obj.get("text_verbatim")

        if (
            isinstance(n, int)
            and 1 <= n <= 41
            and isinstance(title, str)
            and title
            and isinstance(text, str)
            and text
            and n not in rules
        ):
            rules[n] = {
                "title": title,
                "text": text.strip()
            }

        for value in obj.values():
            walk(value)

    elif isinstance(obj, list):
        for item in obj:
            walk(item)

walk(data)

with open(OUTPUT, "w", encoding="utf-8") as f:
    for number in sorted(rules):
        rule = rules[number]
        f.write(
            f"RULE {number} — {rule['title']}\n"
            f"{rule['text']}\n\n"
        )

print(f"Wrote {len(rules)} rules to {OUTPUT}")