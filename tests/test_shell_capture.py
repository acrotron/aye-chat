import os
import pytest
from unittest.mock import Mock, patch
from datetime import datetime

from aye.controller.shell_capture import (
    truncate_output,
    _trim_front_bytes,
    enforce_byte_limit,
    capture_shell_result,
    maybe_attach_shell_result,
)


class TestTruncateOutput:
    """Tests for truncate_output function."""

    def test_empty_string(self):
        result, was_truncated = truncate_output("")
        assert result == ""
        assert was_truncated is False

    def test_none_input(self):
        result, was_truncated = truncate_output(None)
        assert result is None
        assert was_truncated is False

    def test_short_text_not_truncated(self):
        text = "line1\nline2\nline3\n"
        result, was_truncated = truncate_output(text)
        assert result == text
        assert was_truncated is False

    def test_exact_max_lines_not_truncated(self):
        lines = [f"line{i}\n" for i in range(200)]
        text = "".join(lines)
        result, was_truncated = truncate_output(text)
        assert result == text
        assert was_truncated is False

    def test_exceeds_max_lines_truncated(self):
        lines = [f"line{i}\n" for i in range(250)]
        text = "".join(lines)
        result, was_truncated = truncate_output(text)
        assert was_truncated is True
        assert result.startswith("...[truncated: showing last 200 lines]\n")
        result_lines = result.splitlines()
        # 1 marker line + 200 content lines
        assert len(result_lines) == 201
        # Last line should be the last from original
        assert "line249" in result_lines[-1]

    def test_custom_max_lines(self):
        lines = [f"line{i}\n" for i in range(10)]
        text = "".join(lines)
        result, was_truncated = truncate_output(text, max_lines=5)
        assert was_truncated is True
        assert "showing last 5 lines" in result
        content_lines = result.splitlines()[1:]  # skip marker
        assert len(content_lines) == 5
        assert "line5" in content_lines[0]
        assert "line9" in content_lines[-1]

    def test_single_line_not_truncated(self):
        text = "single line"
        result, was_truncated = truncate_output(text)
        assert result == text
        assert was_truncated is False

    def test_max_lines_one(self):
        text = "line1\nline2\nline3\n"
        result, was_truncated = truncate_output(text, max_lines=1)
        assert was_truncated is True
        assert "showing last 1 lines" in result

    def test_preserves_line_endings(self):
        text = "a\nb\nc\n"
        result, was_truncated = truncate_output(text, max_lines=2)
        assert was_truncated is True
        # The last 2 lines kept are "c\n" and... let's check
        # splitlines(keepends=True) on "a\nb\nc\n" = ["a\n", "b\n", "c\n"]
        # last 2 = ["b\n", "c\n"]
        assert "b\n" in result
        assert "c\n" in result


class TestTrimFrontBytes:
    """Tests for _trim_front_bytes function."""

    def test_empty_string(self):
        assert _trim_front_bytes("", 100) == ""

    def test_fits_within_budget(self):
        text = "hello"
        assert _trim_front_bytes(text, 100) == "hello"

    def test_exact_fit(self):
        text = "hello"  # 5 bytes
        assert _trim_front_bytes(text, 5) == "hello"

    def test_trim_from_front(self):
        text = "abcdefghij"  # 10 bytes
        result = _trim_front_bytes(text, 5)
        assert result == "fghij"

    def test_zero_budget(self):
        assert _trim_front_bytes("hello", 0) == ""

    def test_negative_budget(self):
        assert _trim_front_bytes("hello", -5) == ""

    def test_multibyte_characters(self):
        # Each emoji is 4 bytes in UTF-8
        text = "\U0001f600\U0001f601\U0001f602"  # 12 bytes total
        result = _trim_front_bytes(text, 4)
        # Should get the last 4 bytes = last emoji
        assert result == "\U0001f602"

    def test_multibyte_split_boundary(self):
        # If we cut in the middle of a multi-byte char, errors='ignore' drops it
        text = "\U0001f600\U0001f601"  # 8 bytes
        result = _trim_front_bytes(text, 5)
        # Last 5 bytes: 1 byte of first emoji (invalid) + 4 bytes of second
        # The invalid partial byte is ignored
        assert result == "\U0001f601"

    def test_single_byte_budget(self):
        text = "abc"
        result = _trim_front_bytes(text, 1)
        assert result == "c"


