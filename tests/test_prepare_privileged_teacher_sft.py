import unittest

from sft.prepare_privileged_teacher_sft import privileged_reason_marker


class PrivilegedTeacherSftTests(unittest.TestCase):
    def test_rejects_lookahead_only_language(self):
        self.assertEqual(
            privileged_reason_marker(
                "The candidate projection shows a projected minimum separation of 620 m."
            ),
            "candidate projection",
        )
        self.assertEqual(
            privileged_reason_marker("The lookahead table marks this action safe."),
            "lookahead table",
        )

    def test_accepts_student_visible_cpa_reasoning(self):
        self.assertIsNone(privileged_reason_marker(
            "TS1 is closing with CPA 430 m and TCPA 180 s. Rule 16 requires early action."
        ))


if __name__ == "__main__":
    unittest.main()
