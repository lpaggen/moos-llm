# Model-knowledge baseline

The existing **`base`** architecture is this experiment. It gives the untuned
model a navigation prompt and bridge observations, asks it to infer the COLREG
responsibilities, cite applicable rule numbers, and choose an executable action.
Rule selection and the decision both come from the model's training knowledge.

No COLREG text, simplified summaries, retrieved examples, or decision-tree
results are supplied. Both MAIB RAG and COLREG RAG are disabled; the COLREG tree
is off by default in the simulator launcher. The model input excludes bridge
risk verdicts, encounter labels, vessel roles, rule lists, and maneuver advice.
It retains observations such as vessel types, positions, headings, speeds,
bearings, CPA/TCPA, and recent history.

The prompt is [`captain.md`](../agents/skills/captain.md) plus
`BASE_RUNTIME_CONTRACT` in [`agent_oow.py`](../agents/agent_oow.py). It explains
observation units, the task, and the helm interface, but supplies no COLREG rule
text or numbered rule guidance. It asks the model to infer responsibilities and
cite the rules that support its decision.

Validate the configuration without running services:

```sh
./run_imazu_evaluation.sh --architecture base --cases 1-22 --check
```

Run all 22 IMAZU scenarios:

```sh
./run_imazu_evaluation.sh --architecture base --cases 1-22 --gui
```

GUI is enabled by default and the runner automatically deploys every scenario.
For a single scenario with GUI and deployment:

```sh
./run_simulator.sh --scenario imazu/01 --no-rag --no-colregs-rag --no-colregs-tree --oow base --deploy
```

Use `base-reasoning` for the same experiment with thinking enabled. Compare
against `colregs-rag` and `colregs-tree` with matching scenarios, time warp,
timeouts and cycle limits. The base profile uses numeric action arguments;
the RAG/tree profiles use qualitative intents converted to numeric commands.
This existing interface difference should be reported when comparing outcomes.
