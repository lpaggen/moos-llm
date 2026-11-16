# Payload schemas

The Pydantic classes in this directory are the single source of truth for
agent outputs and inter-agent payloads. Prompts remain under `agents/skills/`.

The `json/` directory contains generated JSON Schema artifacts for model
structured output, documentation, and non-Python consumers. Do not edit those
files manually. Regenerate them from the repository root with:

```sh
python3 -m agents.schemas.generate
```

`CaptainDecision`, `WatchAssessment`, and `VHFInterpretation` are model-generated
data. `WatchAssessment` is a bridge brief with `time`, `own_ship`,
`own_ship_history`, `traffic`, `controls`, `situation`, and `caveat` sections.
`VHFEvent` is the complete radio audit record, and `VHFAdvisory` is its smaller
operational message for the captain.
