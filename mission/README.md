# Mission scenarios

The reusable MOOS-IvP launch scripts and templates live directly in this
directory. Scenario definitions and their assets live below `scenarios/`:

- `scenarios/open_waters/fleet.ini` contains the existing Marsdiep/open-water
  mission and its chart files.
- `scenarios/imazu/01/01.ini` through `22/22.ini` contain the selected Imazu
  reference implementation.

Run commands from this directory (the scripts also change here automatically):

```sh
# Existing open-water mission (the default)
./launch.sh

# Imazu case 1 without a geographic chart
./launch.sh --config=scenarios/imazu/01/01.ini --tiff=none

# Imazu case 1 with its neutral ocean background
./launch.sh --config=scenarios/imazu/01/01.ini \
  --tiff=scenarios/imazu/ocean_background_4096.tif

# Only render/validate the generated MOOS target files
./launch.sh --config=scenarios/imazu/01/01.ini --tiff=none --just_make
```

Pass `--tiff=scenarios/<name>/<chart>.tif` when a scenario has a matching
georeferenced chart. `none` leaves pMarineViewer on its coordinate grid.
MOOS-IvP requires the `.tif` suffix and a same-basename `.info` file.

To trace land or other static collision areas directly on one of those charts:

```sh
uv run python tools/label_collision_polygons.py \
  mission/scenarios/open_waters/denhelder_texel.tif
```

Left-click polygon vertices and press Enter to close/name a polygon. Each shape
is reduced to its convex hull, as required by MOOS-IvP. Ctrl+S saves both
`<chart>.collision.json` (local MOOS `x/y`, latitude/longitude, and source
pixels) and `<chart>.collision.moos` (`given_obstacle = pts={...}` lines ready
for a `pObstacleMgr` configuration block). Reopening the chart resumes editing.
Use several overlapping convex polygons for a concave coastline; one hull around
the whole coast would incorrectly mark navigable water as land.

`launch.sh` automatically uses that `.collision.moos` companion when it exists.
An explicit map can be selected with `--obstacles=PATH`; otherwise the mission
runs `pObstacleMgr` with an empty map. Each vehicle helm uses
`BHV_AvoidObstacleV24`, treating an obstacle CPA of 15 m as minimum utility and
30 m as maximum utility. Tune those CPA distances and the behavior ranges in
`meta_vehicle.bhv` for the vessel and operating speed.

From the repository root, `run_simulator.sh` starts the complete stack—vLLM,
MOOS, and pLLMBridge—for a selected scenario:

```sh
./run_simulator.sh --scenario open_waters
./run_simulator.sh --scenario imazu/03
./run_simulator.sh --scenario imazu/22 --nogui
```

The open-water and Imazu backgrounds are selected automatically. Use
`--tiff=none` or `--tiff=PATH` to override that choice. Arguments following
`--` are passed through to `mission/launch.sh`.
