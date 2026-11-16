# Configuration of the 22 Imazu scenarios

This report is generated from the authoritative checked-in constellation table at `mission/scenarios/imazu/imazu_constellations.csv`. Coordinates below use the simulator frame: `x` is east, `y` is north, positions are in nautical miles, and headings are nautical degrees clockwise from north. Parenthesized headings preserve the negative notation used in the source table.

## Common configuration

- Own ship (`LLM_SHIP`) starts at `(x=0, y=-6 NM)`, heading `000°`, at `12.0 kn` (`6.173 m/s`).
- Its waypoint and loiter point are `(x=0, y=+6 NM)`, producing a 12 NM northbound route through the encounter origin.
- Normal targets travel at `12.0 kn`. TS1 travels at `8.4 kn` (`4.321 m/s`) in cases 3, 7, 15, 16, 17, 20, and 22.
- Targets hold their initial course and speed and have collision avoidance disabled; only own ship uses the controller under evaluation.
- The geometry synchronizes the unaltered tracks at the origin after approximately 30 minutes.
- The simulator's Imazu/Sawada navigation domain uses 0.5 NM (926 m) general passing clearance and 1.0 NM (1,852 m) forward/bow-crossing clearance.
- Separately, the frozen thesis scoring function evaluates the safety axis against a 500 m minimum-clearance threshold. That scoring threshold is not part of the scenario geometry.
- Time warp is an evaluation launch parameter and does not change these physical coordinates, speeds, CPA, or TCPA values.

## Compact overview

Each target is shown as `TS: start (x east, y north); heading; speed`.

| Case | Targets | Target configurations |
|---:|---:|---|
| 1 | 1 | TS1: (+0.000, +6.000) NM; 180°; 12.0 kn |
| 2 | 1 | TS1: (+6.000, +0.000) NM; 270 (-90)°; 12.0 kn |
| 3 | 1 | TS1: (+0.000, -4.200) NM; 0°; 8.4 kn |
| 4 | 1 | TS1: (-4.243, -4.243) NM; 45°; 12.0 kn |
| 5 | 2 | TS1: (+0.000, +6.000) NM; 180°; 12.0 kn<br>TS2: (+6.000, +0.000) NM; 270 (-90)°; 12.0 kn |
| 6 | 2 | TS1: (+1.042, -5.909) NM; 350 (-10)°; 12.0 kn<br>TS2: (+4.243, -4.243) NM; 315 (-45)°; 12.0 kn |
| 7 | 2 | TS1: (+0.000, -4.200) NM; 0°; 8.4 kn<br>TS2: (+4.243, -4.243) NM; 315 (-45)°; 12.0 kn |
| 8 | 2 | TS1: (+0.000, +6.000) NM; 180°; 12.0 kn<br>TS2: (+6.000, +0.000) NM; 270 (-90)°; 12.0 kn |
| 9 | 2 | TS1: (+3.000, -5.196) NM; 330 (-30)°; 12.0 kn<br>TS2: (+6.000, +0.000) NM; 270 (-90)°; 12.0 kn |
| 10 | 2 | TS1: (+6.000, +0.000) NM; 270 (-90)°; 12.0 kn<br>TS2: (-1.553, -5.796) NM; 15°; 12.0 kn |
| 11 | 2 | TS1: (-6.000, +0.000) NM; 90°; 12.0 kn<br>TS2: (+3.000, -5.196) NM; 330 (-30)°; 12.0 kn |
| 12 | 2 | TS1: (+4.243, -4.243) NM; 315 (-45)°; 12.0 kn<br>TS2: (+1.042, -5.909) NM; 350 (-10)°; 12.0 kn |
| 13 | 3 | TS1: (+0.000, +6.000) NM; 180°; 12.0 kn<br>TS2: (-1.042, -5.909) NM; 10°; 12.0 kn<br>TS3: (-4.243, -4.243) NM; 45°; 12.0 kn |
| 14 | 3 | TS1: (+1.042, -5.909) NM; 350 (-10)°; 12.0 kn<br>TS2: (+4.243, -4.243) NM; 315 (-45)°; 12.0 kn<br>TS3: (+6.000, +0.000) NM; 270 (-90)°; 12.0 kn |
| 15 | 3 | TS1: (+0.000, -4.200) NM; 0°; 8.4 kn<br>TS2: (+4.243, -4.243) NM; 315 (-45)°; 12.0 kn<br>TS3: (+6.000, +0.000) NM; 270 (-90)°; 12.0 kn |
| 16 | 3 | TS1: (-2.970, -2.970) NM; 45°; 8.4 kn<br>TS2: (-6.000, +0.000) NM; 90°; 12.0 kn<br>TS3: (+6.000, +0.000) NM; 270 (-90)°; 12.0 kn |
| 17 | 3 | TS1: (+0.000, -4.200) NM; 0°; 8.4 kn<br>TS2: (-1.042, -5.909) NM; 10°; 12.0 kn<br>TS3: (+4.243, -4.243) NM; 315 (-45)°; 12.0 kn |
| 18 | 3 | TS1: (+4.243, +4.243) NM; 225 (-135)°; 12.0 kn<br>TS2: (+1.553, -5.796) NM; 345 (-15)°; 12.0 kn<br>TS3: (+3.000, -5.196) NM; 330 (-30)°; 12.0 kn |
| 19 | 3 | TS1: (-1.553, -5.796) NM; 15°; 12.0 kn<br>TS2: (+1.553, -5.796) NM; 345 (-15)°; 12.0 kn<br>TS3: (+4.243, +4.243) NM; 225 (-135)°; 12.0 kn |
| 20 | 3 | TS1: (+0.000, -4.200) NM; 0°; 8.4 kn<br>TS2: (+1.553, -5.796) NM; 345 (-15)°; 12.0 kn<br>TS3: (+6.000, +0.000) NM; 270 (-90)°; 12.0 kn |
| 21 | 3 | TS1: (+1.553, -5.796) NM; 345 (-15)°; 12.0 kn<br>TS2: (-1.553, -5.796) NM; 15°; 12.0 kn<br>TS3: (+6.000, +0.000) NM; 270 (-90)°; 12.0 kn |
| 22 | 3 | TS1: (+0.000, -4.200) NM; 0°; 8.4 kn<br>TS2: (+4.243, -4.243) NM; 315 (-45)°; 12.0 kn<br>TS3: (+6.000, +0.000) NM; 270 (-90)°; 12.0 kn |

