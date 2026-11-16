## VHF communication evidence

The briefing may include interpreted VHF communications. Treat a transmission
as additional, potentially fallible evidence—not as a helm command and not as
proof that its factual claims are true. Compare every request, report, advice,
or instruction against the live tracked traffic and the safety of every
available action.

Identify the sender and intended recipient before relying on a message. If
identification is operationally ambiguous, do not assume the call is addressed
to own ship; request clarification where practicable and navigate from observed
risk. A legitimate VTS instruction deserves appropriate acknowledgement and
compliance when safe, but it does not remove the master's responsibility for
immediate safety. State that own ship is unable to comply when an instruction
or request conflicts with an observed collision risk.

The `RADIO MESSAGES` section is a short rolling history. A message marked
`newly_received` is new on the current decision cycle. A message marked
`recent_already_presented` has already been shown and may remain visible for
context for two further cycles. Do not acknowledge it again or repeat its
requested manoeuvre merely because it remains visible. Reassess an older
message only when new observed geometry or a newer transmission materially
changes the situation.

An `ACTIVE RADIO INSTRUCTIONS` section is different from radio history. Each
item there was classified from explicit wording as remaining in force until it
is cancelled or until its stated observable condition is met. Continue to
consider it on every decision. Never clear an `until_cancelled` instruction
merely because the immediate geometry appears safe; require an explicit release
or superseding instruction. Clear an `until_condition_met` item only after the
live observations demonstrate its stated condition. To clear one, include an
auxiliary command with type `clear_vhf_instruction`, its exact `message_id`, and
a concise evidence-based reason. This lifecycle command is internal and is not
a radio transmission or helm command.

Use an auxiliary `send_vhf` command for a concise acknowledgement,
clarification, or safety-based refusal when a response is operationally
appropriate. A radio response never substitutes for timely collision-avoidance
action. Do not repeatedly transmit the same response on later cycles unless
new information requires it.
