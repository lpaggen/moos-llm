import unittest

from tools.check_openai_teacher import validate_key_shape


class CheckOpenAITeacherTests(unittest.TestCase):
    def test_rejects_missing_and_placeholder_keys(self):
        for value in (None, "", "your-key", "sk-your-key", "<api-key>", "..."):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    validate_key_shape(value)

    def test_accepts_credible_key_shape_without_exposing_it(self):
        value = "sk-test-abcdefghijklmnopqrstuvwxyz"
        self.assertEqual(validate_key_shape(value), value)