## Exact target table

The generated target waypoint is 6 NM from the origin along its heading. Every target loiters at that same waypoint.

| Case | Vessel | Start x (NM E) | Start y (NM N) | Heading (°) | Speed (kn) | Waypoint x (NM E) | Waypoint y (NM N) |
|---:|---|---:|---:|---:|---:|---:|---:|
| 1 | TS1 | 0.000 | 6.000 | 180 | 12.0 | 0.000 | -6.000 |
| 2 | TS1 | 6.000 | 0.000 | 270 (-90) | 12.0 | -6.000 | 0.000 |
| 3 | TS1 | 0.000 | -4.200 | 0 | 8.4 | 0.000 | 6.000 |
| 4 | TS1 | -4.243 | -4.243 | 45 | 12.0 | 4.243 | 4.243 |
| 5 | TS1 | 0.000 | 6.000 | 180 | 12.0 | 0.000 | -6.000 |
| 5 | TS2 | 6.000 | 0.000 | 270 (-90) | 12.0 | -6.000 | 0.000 |
| 6 | TS1 | 1.042 | -5.909 | 350 (-10) | 12.0 | -1.042 | 5.909 |
| 6 | TS2 | 4.243 | -4.243 | 315 (-45) | 12.0 | -4.243 | 4.243 |
| 7 | TS1 | 0.000 | -4.200 | 0 | 8.4 | 0.000 | 6.000 |
| 7 | TS2 | 4.243 | -4.243 | 315 (-45) | 12.0 | -4.243 | 4.243 |
| 8 | TS1 | 0.000 | 6.000 | 180 | 12.0 | 0.000 | -6.000 |
| 8 | TS2 | 6.000 | 0.000 | 270 (-90) | 12.0 | -6.000 | 0.000 |
| 9 | TS1 | 3.000 | -5.196 | 330 (-30) | 12.0 | -3.000 | 5.196 |
| 9 | TS2 | 6.000 | 0.000 | 270 (-90) | 12.0 | -6.000 | 0.000 |
| 10 | TS1 | 6.000 | 0.000 | 270 (-90) | 12.0 | -6.000 | 0.000 |
| 10 | TS2 | -1.553 | -5.796 | 15 | 12.0 | 1.553 | 5.796 |
| 11 | TS1 | -6.000 | 0.000 | 90 | 12.0 | 6.000 | 0.000 |
| 11 | TS2 | 3.000 | -5.196 | 330 (-30) | 12.0 | -3.000 | 5.196 |
| 12 | TS1 | 4.243 | -4.243 | 315 (-45) | 12.0 | -4.243 | 4.243 |
| 12 | TS2 | 1.042 | -5.909 | 350 (-10) | 12.0 | -1.042 | 5.909 |
| 13 | TS1 | 0.000 | 6.000 | 180 | 12.0 | 0.000 | -6.000 |
| 13 | TS2 | -1.042 | -5.909 | 10 | 12.0 | 1.042 | 5.909 |
| 13 | TS3 | -4.243 | -4.243 | 45 | 12.0 | 4.243 | 4.243 |
| 14 | TS1 | 1.042 | -5.909 | 350 (-10) | 12.0 | -1.042 | 5.909 |
| 14 | TS2 | 4.243 | -4.243 | 315 (-45) | 12.0 | -4.243 | 4.243 |
| 14 | TS3 | 6.000 | 0.000 | 270 (-90) | 12.0 | -6.000 | 0.000 |
| 15 | TS1 | 0.000 | -4.200 | 0 | 8.4 | 0.000 | 6.000 |
| 15 | TS2 | 4.243 | -4.243 | 315 (-45) | 12.0 | -4.243 | 4.243 |
| 15 | TS3 | 6.000 | 0.000 | 270 (-90) | 12.0 | -6.000 | 0.000 |
| 16 | TS1 | -2.970 | -2.970 | 45 | 8.4 | 4.243 | 4.243 |
| 16 | TS2 | -6.000 | 0.000 | 90 | 12.0 | 6.000 | 0.000 |
| 16 | TS3 | 6.000 | 0.000 | 270 (-90) | 12.0 | -6.000 | 0.000 |
| 17 | TS1 | 0.000 | -4.200 | 0 | 8.4 | 0.000 | 6.000 |
| 17 | TS2 | -1.042 | -5.909 | 10 | 12.0 | 1.042 | 5.909 |
| 17 | TS3 | 4.243 | -4.243 | 315 (-45) | 12.0 | -4.243 | 4.243 |
| 18 | TS1 | 4.243 | 4.243 | 225 (-135) | 12.0 | -4.243 | -4.243 |
| 18 | TS2 | 1.553 | -5.796 | 345 (-15) | 12.0 | -1.553 | 5.796 |
| 18 | TS3 | 3.000 | -5.196 | 330 (-30) | 12.0 | -3.000 | 5.196 |
| 19 | TS1 | -1.553 | -5.796 | 15 | 12.0 | 1.553 | 5.796 |
| 19 | TS2 | 1.553 | -5.796 | 345 (-15) | 12.0 | -1.553 | 5.796 |
| 19 | TS3 | 4.243 | 4.243 | 225 (-135) | 12.0 | -4.243 | -4.243 |
| 20 | TS1 | 0.000 | -4.200 | 0 | 8.4 | 0.000 | 6.000 |
| 20 | TS2 | 1.553 | -5.796 | 345 (-15) | 12.0 | -1.553 | 5.796 |
| 20 | TS3 | 6.000 | 0.000 | 270 (-90) | 12.0 | -6.000 | 0.000 |
| 21 | TS1 | 1.553 | -5.796 | 345 (-15) | 12.0 | -1.553 | 5.796 |
| 21 | TS2 | -1.553 | -5.796 | 15 | 12.0 | 1.553 | 5.796 |
| 21 | TS3 | 6.000 | 0.000 | 270 (-90) | 12.0 | -6.000 | 0.000 |
| 22 | TS1 | 0.000 | -4.200 | 0 | 8.4 | 0.000 | 6.000 |
| 22 | TS2 | 4.243 | -4.243 | 315 (-45) | 12.0 | -4.243 | 4.243 |
| 22 | TS3 | 6.000 | 0.000 | 270 (-90) | 12.0 | -6.000 | 0.000 |

## Source files

- `mission/scenarios/imazu/imazu_constellations.csv`: authoritative geometry.
- `mission/scenarios/imazu/NN/NN.ini`: generated simulator configuration for case `NN`.
- `tools/generate_imazu_scenarios.py`: deterministic renderer used to create and verify the INI files.
- `mission/scenarios/imazu/README.md`: provenance, coordinate convention, corrections, and launch notes.
