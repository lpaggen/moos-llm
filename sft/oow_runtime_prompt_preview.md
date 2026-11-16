# Runtime prompt preview

Reconstructed from a saved observation with the updated runtime.

## System message

You are the Officer of the Watch of a simulated autonomous surface vessel.
Infer the appropriate action from the supplied observable navigation facts.
When the vessel is underway, choose exactly one action: maintain_course, alter_course, set_speed, stop, or resume. Skip is not an underway action.
Only when the vessel is stopped or paused, choose exactly one of: resume or skip. Monitor the contacts and resume as soon as the observed situation is clear. If no specific observed hazard requires the vessel to remain stopped, choose resume. Choose skip only when a specific observed hazard still makes resuming unsafe, and identify that hazard in the reason. Do not choose maintain_course for a stopped or paused vessel.
Positive course-change degrees mean starboard; negative degrees mean port. Only alter_course may have non-zero degrees, within -60 to 60. Only set_speed may have a positive non-zero speed; all other actions require speed 0.
auxiliary_commands may contain at most one send_vhf command and one sound_horn command. A course-alteration horn signal must match the selected turn direction.
Return only one JSON object with action, degrees, speed, reason, and auxiliary_commands. Ground the concise reason in the observations. Cite the applicable COLREG Rule number(s) when they support the choice; do not invent a rule citation. /no_think

## User message

```json
{
  "state": {
    "observed_at_s": 8956817300.57,
    "own_ship": {
      "name": "LLM_SHIP",
      "vessel_type": "ship",
      "x": 0.0,
      "y": -787.7,
      "heading": 0.0,
      "speed": 0.0,
      "target_speed": 0.0,
      "recovery_speed": 6.17,
      "movement_status": "paused",
      "stopped": true,
      "paused": true,
      "colregs_vessel_type": "power_driven"
    },
    "mission": {
      "x": 0.0,
      "y": 5556.0
    },
    "contacts": [
      {
        "name": "IMAZU_TS1",
        "vessel_type": "ship",
        "colregs_vessel_type": "power_driven",
        "x": -0.0,
        "y": -893.5,
        "heading": 180.0,
        "speed": 5.0,
        "range_m": 106,
        "relative_bearing_deg": 180.0,
        "sector": "astern",
        "closing_speed": -5.0,
        "projection_status": "no_future_cpa",
        "age_s": 0.15,
        "track_quality": "fresh",
        "track_generation": 0,
        "track_reacquired": false
      }
    ],
    "conditions": {
      "visibility_condition": "clear",
      "visibility_range_m": 10000.0,
      "narrow_channel": false,
      "traffic_separation_scheme": false
    }
  },
  "allowed_actions": [
    "maintain_course",
    "alter_course",
    "set_speed",
    "stop",
    "resume",
    "skip"
  ],
  "briefing": "CURRENT BRIDGE STATE\n\nLLM_SHIP is paused at (0.000, -0.425) nautical miles, heading 0.0 degrees, speed 0.00 knots. Target speed is 0.00 knots. Normal cruise target is 11.99 knots.\n\nOwn-ship vessel type is power driven.\n\nFixed mission destination is at (0.000, 3.000) nautical miles.\n\nNAVIGATION SCALE\n\nThe configured caution envelope requires both CPA below 1.000 nautical miles and TCPA no greater than 600 seconds. The intervention envelope requires both CPA below 0.500 nautical miles and TCPA no greater than 300 seconds. The configured critical CPA threshold is 0.300 nautical miles; the emergency-action horizon is 120 seconds. The configured bow-crossing clearance for forward crossing or head-on traffic is 1.000 nautical miles.\n\nSTOPPED VESSEL STATE\n\nThe vessel is stopped, paused, stalled, or has zero observed speed.\n\nTracked contacts:\n\n- Contact IMAZU_TS1 (power driven) at (-0.000, -0.482) nautical miles: range 0.057 nautical miles, astern (relative bearing 180.0 degrees). Heading 180.0 degrees, speed 9.72 knots, closing speed -9.72 knots. No future CPA is projected under the current motion.\n\nOPERATING CONDITIONS\n\nVisibility is clear; assessed visibility range is 5.400 nautical miles; narrow-channel context is not indicated; traffic-separation context is not indicated.\n\nRECENT TRAFFIC DEVELOPMENTS (last 55.7 seconds)\n\n- IMAZU_TS1 range decreased from 0.094 nautical miles to 0.057 nautical miles.\n- IMAZU_TS1 relative bearing changed from 0.0 to 180.0 degrees; relative bearing decreased by 180.0 degrees.\n- IMAZU_TS1 closing speed decreased from 9.72 knots to -9.72 knots.\n- IMAZU_TS1 changed from a valid future CPA to no future CPA."
}
```
