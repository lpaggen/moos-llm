# OOW Raw-State Payload Field Reference

This document describes the processed numeric payload supplied to the OOW. It
distinguishes the ordinary local raw-state baseline from optional runtime
extensions, privileged teacher-only evidence, and the older offline candidate
action enrichment. These structures must not be treated as equivalent.

Authoritative implementations:

- `bridge/observations.py::compact_state` constructs `state`;
- `agents/agent_oow.py::deterministic_input` constructs the user payload;
- `agents/teacher_lookahead.py` constructs privileged candidate projections;
- `agents/colregs/paper_state_machine.py` constructs the privileged Eriksen
  oracle;
- `bridge/candidate_actions.py` constructs the older offline candidate list.

## 1. Ordinary raw-state user payload

The ordinary local base OOW receives a JSON object with these outer fields.
`clean_none` removes unavailable nested values rather than serialising them as
`null`, except in structures that are not passed through `clean_none`.

| Field path | Type | Presence | Meaning / activation |
|---|---|---|---|
| `allowed_actions` | array of strings | Always | Actions accepted for this cycle. Normally `maintain_course`, `alter_course`, `set_speed`, `stop`, `resume`, and `skip`; stopped/paused state reduces this to `resume` and `skip`; `rejoin_route` is inserted during an active course alteration. |
| `state` | object | Always in raw-state mode | Current compact numeric observation described in Sections 2–7. |
| `safety_limits` | object | Always in raw-state mode | Configured CPA/TCPA and bow-crossing thresholds described in Section 8. |
| `previous_states` | array of `state` objects | Conditional | Earlier compact observations from the bounded history. Omitted on the first observation or after history is cleared. Does not include the current state. |
| `active_action_review` | object | Conditional | Added when own ship is stopped/paused, a course alteration is active, or target speed remains reduced. See Section 9. |
| `vhf_communications` | array of objects | Conditional | Added only when the VHF benchmark is enabled and interpreted transmissions exist. See Section 10. |
| `briefing` | string | Configuration-dependent exception | Normally absent in raw-state mode. Currently added if the local Eriksen prompt is enabled because that branch appends a rendered COLREG assessment to the narrative. This makes raw-state + Eriksen a mixed numeric/prose input. |

The canonical raw-state baseline therefore normally has only
`allowed_actions`, `state`, `safety_limits`, optional `previous_states`, and
optional `active_action_review`.

Historical note: early `base_raw_state_warp5` logs predate the addition of
`safety_limits` and `previous_states`. The later matched raw-state campaign
records both fields. For reproducing a particular historical result, the exact
logged `user_prompt` remains authoritative; this document describes the current
implementation and separately identifies those older omissions.

## 2. Current-state fields

| Field path | Type / unit | Presence | Meaning |
|---|---|---|---|
| `state.observed_at_s` | number, simulator seconds | Core; omitted if unavailable | Observation time copied from MOOS time and rounded to two decimals. |
| `state.own_ship` | object | Always | Own-ship observation. |
| `state.mission` | object | Always | Fixed mission destination and derived geometry. |
| `state.contacts` | array | Always, possibly empty | One object per tracked contact. |
| `state.current_maneuver` | object | Conditional | Present only while an `alter_course` action is underway. |
| `state.conditions` | object | Conditional | Present when COLREG/environment assumptions are available. |

## 3. Own-ship fields

| Field path | Type / unit | Meaning |
|---|---|---|
| `state.own_ship.name` | string | Own-ship name, normally `LLM_SHIP` or scenario-specific name. |
| `state.own_ship.vessel_type` | string | Simulator vessel type. |
| `state.own_ship.x` | number, m | Cartesian easting relative to scenario origin. |
| `state.own_ship.y` | number, m | Cartesian northing relative to scenario origin. |
| `state.own_ship.heading` | number, degrees | Compass heading: 0 north, 90 east. |
| `state.own_ship.speed` | number, m/s | Measured speed, rounded to two decimals. |
| `state.own_ship.target_speed` | number, m/s | Currently commanded target speed. |
| `state.own_ship.recovery_speed` | number, m/s | Normal cruise target to restore after a reduction. |
| `state.own_ship.movement_status` | string | Typically `underway`, `paused`, or `stalled`. |
| `state.own_ship.stopped` | boolean | Whether measured motion is treated as stopped. |
| `state.own_ship.paused` | boolean | Whether bridge control is paused. |
| `state.own_ship.colregs_vessel_type` | string | Normalised COLREG vessel type, e.g. `power_driven`. This is vessel status, not encounter role. |

