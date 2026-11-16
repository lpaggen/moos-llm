from collections import Counter

from tools.benchmark_oow_action_consistency import (
    control_signature,
    entropy,
    normalize_thinking_prompt,
    pairwise_disagreement,
)


def test_prompt_thinking_mode_is_replaced_not_duplicated():
    source = "system\n/no_think"
    assert normalize_thinking_prompt(source, True).endswith("/think")
    assert "/no_think" not in normalize_thinking_prompt(source, True)
    assert normalize_thinking_prompt(source, False).endswith("/no_think")


def test_control_signature_distinguishes_direction_and_magnitude():
    assert control_signature({"action": "alter_course", "degrees": 30, "speed": 0}) == "alter_course:starboard:30"
    assert control_signature({"action": "alter_course", "degrees": -45, "speed": 0}) == "alter_course:port:45"


def test_consistency_metrics_are_zero_for_unanimous_actions():
    counts = Counter({"maintain_course": 100})
    assert entropy(counts) == 0.0
    assert pairwise_disagreement(counts) == 0.0


def test_pairwise_disagreement_for_even_split():
    assert pairwise_disagreement(Counter({"maintain_course": 50, "alter_course": 50})) == 0.5
