import unittest

from aye.model import telemetry


class TestTelemetry(unittest.TestCase):
    def setUp(self):
        # Global in-memory state: reset between tests.
        telemetry.reset()
        telemetry.set_enabled(False)

    def tearDown(self):
        telemetry.reset()
        telemetry.set_enabled(False)

    def test_set_enabled_and_is_enabled(self):
        self.assertFalse(telemetry.is_enabled())
        telemetry.set_enabled(True)
        self.assertTrue(telemetry.is_enabled())
        telemetry.set_enabled(False)
        self.assertFalse(telemetry.is_enabled())

    def test_reset_clears_counts_without_disabling(self):
        telemetry.set_enabled(True)
        telemetry.record_command("help", has_args=False)
        self.assertEqual(
            telemetry.build_payload(),
            {"v": 1, "events": [{"name": "help", "count": 1}]},
        )

        telemetry.reset()
        self.assertTrue(telemetry.is_enabled())
        self.assertEqual(telemetry.build_payload(), {"v": 1, "events": []})

    def test_record_command_noop_when_disabled(self):
        telemetry.set_enabled(False)
        telemetry.record_command("help", has_args=False)
        self.assertIsNone(telemetry.build_payload())

    def test_record_command_ignores_empty_or_whitespace_token(self):
        telemetry.set_enabled(True)
        telemetry.record_command("", has_args=False)
        telemetry.record_command("   ", has_args=True)
        self.assertEqual(telemetry.build_payload(), {"v": 1, "events": []})

    def test_record_command_sanitizes_lowercase_and_strips(self):
        telemetry.set_enabled(True)
        telemetry.record_command("  HeLp  ", has_args=False)
        self.assertEqual(
            telemetry.build_payload(),
            {"v": 1, "events": [{"name": "help", "count": 1}]},
        )

    def test_record_command_sanitizes_slash_prefixed_command(self):
        telemetry.set_enabled(True)
        telemetry.record_command("/all", has_args=True)
        self.assertEqual(
            telemetry.build_payload(),
            {"v": 1, "events": [{"name": "all <args>", "count": 1}]},
        )

    def test_record_command_sanitizes_paths_to_basename_posix(self):
        telemetry.set_enabled(True)
        telemetry.record_command("/usr/local/bin/python", has_args=False)
        self.assertEqual(
            telemetry.build_payload(),
            {"v": 1, "events": [{"name": "python", "count": 1}]},
        )

    def test_record_command_sanitizes_paths_to_basename_windows_like(self):
        telemetry.set_enabled(True)
        telemetry.record_command(r"C:\Windows\System32\cmd.exe", has_args=False)
        self.assertEqual(
            telemetry.build_payload(),
            {"v": 1, "events": [{"name": "cmd.exe", "count": 1}]},
        )

    def test_record_command_includes_prefix_and_args_suffix(self):
        telemetry.set_enabled(True)
        telemetry.record_command("git", has_args=True, prefix="cmd:")
        telemetry.record_command("help", has_args=False, prefix="aye:")

        # Both counts are 1; sorting should be by name asc.
        self.assertEqual(
            telemetry.build_payload(),
            {
                "v": 1,
                "events": [
                    {"name": "aye:help", "count": 1},
                    {"name": "cmd:git <args>", "count": 1},
                ],
            },
        )

    def test_record_command_prefix_is_normalized(self):
        telemetry.set_enabled(True)
        telemetry.record_command("GIT", has_args=False, prefix="  CMD: ")
        self.assertEqual(
            telemetry.build_payload(),
            {"v": 1, "events": [{"name": "cmd:git", "count": 1}]},
        )

    def test_record_llm_prompt_noop_when_disabled(self):
        telemetry.set_enabled(False)
        telemetry.record_llm_prompt("LLM")
        self.assertIsNone(telemetry.build_payload())

    def test_record_llm_prompt_counts_allowed_kinds(self):
        telemetry.set_enabled(True)
        telemetry.record_llm_prompt("LLM")
        telemetry.record_llm_prompt("LLM <with>")
        telemetry.record_llm_prompt("LLM @")

        payload = telemetry.build_payload()
        self.assertEqual(payload["v"], 1)
        self.assertEqual(
            payload["events"],
            [
                {"name": "LLM", "count": 1},
                {"name": "LLM <with>", "count": 1},
                {"name": "LLM @", "count": 1},
            ],
        )

    def test_record_llm_prompt_invalid_kind_coerces_to_llm(self):
        telemetry.set_enabled(True)
        telemetry.record_llm_prompt("SOMETHING ELSE")
        telemetry.record_llm_prompt("LLM")
        self.assertEqual(
            telemetry.build_payload(),
            {"v": 1, "events": [{"name": "LLM", "count": 2}]},
        )

    def test_build_payload_disabled_returns_none(self):
        telemetry.set_enabled(False)
        self.assertIsNone(telemetry.build_payload())

    def test_build_payload_is_sorted_by_count_desc_then_name_asc(self):
        telemetry.set_enabled(True)

        telemetry.record_command("b", has_args=False)
        telemetry.record_command("b", has_args=False)
        telemetry.record_command("a", has_args=False)

        payload = telemetry.build_payload()
        self.assertEqual(
            payload,
            {
                "v": 1,
                "events": [
                    {"name": "b", "count": 2},
                    {"name": "a", "count": 1},
                ],
            },
        )

    def test_build_payload_top_n_bounds(self):
        telemetry.set_enabled(True)

        telemetry.record_command("a", has_args=False)
        telemetry.record_command("b", has_args=False)
        telemetry.record_command("c", has_args=False)

        payload_2 = telemetry.build_payload(top_n=2)
        self.assertEqual(len(payload_2["events"]), 2)

        payload_0 = telemetry.build_payload(top_n=0)
        self.assertEqual(payload_0, {"v": 1, "events": []})

        payload_neg = telemetry.build_payload(top_n=-5)
        self.assertEqual(payload_neg, {"v": 1, "events": []})


if __name__ == "__main__":
    unittest.main()