Each value is omitted if its upstream value is unavailable.

## 4. Mission fields

`mission_objective` is authoritative. `next_waypoint` is used only as a fallback
when no fixed objective exists; a temporary avoidance waypoint is not substituted
for the destination.

| Field path | Type / unit | Presence | Meaning |
|---|---|---|---|
| `state.mission.x` | number, m | Core | Destination easting. |
| `state.mission.y` | number, m | Core | Destination northing. |
| `state.mission.distance_m` | number, m | Derived when geometry is complete | Straight-line distance from own ship to destination. |
| `state.mission.bearing_deg` | number, degrees | Derived when geometry is complete | Compass bearing from own ship to destination. |
| `state.mission.heading_error_deg` | number, degrees | Derived when geometry is complete | Signed shortest difference between objective bearing and own heading. Negative is port, positive is starboard. |

## 5. Contact fields

All contact kinematics are observations or deterministic geometry derived from
the same snapshot. The compact state deliberately omits encounter role, risk,
recommended manoeuvre, applicable rules, raw MOOS strings, and collision labels.

| Field path | Type / unit | Presence | Meaning |
|---|---|---|---|
| `state.contacts[].name` | string | Core | Contact identifier. |
| `state.contacts[].vessel_type` | string | When reported | Simulator vessel type. |
| `state.contacts[].colregs_vessel_type` | string | When known | Persisted, normalised COLREG vessel type. |
| `state.contacts[].x` | number, m | When reported | Contact easting. |
| `state.contacts[].y` | number, m | When reported | Contact northing. |
| `state.contacts[].heading` | number, degrees | When reported | Contact compass heading. |
| `state.contacts[].speed` | number, m/s | When reported | Contact measured speed. |
| `state.contacts[].range_m` | integer, m | When geometry is available | Current centre-to-centre range. |
| `state.contacts[].relative_bearing_deg` | number, degrees | When geometry is available | Clockwise bearing relative to own bow. |
| `state.contacts[].sector` | string | When bearing is available | `ahead`, `starboard bow`, `starboard beam`, `starboard quarter`, `astern`, `port quarter`, `port beam`, or `port bow`. |
| `state.contacts[].closing_speed` | number, m/s | When geometry is available | Positive means closing; negative means opening. |
| `state.contacts[].projection_status` | enum | When calculated | `valid`, `beyond_planning_horizon`, or `no_future_cpa`. |
| `state.contacts[].cpa_distance_m` | integer, m | Conditional | CPA distance, included only for `valid` or `beyond_planning_horizon`. |
| `state.contacts[].tcpa_s` | number, s | Conditional | Time to CPA, included only for `valid` or `beyond_planning_horizon`. |
| `state.contacts[].track_intersection` | object | Conditional | Intersection of the two forward course rays; distinct from CPA. |
| `state.contacts[].track_intersection.x` | number, m | With intersection | Intersection easting. |
| `state.contacts[].track_intersection.y` | number, m | With intersection | Intersection northing. |
| `state.contacts[].track_intersection.own_eta_s` | number, s | With intersection | Own-ship ETA to the intersection at current speed. May be infinite internally if stopped. |
| `state.contacts[].track_intersection.contact_eta_s` | number, s | With intersection | Contact ETA to the intersection at current speed. May be infinite internally if stopped. |
| `state.contacts[].age_s` | number, s | Core when reported | Age of the track observation. |
| `state.contacts[].track_quality` | enum | Always derived | `fresh` at at most 5 s, `stale` above 5 s through 60 s, otherwise `unreliable`. |
| `state.contacts[].track_generation` | integer | Always, default 0 | Increments when a stale track is reacquired with an implausible position jump. |
| `state.contacts[].track_reacquired` | boolean | Always, default false | True for the observation on which reacquisition was detected. |

