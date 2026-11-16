# COLREGs — simplified ship guidance

The 20 rule titles and simplified guidance below are copied unchanged from
[the RAG guidance file](experiments/qwen8b_colregs_rag/colregs_ship_guidance.json).
This is the existing simplified subset, not the complete COLREG corpus.

The [full-text corpus](agents/colregs/colregs.json) is separate. The RAG
[retriever](experiments/qwen8b_colregs_rag/retrieval.py) ranks the full-text rules
and renders selected rules as this concise guidance for the model.

## Rule 2 — Responsibility

- Follow the rules and act carefully.
- Consider all dangers.
- Break a rule only when needed to avoid immediate danger.

## Rule 5 — Look-out

- Keep watching and listening.
- Use all available tools to check for danger.

## Rule 6 — Safe Speed

- Go slowly enough to turn or stop safely.
- Adjust speed for visibility, traffic, weather and how your ship handles.

## Rule 7 — Risk of Collision

- Use all available information to check for a possible collision.
- If unsure, assume a collision is possible.
- Do not decide from too little information.
- A ship getting closer while staying in the same direction from you is dangerous.
- A nearby ship can still be dangerous even if its direction changes.

## Rule 8 — Action to Avoid Collision

- Act early and clearly.
- Make one clear change instead of many small changes.
- Turn clearly if there is enough space.
- Check that your action does not create another danger.
- Leave enough space and keep checking until safely past.
- Slow down or stop if needed.

## Rule 9 — Narrow Channels

- Stay near the right edge when safe.
- Do not cross in front of a ship that needs the deep, narrow route.
- Use the required horn signals when passing a ship or approaching a hidden bend.

## Rule 10 — Traffic Separation Schemes

- Use the correct lane and follow its traffic direction.
- Join or leave at the lane end when possible.
- Otherwise, join or leave at a small angle.
- If you must cross, point straight across the traffic direction.

## Rule 11 — When Ships Can See Each Other

- Use rules 11 to 18 when the ships can see each other.

## Rule 12 — Sailing Vessels

- When two ships using sails might collide, check which side each receives wind from.
- If wind comes from different sides, the ship receiving wind from its left must keep clear.
- If wind comes from the same side, the ship nearer the source of the wind must keep clear.
- If wind comes from your left and you cannot tell the other ship's wind side, keep clear.

## Rule 13 — Passing From Behind

- When catching up from behind, keep clear of the ship ahead.
- If unsure whether you are coming from behind, assume you are.
- Keep clear until fully past and safely separated.

## Rule 14 — Meeting Head-on

- When two ships using engines approach almost straight toward each other, both must turn right.
- If unsure whether this applies, assume it does.

## Rule 15 — Crossing Paths

- When two ships using engines cross paths and might collide, keep clear if the other ship is on your right.
- Avoid passing in front of it.
- Usually, turn right or slow down to pass behind it safely.
- Check for other ships before acting.
- Do not turn left just because it makes the other ship appear farther to one side.

## Rule 16 — When You Must Keep Clear

- Act early.
- Make a clear change and leave plenty of space.

## Rule 17 — When the Other Ship Must Keep Clear

- At first, keep the same direction and speed.
- You may act if the other ship is not doing enough.
- You must act when the other ship cannot prevent a collision alone.
- If a ship using engines is crossing from your left, avoid turning left when possible.

## Rule 18 — Which Ship Must Keep Clear

- When using engines, keep clear of ships using sails, fishing with equipment that limits movement, unable to steer normally, or doing work that limits movement.
- Rules for narrow routes, traffic lanes and passing from behind can change who must keep clear.
- Follow the special rules for ships needing deep water, aircraft on water and craft flying just above water.

## Rule 19 — Poor Visibility

- When visibility is poor and ships cannot see each other, use a safe speed.
- Keep engines ready for quick action.
- Check radar carefully and act early if a collision is possible.
- If turning based on radar alone, avoid turning left for a ship ahead unless passing it from behind.
- Avoid turning toward a ship beside or behind you.
- If you hear a fog signal ahead or cannot avoid getting too close, slow to the lowest speed that still allows steering.
- Stop moving if needed and continue very carefully.

## Rule 34 — Movement and Warning Signals

- When ships can see each other, a ship using engines gives one short horn blast when turning right and two when turning left.
- Give at least five short, quick horn blasts if unsure what the other ship is doing or whether it is doing enough.
- Use the special horn signals when passing from behind in a narrow route.

## Rule 35 — Sound Signals in Poor Visibility

- In or near poor visibility, repeat the required sound signal.
- Use the signal that matches your ship and whether it is moving or stopped.

## Rule 36 — Getting Attention

- Use a clear signal to get attention if needed.
- Do not use a signal that could be confused with another official signal.

## Rule 37 — Emergency Signals

- If your ship is in serious danger and needs help, use the required emergency signals.
