import json
import math
from pathlib import Path

from tools.build_maib_lesson_cards import (
    LessonCardCandidate,
    SupportingQuote,
    build_requests,
    cost_summary,
    export_index,
    quote_is_supported,
    resolve_reasoning_effort,
    select_passages,
    validate_prepared_requests,
)
from tools.build_maib_lesson_cards_gpt_screened import validate_generate
from types import SimpleNamespace


def test_select_passages_prefers_investigator_analysis():
    chunks = [
        {"page": 1, "chunk_id": "narrative", "text": "The vessel arrived in port."},
        {
            "page": 8,
            "chunk_id": "analysis",
            "text": "ANALYSIS The bridge team did not recognise the risk of collision or keep an effective lookout.",
        },
    ]
    selected = select_passages(chunks, 1)
    assert [item["chunk_id"] for item in selected] == ["analysis"]


def test_build_requests_groups_chunks_by_report(tmp_path: Path):
    index = tmp_path / "index.jsonl"
    records = [
        {"chunk_id": "a", "report_title": "Alpha", "source_file": "a.pdf", "page": 2, "text": "Analysis of collision avoidance and lookout."},
        {"chunk_id": "b", "report_title": "Alpha", "source_file": "a.pdf", "page": 3, "text": "Conclusion about the bridge team."},
        {"chunk_id": "c", "report_title": "Beta", "source_file": "b.pdf", "page": 1, "text": "A machinery failure."},
    ]
    index.write_text("".join(json.dumps(item) + "\n" for item in records), encoding="utf-8")
    requests, manifests, tokens = build_requests(index, "gpt-6.1-terra", 8, 1600)
    assert len(requests) == len(manifests) == 2
    assert tokens > 0
    assert requests[0]["url"] == "/v1/chat/completions"
    assert requests[0]["body"]["response_format"]["json_schema"]["strict"] is True


def test_sol_uses_low_reasoning_and_rejects_none(tmp_path: Path):
    assert resolve_reasoning_effort("gpt-6.1-sol", "auto") == "low"
    try:
        resolve_reasoning_effort("gpt-6.1-sol", "none")
    except ValueError:
        pass
    else:
        raise AssertionError("Sol accepted unsupported reasoning_effort=none")

    batch = tmp_path / "batch.jsonl"
    batch.write_text(json.dumps({
        "custom_id": "one",
        "body": {
            "model": "gpt-6.1-sol",
            "reasoning_effort": "none",
            "response_format": {"json_schema": {"strict": True}},
        },
    }) + "\n", encoding="utf-8")
    try:
        validate_prepared_requests(batch)
    except ValueError as exc:
        assert "Run prepare again" in str(exc)
    else:
        raise AssertionError("pre-submit validation accepted an invalid Sol request")


def test_quote_validation_is_whitespace_tolerant_but_page_bound():
    manifest = {
        "selected_chunks": [
            {"page": 7, "text": "The bridge team failed to keep an effective lookout."},
            {"page": 8, "text": "Different page."},
        ]
    }
    assert quote_is_supported(
        SupportingQuote(page=7, quote="bridge team failed   to keep an effective lookout"), manifest
    )
    assert not quote_is_supported(
        SupportingQuote(page=8, quote="bridge team failed to keep an effective lookout"), manifest
    )


def test_cost_summary_reports_expected_and_maximum():
    result = cost_summary(100, 800_000, 1600, 0.5, 2.5)
    assert math.isclose(result["expected_total_cost_usd"], 0.5375)
    assert math.isclose(result["maximum_total_cost_usd"], 0.8)