## 6. Active manoeuvre fields

| Field path | Type / unit | Presence | Meaning |
|---|---|---|---|
| `state.current_maneuver.action` | string | Active course alteration only | Currently `alter_course`. |
| `state.current_maneuver.degrees` | number, degrees | If reported | Signed relative alteration that created the active plan. |
| `state.current_maneuver.temporary_waypoint_active` | boolean | Active course alteration | Whether a temporary waypoint is present. |
| `state.current_maneuver.temporary_waypoint` | object | If waypoint exists | Current avoidance waypoint. |
| `state.current_maneuver.temporary_waypoint.x` | number, m | With waypoint | Waypoint easting. |
| `state.current_maneuver.temporary_waypoint.y` | number, m | With waypoint | Waypoint northing. |

## 7. Operating-condition fields

These values come from the configured COLREG assumptions rather than being
inferred by the language model.

| Field path | Type / unit | Meaning |
|---|---|---|
| `state.conditions.visibility_condition` | string | Normally `clear` or `restricted`. |
| `state.conditions.visibility_range_m` | number, m | Configured visibility range. |
| `state.conditions.narrow_channel` | boolean | Whether narrow-channel context is configured. |
| `state.conditions.traffic_separation_scheme` | boolean | Whether traffic-separation context is configured. |

## 8. Safety-limit fields

| Field path | Type / unit | Meaning / source |
|---|---|---|
| `safety_limits.caution_cpa_m` | number, m | Caution CPA threshold. |
| `safety_limits.caution_tcpa_s` | number, s | Planning/caution TCPA horizon. |
| `safety_limits.intervention_cpa_m` | number, m | Minimum safe/intervention CPA. For the current training profile this is the 500 m hard minimum. |
| `safety_limits.intervention_tcpa_s` | number, s | Intervention TCPA horizon. |
| `safety_limits.critical_cpa_m` | number, m | Critical CPA threshold. |
| `safety_limits.emergency_tcpa_s` | number, s | Emergency-action horizon. |
| `safety_limits.bow_crossing_clearance_m` | number, m | Required clearance for forward crossing or head-on geometry. |

These values may be overridden by the selected navigation profile and
environment variables; they must not be assumed constant across campaigns.

## 9. Active-action review fields

The object has one of three state-dependent shapes.

| Field path | Type / unit | Used when | Meaning |
|---|---|---|---|
| `active_action_review.active_action` | enum | Always when object exists | `stop`, `alter_course`, `alter_course_and_set_speed`, or `set_speed`. |
| `active_action_review.current_speed` | number, m/s | Stopped/paused | Current measured speed. |
| `active_action_review.target_speed` | number, m/s | Stopped/paused | Current target speed. |
| `active_action_review.degrees` | number, degrees | Active course alteration | Existing relative alteration. |
| `active_action_review.current_target_speed` | number, m/s | Active alteration or reduced speed | Current speed target. |
| `active_action_review.normal_speed` | number, m/s | Active alteration or reduced speed | Recovery/normal target. |
| `active_action_review.instruction` | string | Always when object exists | Deterministic instruction to review whether to resume, retain, replace, cancel, or restore the active control. |

## 10. VHF fields

This list is present only when `MOOS_LLM_VHF_BENCHMARK=on` and at least one
interpreted message is retained.

