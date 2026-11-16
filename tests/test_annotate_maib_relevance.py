import csv
from pathlib import Path
import tempfile
import unittest

from tools.annotate_maib_relevance import (
    REQUIRED_FIELDS,
    Style,
    next_unlabelled,
    progress,
    read_sheet,
    render_row,
    write_sheet_atomic,
)


def example_row(identifier: str = "P001", label: str = "") -> dict[str, str]:
    return {
        "annotation_id": identifier,
        "state_id": "S001",
        "navigation_state": "A crossing contact is rapidly closing.",
        "card_finding": "The contact was not monitored.",
        "transferable_lesson": "Continue systematic monitoring.",
        "non_transfer_conditions": '["Does not apply when tracking is reliable."]',
        "supporting_quotations": '[{"page": 4, "quote": "No plot was maintained."}]',
        "relevance_label": label,
        "reviewer_notes": "",
    }


class RelevanceAnnotatorTests(unittest.TestCase):
    def test_atomic_round_trip_preserves_multiline_csv_fields(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "labels.csv"
            rows = [example_row()]
            rows[0]["navigation_state"] += "\nSecond line."
            write_sheet_atomic(path, list(REQUIRED_FIELDS), rows)
            fields, loaded = read_sheet(path)
            self.assertEqual(fields, list(REQUIRED_FIELDS))
            self.assertEqual(loaded, rows)

    def test_resume_finds_first_blank_and_wraps(self):
        rows = [example_row("P001", "2"), example_row("P002"), example_row("P003")]
        self.assertEqual(next_unlabelled(rows), 1)
        self.assertEqual(next_unlabelled(rows, 2), 2)
        rows[2]["relevance_label"] = "0"
        self.assertEqual(next_unlabelled(rows, 2), 1)
        self.assertEqual(progress(rows), 2)

    def test_render_is_method_blind_and_human_readable(self):
        output = render_row(
            example_row(),
            position=0,
            total=137,
            completed=0,
            width=90,
            style=Style(False),
        )
        self.assertIn("NAVIGATION STATE", output)
        self.assertIn("INVESTIGATOR FINDING", output)
        self.assertIn("Page 4", output)
        self.assertIn("[0] Irrelevant", output)
        self.assertNotIn("retrieval method", output.lower())

    def test_reader_rejects_invalid_existing_label(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "labels.csv"
            with path.open("w", encoding="utf-8", newline="") as stream:
                writer = csv.DictWriter(stream, fieldnames=REQUIRED_FIELDS)
                writer.writeheader()
                writer.writerow(example_row(label="yes"))
            with self.assertRaisesRegex(ValueError, "invalid relevance_label"):
                read_sheet(path)


if __name__ == "__main__":
    unittest.main()