class TestEnforceByteLimit:
    """Tests for enforce_byte_limit function."""

    def test_within_limit(self):
        stdout, stderr = enforce_byte_limit("hello", "world", max_bytes=100)
        assert stdout == "hello"
        assert stderr == "world"

    def test_exact_limit(self):
        # "hello" + "world" = 10 bytes
        stdout, stderr = enforce_byte_limit("hello", "world", max_bytes=10)
        assert stdout == "hello"
        assert stderr == "world"

    def test_trim_stderr_when_longer(self):
        stdout = "ab"  # 2 bytes
        stderr = "12345678"  # 8 bytes, total 10
        result_stdout, result_stderr = enforce_byte_limit(stdout, stderr, max_bytes=6)
        assert result_stdout == "ab"
        # stderr should be trimmed to fit: 6 - 2 = 4 bytes
        assert len(result_stderr.encode("utf-8")) <= 4

    def test_trim_stdout_when_longer(self):
        stdout = "12345678"  # 8 bytes
        stderr = "ab"  # 2 bytes, total 10
        result_stdout, result_stderr = enforce_byte_limit(stdout, stderr, max_bytes=6)
        assert result_stderr == "ab"
        assert len(result_stdout.encode("utf-8")) <= 4

    def test_trim_stdout_on_tie(self):
        """When both are equal length, stdout is trimmed first."""
        stdout = "aaaa"  # 4 bytes
        stderr = "bbbb"  # 4 bytes, total 8
        result_stdout, result_stderr = enforce_byte_limit(stdout, stderr, max_bytes=6)
        # stdout trimmed first (tie case)
        combined = len(result_stdout.encode("utf-8")) + len(result_stderr.encode("utf-8"))
        assert combined <= 6

    def test_both_empty(self):
        stdout, stderr = enforce_byte_limit("", "", max_bytes=10)
        assert stdout == ""
        assert stderr == ""

    def test_very_small_limit(self):
        stdout, stderr = enforce_byte_limit("hello world", "error msg", max_bytes=4)
        combined = len(stdout.encode("utf-8")) + len(stderr.encode("utf-8"))
        assert combined <= 4

    def test_default_max_bytes(self):
        """Default limit is 10240 bytes."""
        stdout = "x" * 6000
        stderr = "y" * 6000  # total 12000 > 10240
        result_stdout, result_stderr = enforce_byte_limit(stdout, stderr)
        combined = len(result_stdout.encode("utf-8")) + len(result_stderr.encode("utf-8"))
        assert combined <= 10240

    def test_only_stdout_exceeds(self):
        stdout = "x" * 100
        stderr = ""
        result_stdout, result_stderr = enforce_byte_limit(stdout, stderr, max_bytes=50)
        assert len(result_stdout.encode("utf-8")) <= 50
        assert result_stderr == ""

    def test_only_stderr_exceeds(self):
        stdout = ""
        stderr = "y" * 100
        result_stdout, result_stderr = enforce_byte_limit(stdout, stderr, max_bytes=50)
        assert result_stdout == ""
        assert len(result_stderr.encode("utf-8")) <= 50

    def test_multibyte_within_limit(self):
        """Multi-byte chars that fit within limit are untouched."""
        stdout = "\u00e9" * 3  # 6 bytes UTF-8
        stderr = "ab"  # 2 bytes
        result_stdout, result_stderr = enforce_byte_limit(stdout, stderr, max_bytes=10)
        assert result_stdout == stdout
        assert result_stderr == stderr


