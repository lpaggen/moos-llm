# OOW action space: fine-tuned model, COLREG RAG disabled

Return one JSON object with exactly these five fields:

```json
{
  "action": "maintain_course",
  "degrees": 0.0,
  "speed": 0.0,
  "reason": "A brief explanation based on the supplied observations.",
  "auxiliary_commands": []
}
```

Choose exactly one main action:

| action | meaning | degrees | speed |
|---|---|---|---|
| maintain_course | Keep the current helm plan without issuing a new command. | 0 | 0 |
| alter_course | Issue a temporary waypoint 200 metres along the altered heading, relative to the current course. The mission route resumes automatically on completion. | Nonzero number from -60 to 60 | 0 |
| set_speed | Set an absolute target speed in metres per second. | 0 | Number greater than 0 |
| stop | Stop the vessel; may interrupt a maneuver. | 0 | 0 |
| resume | Resume movement after stopping, pausing, or stalling. | 0 | 0 |
| skip | Issue no command and wait for another observation. Used when stopped or paused and resuming is unsafe. | 0 | 0 |

- Positive degrees turn to starboard (right); negative degrees turn to port (left).
- Degrees specify a relative turn, not an absolute heading.
- Output speed is in metres per second even when the narrative shows knots: m/s = knots / 1.943844.
- For actions other than set_speed, speed=0 is an unused argument; it does not command a stop.
- There is no schema maximum for set_speed. Choose a target consistent with the supplied vessel facts.
- reason must contain 1–512 characters. Ground it in the observations; cite rule numbers only when supported.
- Do not add fields such as distance_m, contact, channel, or message to the main decision.

State-dependent instructions:

- Follow the supplied allowed_actions.
- A new course or speed command may replace the current temporary maneuver.
- Underway: skip is not permitted by the system prompt.
- Stopped or paused: choose resume if clear; otherwise choose skip and name the observed hazard.
- The numeric schema lists all six actions. The stopped/underway restrictions come from the prompt, not an automatic schema filter.

Auxiliary commands:

- auxiliary_commands must be an array, including when empty.
- Include at most two entries: at most one send_vhf and at most one sound_horn.
- They accompany the main action; they are not main-action choices.
- skip requires an empty array.

VHF entry:

```json
{"type": "send_vhf", "channel": 16, "message": "Your message here."}
```

- channel: integer from 1 to 88.
- message: string of 1–512 characters.
- No additional fields.

Horn entry:

```json
{"type": "sound_horn", "signal": "altering_course_to_starboard"}
```

signal must be one of:

- altering_course_to_starboard: only with alter_course and positive degrees.
- altering_course_to_port: only with alter_course and negative degrees.
- doubt_or_danger: may accompany any main action except skip.

No additional fields. A horn is optional; when included, its signal must match the action.

Example turn with a matching horn:

```json
{
  "action": "alter_course",
  "degrees": 30.0,
  "speed": 0.0,
  "reason": "The supplied traffic observations support a starboard alteration.",
  "auxiliary_commands": [
    {"type": "sound_horn", "signal": "altering_course_to_starboard"}
  ]
}
```

This describes the numeric fine-tuned runtime interface. The COLREG RAG path currently uses string course/speed intents and requires an empty auxiliary array instead.