def test_candidate_schema_rejects_uncontrolled_values():
    try:
        LessonCardCandidate.model_validate({
            "pages": [1],
            "encounter_types": ["made_up"],
            "roles": ["uncertain"],
            "encounter_phases": ["monitoring"],
            "warning_signs": ["warning"],
            "causal_mechanisms": ["other"],
            "investigator_finding": "A sufficiently long investigator finding.",
            "transferable_lesson": "A sufficiently long transferable lesson.",
            "non_transfer_conditions": ["When facts differ."],
            "supporting_quotes": [{"page": 1, "quote": "A valid quote."}],
        })
    except ValueError:
        pass
    else:
        raise AssertionError("invalid controlled vocabulary was accepted")


def test_export_index_produces_runtime_retriever_documents(tmp_path: Path):
    cards = tmp_path / "cards.jsonl"
    output = tmp_path / "index.jsonl"
    cards.write_text(json.dumps({
        "card_id": "maib-lesson-one",
        "report_title": "Example report",
        "source_file": "accidents/example.pdf",
        "source_type": "published_investigation",
        "review_status": "machine_validated_needs_human_review",
        "pages": [8],
        "encounter_types": ["crossing"],
        "roles": ["give_way"],
        "encounter_phases": ["risk_developing"],
        "warning_signs": ["Constant bearing and decreasing range."],
        "causal_mechanisms": ["delayed_action"],
        "investigator_finding": "The investigation found that avoiding action was delayed.",
        "transferable_lesson": "Act in sufficient time after collision risk is established.",
        "non_transfer_conditions": ["Collision risk has not been established."],
        "supporting_quotes": [{"page": 8, "quote": "avoiding action was delayed until close quarters"}],
    }) + "\n", encoding="utf-8")

    export_index(SimpleNamespace(cards=cards, output=output))

    record = json.loads(output.read_text(encoding="utf-8"))
    assert record["chunk_id"] == "maib-lesson-one"
    assert record["source_type"] == "published_investigation_lesson_card"
    assert record["encounter_types"] == ["crossing"]
    assert "Transferable lesson:" in record["text"]
    assert "Do not transfer this analogy when:" in record["text"]


def test_report_level_validation_exports_only_quoted_pages(tmp_path: Path):
    work_dir = tmp_path / "report-level"
    work_dir.mkdir()
    identifier = "maib-card-example"
    manifest = {
        "custom_id": identifier,
        "source_file": "accidents/example.pdf",
        "report_title": "Example report",
        "selected_chunks": [
            {"chunk_id": "one", "page": 7, "text": "Context without a quoted finding."},
            {"chunk_id": "two", "page": 8, "text": "The avoiding action was delayed until close quarters."},
        ],
    }
    candidate = {
        "pages": [7, 8],
        "encounter_types": ["crossing"],
        "roles": ["give_way"],
        "encounter_phases": ["action_required"],
        "warning_signs": ["Avoiding action was delayed."],
        "causal_mechanisms": ["delayed_action"],
        "investigator_finding": "The investigation found that avoiding action was delayed.",
        "transferable_lesson": "Act in sufficient time after collision risk is established.",
        "non_transfer_conditions": ["Collision risk has not been established."],
        "supporting_quotes": [{"page": 8, "quote": "avoiding action was delayed until close quarters"}],
    }
    response = {
        "custom_id": identifier,
        "response": {
            "body": {
                "choices": [{
                    "message": {"content": json.dumps({"cards": [candidate]})},
                    "finish_reason": "stop",
                }],
                "usage": {"prompt_tokens": 100, "completion_tokens": 40},
            }
        },
    }
    (work_dir / "manifest.jsonl").write_text(json.dumps(manifest) + "\n", encoding="utf-8")
    (work_dir / "batch_output.jsonl").write_text(json.dumps(response) + "\n", encoding="utf-8")

    validate_generate(SimpleNamespace(work_dir=work_dir))

    card = json.loads((work_dir / "cards.machine_validated.jsonl").read_text(encoding="utf-8"))
    summary = json.loads((work_dir / "validation_summary.json").read_text(encoding="utf-8"))
    assert card["pages"] == [8]
    assert summary["cards_with_unquoted_declared_pages"] == 0
    assert summary["status"] == "complete"