| Field path | Type | Meaning |
|---|---|---|
| `vhf_communications[].message_id` | string | Unique interpreted-message identifier. |
| `vhf_communications[].channel` | integer | VHF channel. |
| `vhf_communications[].scenario_id` | string | Benchmark scenario identifier. |
| `vhf_communications[].scenario_event_id` | string | Triggered event identifier. |
| `vhf_communications[].speaker` | string | Interpreted sender. |
| `vhf_communications[].addressee` | string | Interpreted recipient. |
| `vhf_communications[].addressed_to_own_ship` | boolean | Whether the interpreter considers the message directed to own ship. |
| `vhf_communications[].source_type` | string | E.g. vessel or VTS. |
| `vhf_communications[].identity_status` | string | Verification/ambiguity status. |
| `vhf_communications[].intent` | string | Message intent, e.g. manoeuvre request or traffic report. |
| `vhf_communications[].message` | string | Normalised semantic message. |
| `vhf_communications[].requested_action` | string or null | Requested action category. |
| `vhf_communications[].requested_turn_direction` | enum | `port`, `starboard`, or `none`. |
| `vhf_communications[].requested_turn_degrees` | number or null | Requested relative turn magnitude. |
| `vhf_communications[].requested_speed_knots` | number or null | Requested speed. Unlike state speeds, this field is explicitly in knots. |
| `vhf_communications[].requested_course_degrees` | number or null | Requested absolute course. |
| `vhf_communications[].reported_facts` | array of strings | Claims extracted from the transmission. |
| `vhf_communications[].confidence` | number | Interpreter confidence. |
| `vhf_communications[].clarification_required` | boolean | Whether identification or meaning requires clarification. |

## 11. Privileged teacher-only fields

These are not part of the local raw-state baseline. The OpenAI teacher profile
receives the numeric `state` alongside a prose `briefing`, a deterministic
oracle, and optionally the candidate lookahead. Raw-state input mode itself is
explicitly rejected for OpenAI inference, so `teacher_candidate_lookahead`
cannot honestly be described as an optional field of the runtime raw-state
baseline.

### 11.1 Deterministic COLREG oracle

| Field path | Type / unit | Meaning |
|---|---|---|
| `deterministic_colregs_oracle.method` | string | `eriksen_2020`. |
| `deterministic_colregs_oracle.available` | boolean | Whether sufficient geometry was available. |
| `deterministic_colregs_oracle.assumptions` | array of strings | Classifier assumptions. |
| `deterministic_colregs_oracle.thresholds` | object | Persistent-state thresholds below. |
| `.thresholds.cpa_entry_m` | number, m | Encounter entry CPA. |
| `.thresholds.cpa_exit_m` | number, m | Encounter exit CPA. |
| `.thresholds.tcpa_entry_min_s` | number, s | Lower entry TCPA bound. |
| `.thresholds.tcpa_entry_max_s` | number, s | Upper entry TCPA bound. |
| `.thresholds.tcpa_exit_min_s` | number, s | Lower exit TCPA bound. |
| `.thresholds.tcpa_exit_max_s` | number, s | Upper exit TCPA bound. |
| `.thresholds.emergency_entry_s` | number, s | Emergency-entry time-to-critical threshold. |
| `.thresholds.emergency_exit_s` | number, s | Emergency-exit threshold. |
| `.thresholds.critical_distance_m` | number, m | Distance used for time-to-critical calculation. |
| `deterministic_colregs_oracle.primary_contact` | string or null | Highest-priority classified contact. |
| `deterministic_colregs_oracle.priority_basis` | string | Text describing deterministic ordering. |
| `deterministic_colregs_oracle.contacts` | array | Per-contact classifier records. |
| `.contacts[].contact` | string | Contact name. |
| `.contacts[].previous_state` | enum | Previous persistent state: `SF`, `OT`, `HO`, `GW`, `SO`, or `EM`. |
| `.contacts[].state` | enum | Current persistent state. |
| `.contacts[].state_name` | string | Expanded state name. |
| `.contacts[].geometrical_state` | enum | Instantaneous geometry before persistence/hysteresis. |
| `.contacts[].own_role` | string | `give_way`, `stand_on`, `both_give_way`, `special_consideration`, `avoid_impeding`, or `not_applicable`. |
| `.contacts[].own_vessel_type` | string | Normalised own-ship type. |
| `.contacts[].contact_vessel_type` | string | Normalised contact type. |
| `.contacts[].differing_vessel_type_responsibility` | boolean | Whether Rule 18/type hierarchy changed responsibility. |
| `.contacts[].applicable_rules` | array of integers | Complete oracle rule list. |
| `.contacts[].applicable_rule_summaries` | object | Rule-number strings mapped to summaries. |
| `.contacts[].transition_reason` | string | Reason for entry, exit, or retained state. |
| `.contacts[].evolution_since_previous_snapshot.assessment` | string | `first_observation`, `closing`, `opening`, or `approximately_steady`. |
| `.contacts[].evolution_since_previous_snapshot.range_change_m` | number, m | Conditional after first observation. |
| `.contacts[].evolution_since_previous_snapshot.dcpa_change_m` | number, m | Conditional after first observation. |
| `.contacts[].evolution_since_previous_snapshot.tcpa_change_s` | number, s | Conditional after first observation. |
| `.contacts[].range_m` | number, m | Current range. |
| `.contacts[].tcpa_s` | number, s | Oracle TCPA, which may be negative. |
| `.contacts[].dcpa_m` | number, m | Oracle DCPA. |
| `.contacts[].tcrit_s` | number or null, s | Time to enter critical-distance boundary. |
| `.contacts[].own_relative_bearing_deg` | number, degrees | Contact bearing from own ship. |
| `.contacts[].contact_relative_bearing_deg` | number, degrees | Own-ship bearing from contact. |
| `.contacts[].course_difference_deg` | number, degrees | Absolute course difference. |
| `.contacts[].priority_rank` | integer | Deterministic operational rank. |