class TestCaptureShellResult:
    """Tests for capture_shell_result function."""

    @pytest.fixture
    def conf(self):
        conf = Mock()
        conf.verbose = False
        # Ensure attributes don't pre-exist
        del conf._last_shell_result
        del conf._pending_shell_attach
        return conf

    @pytest.fixture(autouse=True)
    def enable_capture(self):
        """Ensure capture is enabled for all tests in this class."""
        with patch("aye.controller.shell_capture.is_capture_disabled", return_value=False):
            yield

    def test_none_response_does_nothing(self, conf):
        """None response means plugin didn't handle -- skip."""
        capture_shell_result(conf, cmd="ls", shell_response=None)
        assert not hasattr(conf, "_last_shell_result") or conf._last_shell_result is None

    def test_interactive_command_skipped(self, conf):
        """Interactive commands (message key, no stdout) are skipped."""
        response = {"message": "Ran vim", "exit_code": 1}
        capture_shell_result(conf, cmd="vim", shell_response=response)
        assert not hasattr(conf, "_last_shell_result") or conf._last_shell_result is None

    def test_interactive_with_stdout_not_skipped(self, conf):
        """Response with both message and stdout is NOT considered interactive."""
        response = {"message": "output", "stdout": "data", "returncode": 1}
        capture_shell_result(conf, cmd="test", shell_response=response)
        assert conf._pending_shell_attach is True

    def test_successful_command_no_capture(self, conf):
        """Successful commands (returncode=0) are not captured."""
        response = {"stdout": "ok", "stderr": "", "returncode": 0}
        capture_shell_result(conf, cmd="ls", shell_response=response)
        assert not hasattr(conf, "_last_shell_result") or conf._last_shell_result is None

    def test_failed_command_captured(self, conf):
        """Failed command (returncode != 0) is captured."""
        response = {"stdout": "output", "stderr": "error", "returncode": 1}
        capture_shell_result(conf, cmd="pytest", shell_response=response)

        assert conf._pending_shell_attach is True
        result = conf._last_shell_result
        assert result["cmd"] == "pytest"
        assert result["returncode"] == 1
        assert result["stdout"] == "output"
        assert result["stderr"] == "error"
        assert result["cwd"] == os.getcwd()
        assert "timestamp" in result
        assert result["truncated"] is False

    def test_error_field_triggers_capture(self, conf):
        """Response with error field (even without returncode) triggers capture."""
        response = {"stdout": "", "stderr": "", "error": "Command not found"}
        capture_shell_result(conf, cmd="badcmd", shell_response=response)
        assert conf._pending_shell_attach is True

    def test_returncode_none_with_error(self, conf):
        """returncode=None but error present still triggers capture."""
        response = {"stdout": "", "stderr": "", "returncode": None, "error": "fail"}
        capture_shell_result(conf, cmd="x", shell_response=response)
        assert conf._pending_shell_attach is True

    def test_returncode_none_no_error_no_capture(self, conf):
        """returncode=None and no error means not failed."""
        response = {"stdout": "hi", "stderr": "", "returncode": None}
        capture_shell_result(conf, cmd="x", shell_response=response)
        assert not hasattr(conf, "_pending_shell_attach") or not conf._pending_shell_attach

    def test_empty_error_string_no_capture(self, conf):
        """Empty error string is falsy -- not treated as failure."""
        response = {"stdout": "ok", "stderr": "", "returncode": 0, "error": ""}
        capture_shell_result(conf, cmd="x", shell_response=response)
        assert not hasattr(conf, "_pending_shell_attach") or not conf._pending_shell_attach

    def test_none_stdout_stderr_treated_as_empty(self, conf):
        """None stdout/stderr are treated as empty strings."""
        response = {"stdout": None, "stderr": None, "returncode": 1, "error": "failed"}
        capture_shell_result(conf, cmd="fail", shell_response=response)
        assert conf._pending_shell_attach is True
        assert conf._last_shell_result["stdout"] == ""
        assert conf._last_shell_result["stderr"] == ""

    def test_truncation_flag_set_for_long_output(self, conf):
        """Output exceeding 200 lines sets truncated flag."""
        long_stdout = "\n".join(f"line{i}" for i in range(300))
        response = {"stdout": long_stdout, "stderr": "", "returncode": 1}
        capture_shell_result(conf, cmd="bigcmd", shell_response=response)
        assert conf._last_shell_result["truncated"] is True

    def test_truncation_flag_false_for_short_output(self, conf):
        """Short output does not set truncated flag."""
        response = {"stdout": "short", "stderr": "err", "returncode": 1}
        capture_shell_result(conf, cmd="cmd", shell_response=response)
        assert conf._last_shell_result["truncated"] is False

    def test_byte_limit_truncation_flag(self, conf):
        """Output within line limit but exceeding byte limit sets truncated."""
        # 150 lines of 100 chars each = 15000 bytes > 10240
        big_stdout = "\n".join("x" * 100 for _ in range(150))
        response = {"stdout": big_stdout, "stderr": "", "returncode": 1}
        capture_shell_result(conf, cmd="cmd", shell_response=response)
        assert conf._last_shell_result["truncated"] is True

    def test_verbose_mode_prints_notice(self, conf):
        """Verbose mode prints a capture notice."""
        conf.verbose = True
        response = {"stdout": "err", "stderr": "", "returncode": 1}

        with patch("aye.controller.shell_capture.rprint") as mock_rprint:
            capture_shell_result(conf, cmd="fail", shell_response=response)
            mock_rprint.assert_called_once()
            assert "Captured" in mock_rprint.call_args[0][0]

    def test_non_verbose_no_print(self, conf):
        """Non-verbose mode does not print."""
        conf.verbose = False
        response = {"stdout": "err", "stderr": "", "returncode": 1}

        with patch("aye.controller.shell_capture.rprint") as mock_rprint:
            capture_shell_result(conf, cmd="fail", shell_response=response)
            mock_rprint.assert_not_called()

    def test_timestamp_is_valid_iso(self, conf):
        """Timestamp stored is a valid ISO format string."""
        response = {"stdout": "x", "stderr": "", "returncode": 1}
        capture_shell_result(conf, cmd="cmd", shell_response=response)
        ts = conf._last_shell_result["timestamp"]
        # Should not raise
        datetime.fromisoformat(ts)

    def test_cwd_is_current_directory(self, conf, tmp_path):
        """Stored cwd matches the process working directory."""
        original = os.getcwd()
        try:
            os.chdir(tmp_path)
            response = {"stdout": "", "stderr": "err", "returncode": 2}
            capture_shell_result(conf, cmd="cmd", shell_response=response)
            assert conf._last_shell_result["cwd"] == str(tmp_path)
        finally:
            os.chdir(original)

    def test_successive_failures_overwrite(self, conf):
        """A second failure overwrites the first captured result."""
        response1 = {"stdout": "first", "stderr": "", "returncode": 1}
        response2 = {"stdout": "second", "stderr": "", "returncode": 2}
        capture_shell_result(conf, cmd="cmd1", shell_response=response1)
        capture_shell_result(conf, cmd="cmd2", shell_response=response2)
        assert conf._last_shell_result["cmd"] == "cmd2"
        assert conf._last_shell_result["stdout"] == "second"
        assert conf._last_shell_result["returncode"] == 2

    def test_missing_stdout_stderr_keys(self, conf):
        """Response without stdout/stderr keys treated as empty."""
        response = {"returncode": 1, "error": "missing output"}
        capture_shell_result(conf, cmd="cmd", shell_response=response)
        assert conf._last_shell_result["stdout"] == ""
        assert conf._last_shell_result["stderr"] == ""


