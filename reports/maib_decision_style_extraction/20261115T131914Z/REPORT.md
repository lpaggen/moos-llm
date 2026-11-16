# MAIB decision-style extraction test

Passages validated: **4**  
Passages with extractable lessons: **2**  
Lesson cards extracted: **7**  
Failures: **0**

## Passage classifications

| Type | Count |
|---|---:|
| background_or_method | 2 |
| investigator_analysis | 1 |
| mixed | 1 |

## Extracted lessons

| Chunk | Finding | Transferable lesson | Potential runtime use |
|---|---|---|---|
| maib-14a8779cc4e3 | The submarine OOW underestimated the ferry's speed and almost certainly assessed that substantially more time was available for avoidance than was actually available. | Check the speed assumptions underlying an available-time assessment; an underestimated approaching contact speed can create unjustified confidence that action can wait. | Flag an avoidance-timing justification that depends on uncertain or contradicted contact speed for verification and uncertainty-aware reassessment. |
| maib-14a8779cc4e3 | The submarine's turn towards the ferry made the vessels converge, reducing the estimated time available when combined speeds were considered. | Evaluate avoidance proposals using the resulting relative motion, including own-ship movement, rather than relying on contact travel time or the intended purpose of the turn. | Require a post-proposal relative-motion assessment and identify proposals that shorten the available response time despite an intention to open away. |
| maib-14a8779cc4e3 | The submarine team's insufficient action allowed an unsafe intrusion inside its go-deep range, while rapid and effective action by the ferry bridge team reduced collision risk. | Assess whether avoidance action actually achieves the required separation; prompt, effective intervention can reduce risk when an encounter remains unsafe. | Challenge passive continuation or nominal avoidance when live evidence shows that the applicable separation requirement remains unmet. |
| maib-d272bb7dbf43 | Post-event RN analysis explicitly linked overestimation of the ferry's range to flawed command-team decisions. | Where range estimates are uncertain or inconsistent, assess the reliability of the range supporting a navigation decision rather than treating apparent distance as established safety margin. | Flag a proposal whose safety justification depends on an inadequately supported range estimate and identify the need to resolve that uncertainty. |
| maib-d272bb7dbf43 | The Karen investigation found that classifying a fishing vessel as a merchant vessel caused significant overestimation of its range and speed and contributed to the collision. | Treat vessel identity and motion estimates as linked uncertainties when the estimation process depends on assumed contact type. Reconsider the dependent estimates when classification is doubtful. | Challenge confidence in a risk assessment when its range and speed inputs inherit uncertainty from an unverified classification; seek corroboration before relying on those inputs. |
| maib-d272bb7dbf43 | The submarine team incorrectly assessed most local contacts as merchant vessels rather than trawlers. Investigators identified the resulting traffic environment as hazardous and the attempted submerged transit as unsafe. | A shared mistaken assumption about multiple contacts can invalidate an area-wide navigation assessment. Check the evidence for traffic composition rather than extending one assumed classification across the traffic picture. | Prompt reassessment of a transit proposal when its overall traffic model relies on common, weakly supported contact classifications. |
| maib-d272bb7dbf43 | The submarine snagged Karen's trawl wires. Its team perceived no collision risk or need for avoidance because the submarine was at what was considered a safe depth. | A safety margin against a vessel itself does not necessarily address hazards from its deployed gear. Where gear presence and exposure are established, include that hazard rather than relying solely on the condition considered safe for hull separation. | Identify an incomplete clearance assessment when a proposal addresses the vessel track but not explicitly evidenced fishing-gear exposure. |