### 11.2 Privileged candidate lookahead

| Field path | Type / unit | Meaning |
|---|---|---|
| `teacher_candidate_lookahead.purpose` | string | Marks this as a teacher aid, not an executable override. |
| `.assumptions.target_motion` | string | Targets use constant course and speed. |
| `.assumptions.own_ship_model` | string | Finite turn/acceleration model. |
| `.assumptions.horizon_s` | number, s | Projection horizon. |
| `.assumptions.timestep_s` | number, s | Simulation step. |
| `.assumptions.turn_rate_deg_s` | number, deg/s | Assumed turn rate. |
| `.assumptions.acceleration_mps2` | number, m/s² | Assumed acceleration. |
| `.assumptions.deceleration_mps2` | number, m/s² | Assumed deceleration. |
| `.assumptions.temporary_maneuver_distance_m` | number, m | Candidate temporary-waypoint distance. |
| `.assumptions.waypoint_completion_radius_m` | number, m | Waypoint completion radius. |
| `.thresholds.hard_minimum_separation_m` | number, m | Training acceptance lower bound. |
| `.thresholds.critical_separation_m` | number, m | Critical classification boundary. |
| `.thresholds.practical_margin_m` | number, m | Margin above the hard minimum. |
| `.thresholds.safe_at_or_above_m` | number, m | Hard minimum plus margin. |
| `.thresholds.classification` | object | Text definitions for `critical`, `unsafe`, `marginal`, and `safe`. |
| `.warning` | string | Projection limitations. |
| `.candidates` | array | Evaluated executable candidates. |
| `.candidates[].action` | string | Candidate action. |
| `.candidates[].label` | string | Human-readable candidate. |
| `.candidates[].degrees` | number, degrees | Course candidates only: −60, −45, −30, +30, +45, or +60. |
| `.candidates[].maneuver_distance_m` | number, m | Course candidates only. |
| `.candidates[].speed_mps` | number, m/s | Speed candidates only. |
| `.candidates[].projected_minimum_separation_m` | number or null, m | Worst projected separation across contacts. |
| `.candidates[].classification` | enum | `critical`, `unsafe`, `marginal`, `safe`, or `no_contacts`. |
| `.candidates[].all_contacts_at_or_above_minimum` | boolean | Whether every contact remains at or above the hard minimum. |
| `.candidates[].worst_contact` | string or null | Contact producing the smallest projection. |
| `.candidates[].contacts` | array | Per-contact projection results. |
| `.candidates[].contacts[].contact` | string | Contact name. |
| `.candidates[].contacts[].minimum_separation_m` | number, m | Projected minimum separation. |
| `.candidates[].contacts[].time_to_minimum_s` | number, s | Time of projected minimum. |
| `.candidates[].contacts[].final_separation_m` | number, m | Separation at horizon end. |
| `.candidates[].contacts[].final_trend` | enum | `opening` or `not_opening`. |
| `.candidates[].contacts[].classification` | enum | Per-contact safety class. |
| `.candidates[].final_mission_distance_m` | number or null, m | Distance to objective at horizon end. |
| `.candidates[].projected_path_length_m` | number, m | Own-ship path length during projection. |
| `.candidates[].final_heading_deg` | number, degrees | Projected final heading. |
| `.candidates[].final_speed_mps` | number, m/s | Projected final speed. |