class TestMaybeAttachShellResult:
    """Tests for maybe_attach_shell_result function."""

    def test_no_pending_returns_prompt_unchanged(self):
        """When nothing is pending, prompt is returned as-is."""
        conf = Mock()
        conf._pending_shell_attach = False
        result = maybe_attach_shell_result(conf, "fix the bug")
        assert result == "fix the bug"

    def test_no_attribute_returns_prompt_unchanged(self):
        """When _pending_shell_attach attribute doesn't exist, prompt is unchanged."""
        conf = Mock(spec=[])  # no attributes
        result = maybe_attach_shell_result(conf, "hello")
        assert result == "hello"

    def test_pending_but_no_result_returns_prompt_unchanged(self):
        """When pending is True but _last_shell_result is None, prompt unchanged."""
        conf = Mock(spec=[])
        conf._pending_shell_attach = True
        conf._last_shell_result = None
        result = maybe_attach_shell_result(conf, "hello")
        assert result == "hello"

    def test_attaches_shell_output(self):
        """Pending shell result is attached to the prompt."""
        conf = Mock()
        conf._pending_shell_attach = True
        conf._last_shell_result = {
            "cmd": "pytest",
            "cwd": "/home/user/project",
            "returncode": 1,
            "stdout": "FAILED test_foo.py",
            "stderr": "Error details",
            "failed": True,
        }

        result = maybe_attach_shell_result(conf, "fix the tests")

        assert result.startswith("fix the tests")
        assert "$ pytest" in result
        assert "cwd: /home/user/project" in result
        assert "exit_code: 1" in result
        assert "STDOUT:" in result
        assert "FAILED test_foo.py" in result
        assert "STDERR:" in result
        assert "Error details" in result
        assert result.endswith("---")

    def test_one_shot_clears_flag(self):
        """After attaching, pending flag is cleared."""
        conf = Mock()
        conf._pending_shell_attach = True
        conf._last_shell_result = {
            "cmd": "make",
            "cwd": "/tmp",
            "returncode": 2,
            "stdout": "error",
            "stderr": "",
            "failed": True,
        }

        maybe_attach_shell_result(conf, "fix it")
        assert conf._pending_shell_attach is False

    def test_second_call_does_not_reattach(self):
        """Second call returns prompt unchanged (one-shot semantics)."""
        conf = Mock()
        conf._pending_shell_attach = True
        conf._last_shell_result = {
            "cmd": "make",
            "cwd": "/tmp",
            "returncode": 2,
            "stdout": "error",
            "stderr": "",
            "failed": True,
        }

        first = maybe_attach_shell_result(conf, "fix it")
        second = maybe_attach_shell_result(conf, "fix it again")

        assert "$ make" in first
        assert second == "fix it again"

    def test_empty_stdout_not_included(self):
        """Empty stdout section is omitted."""
        conf = Mock()
        conf._pending_shell_attach = True
        conf._last_shell_result = {
            "cmd": "cmd",
            "cwd": "/tmp",
            "returncode": 1,
            "stdout": "",
            "stderr": "something failed",
            "failed": True,
        }

        result = maybe_attach_shell_result(conf, "prompt")
        assert "STDOUT:" not in result
        assert "STDERR:" in result

    def test_empty_stderr_not_included(self):
        """Empty stderr section is omitted."""
        conf = Mock()
        conf._pending_shell_attach = True
        conf._last_shell_result = {
            "cmd": "cmd",
            "cwd": "/tmp",
            "returncode": 1,
            "stdout": "some output",
            "stderr": "",
            "failed": True,
        }

        result = maybe_attach_shell_result(conf, "prompt")
        assert "STDOUT:" in result
        assert "STDERR:" not in result

    def test_whitespace_only_stdout_not_included(self):
        """Whitespace-only stdout is treated as empty."""
        conf = Mock()
        conf._pending_shell_attach = True
        conf._last_shell_result = {
            "cmd": "cmd",
            "cwd": "/tmp",
            "returncode": 1,
            "stdout": "   \n  ",
            "stderr": "err",
            "failed": True,
        }

        result = maybe_attach_shell_result(conf, "prompt")
        assert "STDOUT:" not in result

    def test_whitespace_only_stderr_not_included(self):
        """Whitespace-only stderr is treated as empty."""
        conf = Mock()
        conf._pending_shell_attach = True
        conf._last_shell_result = {
            "cmd": "cmd",
            "cwd": "/tmp",
            "returncode": 1,
            "stdout": "out",
            "stderr": "  \t  ",
            "failed": True,
        }

        result = maybe_attach_shell_result(conf, "prompt")
        assert "STDERR:" not in result

    def test_missing_keys_use_defaults(self):
        """Missing keys in result dict use sensible defaults."""
        conf = Mock()
        conf._pending_shell_attach = True
        conf._last_shell_result = {}

        result = maybe_attach_shell_result(conf, "prompt")
        assert "$ unknown" in result
        assert "cwd: unknown" in result
        assert "exit_code: unknown" in result

    def test_both_stdout_and_stderr_included(self):
        """Both stdout and stderr sections appear when non-empty."""
        conf = Mock()
        conf._pending_shell_attach = True
        conf._last_shell_result = {
            "cmd": "test",
            "cwd": "/home",
            "returncode": 1,
            "stdout": "test output",
            "stderr": "test error",
            "failed": True,
        }

        result = maybe_attach_shell_result(conf, "fix")
        assert "STDOUT:" in result
        assert "test output" in result
        assert "STDERR:" in result
        assert "test error" in result

    def test_structure_has_separator_lines(self):
        """Output block is wrapped with --- separators."""
        conf = Mock()
        conf._pending_shell_attach = True
        conf._last_shell_result = {
            "cmd": "ls",
            "cwd": "/tmp",
            "returncode": 1,
            "stdout": "output",
            "stderr": "",
            "failed": True,
        }

        result = maybe_attach_shell_result(conf, "prompt")
        lines = result.split("\n")
        # Should have --- as separator after prompt and at the end
        assert lines.count("---") == 2


