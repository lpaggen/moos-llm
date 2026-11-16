import json

from tools.run_maib_extraction_pilot import (
    build_reasoning_records,
    card_query,
    paired_cluster_bootstrap,
    ranking_metrics,
    reciprocal_rank_fusion,
    reasoning_schema,
)


def test_generic_query_is_independent_of_all_generated_card_content():
    card = {
        "encounter_types": ["crossing"],
        "roles": ["give_way"],
        "encounter_phases": ["action_required"],
        "warning_signs": ["Constant bearing and decreasing range."],
        "investigator_finding": "SECRET FINDING",
        "supporting_quotes": [{"page": 3, "quote": "SECRET QUOTE"}],
    }
    query = card_query(card, "generic")
    assert "crossing" not in query
    assert "Constant bearing" not in query
    assert "SECRET FINDING" not in query
    assert "SECRET QUOTE" not in query


def test_diagnostic_query_modes_add_only_requested_card_fields():
    card = {
        "encounter_types": ["crossing"],
        "roles": ["give_way"],
        "encounter_phases": ["action_required"],
        "warning_signs": ["Constant bearing and decreasing range."],
    }
    metadata = card_query(card, "metadata")
    enriched = card_query(card, "enriched")
    assert "crossing" in metadata
    assert "Constant bearing" not in metadata
    assert "Constant bearing" in enriched


def test_rrf_rewards_items_supported_by_both_rankings():
    fused = reciprocal_rank_fusion([[1, 2, 3], [2, 1, 4]])
    assert fused[:2] == [1, 2]
    assert set(fused) == {1, 2, 3, 4}


def test_page_metrics_deduplicate_and_score_gold_pages():
    metrics = ranking_metrics([4, 8, 9, 12], {8, 12})
    assert metrics["hit_at_1"] == 0.0
    assert metrics["hit_at_3"] == 1.0
    assert metrics["gold_page_recall_at_5"] == 1.0
    assert metrics["mrr"] == 0.5


def test_bootstrap_clusters_correlated_cards_by_report():
    base = [{"hit_at_5": 0.0, "mrr": 0.0, "ndcg_at_5": 0.0}] * 3
    candidate = [{"hit_at_5": 1.0, "mrr": 1.0, "ndcg_at_5": 1.0}] * 3
    result = paired_cluster_bootstrap(base, candidate, ["report-a", "report-a", "report-b"], samples=20)
    assert result["hit_at_5"]["clusters"] == 2
    assert result["hit_at_5"]["observed_delta"] == 1.0


def test_reasoning_record_revalidates_quote_and_refuses_visual_inference():
    cards = [{
        "card_id": "card-1",
        "report_title": "Example",
        "source_file": "accidents/example.pdf",
        "pages": [2],
        "encounter_types": ["crossing"],
        "roles": ["give_way"],
        "encounter_phases": ["action_required"],
        "warning_signs": ["Decreasing range."],
        "causal_mechanisms": ["delayed_action"],
        "investigator_finding": "Action was delayed.",
        "transferable_lesson": "Act after risk is established.",
        "non_transfer_conditions": ["No collision risk."],
        "supporting_quotes": [{"page": 2, "quote": "The avoiding action was delayed."}],
        "review_status": "machine_validated_needs_human_review",
    }]
    documents = [{
        "source_file": "accidents/example.pdf",
        "page": 2,
        "text": "The avoiding action was delayed. Figure 3 shows both tracks.",
    }]
    inventory = [{
        "source_file": "accidents/example.pdf",
        "page": 2,
        "embedded_image_objects": 1,
        "table_or_structured_layout_cue": False,
    }]
    records, summary = build_reasoning_records(cards, documents, inventory)
    record = records[0]
    assert summary["quotes_reverified_against_local_index"] == 1
    assert record["evidence"][0]["quote_reverified_against_local_index"] is True
    assert record["evidence"][0]["page_visual_context"]["visual_content_interpreted"] is False
    assert record["quality"]["human_review_required"] is True


def test_schema_is_strict_at_top_level_and_for_evidence():
    schema = reasoning_schema()
    assert schema["additionalProperties"] is False
    evidence = schema["properties"]["evidence"]
    assert evidence["minItems"] == 1
    assert evidence["items"]["additionalProperties"] is False
    assert json.dumps(schema)
