# Ship trajectory figures

From the repository root:

```bash
.venv/bin/python -m tools.plot_ship_trajectories --case 15
.venv/bin/python -m tools.plot_ship_trajectories --case 15 --layout overlay
.venv/bin/python -m tools.plot_ship_trajectories --case all
```

The default models are MOOS-IvP, Qwen3-8B with COLREG RAG, and the fine-tuned run
`20261005T103227Z_finetuned`. Each case produces matching-scale panels by default.
The dated run contains cases 03–22; `qwen3_8b_finetuned` contains cases 01–02.
Missing model/case logs are reported and
omitted; incomplete tracks end at their last actual observation.

Outputs live in `figures/trajectories/`: PDF and SVG for thesis inclusion,
300-dpi PNG for preview, CSV of plotted coordinates, and JSON identifying source
logs and sample counts. Coordinates use the local simulation frame, with east on
the horizontal axis and north on the vertical axis, in nautical miles. This is an
ocean-style schematic, not a geographic chart: no coastline is inferred.
Straight segments connect logged positions without smoothing or extending tracks.
Each plot uses square bounds centred between the configured mission start and
destination, with side length equal to their larger coordinate separation plus a 1 NM
margin on each side. For the Imazu missions this is −4 to +4 NM on both axes. Tracks outside the square
are clipped in the figure; the CSV keeps every logged coordinate. When a
`scenario.ini` is unavailable, the first own-ship observation supplies the start.
First observations and mission destinations have different markers. Dashed lines
are observed contact tracks. Each vessel has a single direction arrow at the
end of its last visible nonzero segment. For a track ending outside the square,
the arrow marks its last visible exit. Stationary samples do not override the
last observed movement direction, and missing contact reports are not bridged.
There are no intermediate arrows or snapshot icons.

Compare any additional model, such as DPO, by repeating `--run` for every model:

```bash
.venv/bin/python -m tools.plot_ship_trajectories --case 15 \
  --run 'Fine-tuned=logs/evaluations/imazu/20261005T103227Z_finetuned' \
  --run 'DPO=logs/evaluations/imazu/YOUR_DPO_RUN'
```

Each path can also be an individual `audit.jsonl` or `telemetry.jsonl` file.
Use `--no-contacts` for own-ship routes only, `--unit km` for kilometres,
`--title 'Your figure title'`, or `--output-dir PATH` to change the destination.
Re-running the same case/layout in the same output directory replaces its figures.

## All 22 cases per architecture

Produce a separate two-page A4 PDF for each architecture, with 12 panels on the
first page and 10 on the second:

```bash
MPLCONFIGDIR=/tmp/moos-llm-matplotlib .venv/bin/python -m tools.plot_ship_trajectories \
  --layout architecture --output-dir figures/trajectories/architectures/pages \
  --run 'MOOS-IvP=logs/evaluations/imazu/moos_ivp_colregs_baseline' \
  --run 'Qwen3-8B COLREG RAG=logs/evaluations/imazu/qwen3_8b_colregs_rag' \
  --run 'Qwen3-8B SFT=logs/evaluations/imazu/20261005T103227Z_finetuned' \
  --fallback-run 'Qwen3-8B SFT=logs/evaluations/imazu/qwen3_8b_finetuned' \
  --run 'Qwen3-8B DPO=logs/evaluations/imazu/20261007T122820Z_dpo'
```

For a single A3 overview sheet containing all 22 panels, use the same command
with `--cases-per-page 22 --columns 4` and
`--output-dir figures/trajectories/architectures/overview`.

Architecture layout requires run directories. Explicit fallback runs are checked
only when the primary run lacks a usable case. Each architecture gets a PDF,
PNG/SVG per page, a CSV of the observations, and a JSON manifest recording the
source of each case and its square bounds. Missing cases retain labelled empty
panels; unfinished cases show their observed track without implying completion.
The current DPO suite is still running: rerun the command to update its figures.
All architectures use the same expanded square bounds for a given case.

Matplotlib is provided by the `plot` optional dependency. Installation if needed:

```bash
uv pip install --python .venv/bin/python '.[plot]'
```