Candidate generation depends on control state. A stopped/paused ship receives
`skip` and `resume`; an underway ship receives `maintain_course`, six course
alterations, applicable speed targets, `stop`, and optionally `rejoin_route`.

## 12. Older offline `candidate_actions`

`bridge/candidate_actions.py` can enrich an offline compact JSONL record. The
current online `BridgeSnapshot.state_for_agent` explicitly removes
`candidate_actions`, and `compact_state` does not create it. It is therefore not
evidence received by the evaluated runtime raw-state OOW.

| Field path | Type / unit | Meaning |
|---|---|---|
| `state.allowed_actions` | array of strings | Actions inferred from the offline candidate list. This nesting differs from the online outer `allowed_actions`. |
| `state.candidate_actions` | array | Offline executable candidates. |
| `.candidate_actions[].action` | string | `maintain_course`, `alter_course`, `set_speed`, `stop`, `skip`, or `resume`. |
| `.candidate_actions[].degrees` | number, degrees | Fixed zero for argument-free candidates. |
| `.candidate_actions[].degrees_range.min` | number, degrees | Minimum permitted alteration for an `alter_course` candidate. |
| `.candidate_actions[].degrees_range.max` | number, degrees | Maximum permitted alteration. |
| `.candidate_actions[].distance_m` | number, m | Temporary manoeuvre distance; the offline default is 200 m. |
| `.candidate_actions[].speed` | number, m/s | Candidate target speed. |

This older list does not project trajectories and must not be confused with
`teacher_candidate_lookahead`.

## 13. Configuration matrix

| Configuration | Effective user content | Important exclusions / additions |
|---|---|---|
| Base narrative | `allowed_actions`, `briefing`, optional action review/VHF | No numeric `state`, no `safety_limits`. |
| Base raw state | `allowed_actions`, `state`, `safety_limits`, optional history/action review/VHF | No prose briefing under the normal configuration. |
| Raw state + Eriksen | Raw-state fields plus `briefing` containing rendered Eriksen assessment | Mixed input; not the canonical raw baseline. |
| OpenAI privileged teacher | `allowed_actions`, `briefing`, `state`, `deterministic_colregs_oracle`, optional `teacher_candidate_lookahead`, action review, and VHF | Teacher-only evidence is removed before student SFT preparation. This is not raw-state runtime mode. |
| COLREG RAG | Base payload wrapped in text plus retrieved COLREG provisions | Retrieval is appended to the effective user message, not inserted as a JSON field. |
| COLREG decision tree | Base payload wrapped in text plus selected simplified guidance | Selection is appended to the effective message, not inserted into `state`. |
| MAIB-RAG critic captain | OOW payload unchanged; captain receives a separate compact bridge state, OOW proposal, VHF list, and retrieved excerpts | MAIB evidence is not part of the OOW raw state. |
| Narrative-only SFT ablation | Plain narrative string | No JSON wrapper, numeric state, safety limits, action review, or privileged evidence. |

Reasoning mode changes the system-prompt thinking directive but does not add a
raw-state field. The fine-tuned local OOW and OpenAI API paths reject the
`raw-state` input profile in the current implementation.