class TestEndToEndCaptureAndAttach:
    """Integration tests: capture then attach."""

    @pytest.fixture
    def conf(self):
        conf = Mock()
        conf.verbose = False
        # Remove auto-created attributes so hasattr/getattr behave properly
        del conf._last_shell_result
        del conf._pending_shell_attach
        return conf

    @pytest.fixture(autouse=True)
    def enable_capture(self):
        """Ensure capture is enabled for all tests in this class."""
        with patch("aye.controller.shell_capture.is_capture_disabled", return_value=False):
            yield

    def test_capture_then_attach(self, conf):
        """Full round trip: capture a failure then attach to prompt."""
        response = {"stdout": "FAIL: test_x", "stderr": "AssertionError", "returncode": 1}
        capture_shell_result(conf, cmd="pytest -x", shell_response=response)

        result = maybe_attach_shell_result(conf, "fix this test")

        assert "fix this test" in result
        assert "$ pytest -x" in result
        assert "FAIL: test_x" in result
        assert "AssertionError" in result

    def test_attach_is_one_shot(self, conf):
        """After attaching once, second call does not re-attach."""
        response = {"stdout": "error", "stderr": "", "returncode": 1}
        capture_shell_result(conf, cmd="make", shell_response=response)

        first = maybe_attach_shell_result(conf, "fix")
        second = maybe_attach_shell_result(conf, "another prompt")

        assert "$ make" in first
        assert second == "another prompt"

    def test_success_does_not_arm_attach(self, conf):
        """Successful commands don't arm the attach mechanism."""
        response = {"stdout": "ok", "stderr": "", "returncode": 0}
        capture_shell_result(conf, cmd="echo ok", shell_response=response)

        result = maybe_attach_shell_result(conf, "do something")
        assert result == "do something"

    def test_new_failure_rearms_after_attach(self, conf):
        """A new failure after attach was consumed re-arms the mechanism."""
        resp1 = {"stdout": "fail1", "stderr": "", "returncode": 1}
        capture_shell_result(conf, cmd="cmd1", shell_response=resp1)
        maybe_attach_shell_result(conf, "fix1")  # consumes

        resp2 = {"stdout": "fail2", "stderr": "", "returncode": 1}
        capture_shell_result(conf, cmd="cmd2", shell_response=resp2)

        result = maybe_attach_shell_result(conf, "fix2")
        assert "$ cmd2" in result
        assert "fail2" in result
