"""Tests for the tool implementations and the tool-call wire protocol."""

import json
import platform
from pathlib import Path

import httpx
import pytest

from aye.model.tool_protocol import (
    MAX_CALLS_PER_ROUND,
    ToolCall,
    build_tools_prompt,
    describe_call,
    format_tool_results,
    is_tool_request,
    looks_like_protocol_json,
    looks_like_stub,
    parse_tool_calls,
    summary_with_tool_calls,
)
from aye.model.tools import (
    ALL_TOOLS,
    FILE_TOOLS,
    MAX_GLOB_RESULTS,
    MAX_GREP_MATCHES,
    MAX_SHELL_OUTPUT_BYTES,
    MAX_WRITE_BYTES,
    PERMISSION_DEFAULT,
    PERMISSION_FULL,
    SHELL_TOOLS,
    VALID_PERMISSIONS,
    ToolError,
    build_registry,
    execute_tool,
    needs_confirmation,
    permission_mode,
    read_only_registry,
    run_glob,
    run_grep,
    run_read,
    run_web_search,
    _format_shell_result,
    _matches_include,
    _resolve_in_root,
)


@pytest.fixture
def project(tmp_path):
    """A small project tree with an ignored directory and a binary file."""
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "main.py").write_text(
        "def hello():\n    return 'hi'\n\ndef goodbye():\n    return 'bye'\n",
        encoding="utf-8",
    )
    (tmp_path / "src" / "util.py").write_text(
        "def hello_again():\n    pass\n", encoding="utf-8"
    )
    (tmp_path / "README.md").write_text("# Demo\nhello world\n", encoding="utf-8")
    (tmp_path / ".gitignore").write_text("build/\nsecret.txt\n", encoding="utf-8")

    (tmp_path / "build").mkdir()
    (tmp_path / "build" / "out.py").write_text("hello\n", encoding="utf-8")
    (tmp_path / "secret.txt").write_text("hello secret\n", encoding="utf-8")
    (tmp_path / "blob.bin").write_bytes(b"\x00\x01\xff\xfe hello")

    return tmp_path


# ---------------------------------------------------------------------------
# Path sandboxing
# ---------------------------------------------------------------------------

class TestResolveInRoot:
    def test_relative_path_resolves_under_root(self, tmp_path):
        assert _resolve_in_root("a/b.py", tmp_path) == (tmp_path / "a" / "b.py").resolve()

    def test_absolute_path_inside_root_is_allowed(self, tmp_path):
        target = tmp_path / "x.py"
        assert _resolve_in_root(str(target), tmp_path) == target.resolve()

    def test_parent_traversal_is_rejected(self, tmp_path):
        with pytest.raises(ToolError, match="escapes the project root"):
            _resolve_in_root("../outside.txt", tmp_path)

    def test_deep_traversal_is_rejected(self, tmp_path):
        with pytest.raises(ToolError, match="escapes the project root"):
            _resolve_in_root("a/../../../etc/passwd", tmp_path)

    def test_absolute_path_outside_root_is_rejected(self, tmp_path):
        with pytest.raises(ToolError, match="escapes the project root"):
            _resolve_in_root(str(tmp_path.parent / "elsewhere.txt"), tmp_path)

    def test_empty_path_is_rejected(self, tmp_path):
        with pytest.raises(ToolError, match="path is required"):
            _resolve_in_root("   ", tmp_path)


# ---------------------------------------------------------------------------
# read
# ---------------------------------------------------------------------------

class TestRunRead:
    def test_returns_numbered_lines_with_header(self, project):
        out = run_read({"path": "src/main.py"}, project)
        assert "src/main.py (5 lines)" in out
        assert "1: def hello():" in out
        assert "5:     return 'bye'" in out

    def test_start_and_limit_select_a_range(self, project):
        out = run_read({"path": "src/main.py", "start": 4, "limit": 1}, project)
        assert "4: def goodbye():" in out
        assert "1: def hello():" not in out

    def test_string_numbers_are_coerced(self, project):
        """Models frequently send integers as strings."""
        out = run_read({"path": "src/main.py", "start": "4", "limit": "1"}, project)
        assert "4: def goodbye():" in out

    def test_missing_file_raises(self, project):
        with pytest.raises(ToolError, match="file not found"):
            run_read({"path": "nope.py"}, project)

    def test_directory_raises(self, project):
        with pytest.raises(ToolError, match="is a directory"):
            run_read({"path": "src"}, project)

    def test_ignored_file_raises(self, project):
        with pytest.raises(ToolError, match="excluded by ignore rules"):
            run_read({"path": "secret.txt"}, project)

    def test_binary_file_raises(self, project):
        with pytest.raises(ToolError, match="not valid UTF-8"):
            run_read({"path": "blob.bin"}, project)

    def test_out_of_range_start_reports_empty(self, project):
        out = run_read({"path": "src/main.py", "start": 999}, project)
        assert "no lines in the requested range" in out

    def test_long_output_is_truncated(self, project):
        big = project / "big.py"
        big.write_text("x = 1\n" * 20_000, encoding="utf-8")
        out = run_read({"path": "big.py"}, project)
        assert "truncated at" in out
        assert "call read again with start=" in out


# ---------------------------------------------------------------------------
# glob
# ---------------------------------------------------------------------------

class TestRunGlob:
    def test_finds_matching_files(self, project):
        out = run_glob({"pattern": "src/*.py"}, project)
        assert "src/main.py" in out
        assert "src/util.py" in out

    def test_recursive_pattern_acts_as_ls(self, project):
        out = run_glob({"pattern": "**/*"}, project)
        assert "src/main.py" in out
        assert "README.md" in out

    def test_excludes_ignored_directories(self, project):
        out = run_glob({"pattern": "**/*.py"}, project)
        assert "build/out.py" not in out

    def test_excludes_dot_prefixed_paths(self, project):
        out = run_glob({"pattern": "**/*"}, project)
        assert ".gitignore" not in out

    def test_no_matches_is_not_an_error(self, project):
        assert "No files match" in run_glob({"pattern": "*.rs"}, project)

    def test_missing_pattern_raises(self, project):
        with pytest.raises(ToolError, match="pattern is required"):
            run_glob({}, project)

    def test_results_are_capped(self, project):
        for i in range(MAX_GLOB_RESULTS + 20):
            (project / f"gen{i}.py").write_text("pass\n", encoding="utf-8")
        out = run_glob({"pattern": "*.py"}, project)
        assert f"capped at {MAX_GLOB_RESULTS}" in out
        assert len(out.splitlines()) == MAX_GLOB_RESULTS + 1


# ---------------------------------------------------------------------------
# grep
# ---------------------------------------------------------------------------

class TestRunGrep:
    def test_finds_matches_with_path_and_line(self, project):
        out = run_grep({"pattern": r"def hello"}, project)
        assert "src/main.py:1: def hello():" in out
        assert "src/util.py:1: def hello_again():" in out

    def test_include_filters_by_glob(self, project):
        out = run_grep({"pattern": "hello", "include": "*.md"}, project)
        assert "README.md" in out
        assert "src/main.py" not in out

    def test_ignore_case_option(self, project):
        (project / "case.py").write_text("HELLO = 1\n", encoding="utf-8")
        assert "case.py" not in run_grep({"pattern": "hello"}, project)
        assert "case.py" in run_grep({"pattern": "hello", "ignore_case": True}, project)

    def test_ignore_case_accepts_string_true(self, project):
        """Models send booleans as strings; both spellings must work."""
        (project / "case.py").write_text("HELLO = 1\n", encoding="utf-8")
        assert "case.py" in run_grep(
            {"pattern": "hello", "ignore_case": "true"}, project
        )

    def test_skips_ignored_and_binary_files(self, project):
        out = run_grep({"pattern": "hello"}, project)
        assert "secret.txt" not in out
        assert "build/out.py" not in out
        assert "blob.bin" not in out

    def test_no_matches_reports_files_searched(self, project):
        out = run_grep({"pattern": "zzz_nothing"}, project)
        assert "No matches" in out
        assert "files searched" in out

    def test_invalid_regex_raises(self, project):
        with pytest.raises(ToolError, match="invalid regular expression"):
            run_grep({"pattern": "([unclosed"}, project)

    def test_missing_pattern_raises(self, project):
        with pytest.raises(ToolError, match="pattern is required"):
            run_grep({}, project)

    def test_matches_are_capped(self, project):
        (project / "many.py").write_text("needle\n" * (MAX_GREP_MATCHES + 40), encoding="utf-8")
        out = run_grep({"pattern": "needle"}, project)
        assert f"capped at {MAX_GREP_MATCHES}" in out


class TestMatchesInclude:
    def test_bare_pattern_matches_basename_at_any_depth(self):
        assert _matches_include("src/deep/a.py", "*.py") is True

    def test_pattern_with_slash_matches_full_path(self):
        assert _matches_include("src/a.py", "src/*.py") is True
        assert _matches_include("other/a.py", "src/*.py") is False


# ---------------------------------------------------------------------------
# write
# ---------------------------------------------------------------------------

class TestRunWrite:
    """``write`` goes through apply_updates(), so the snapshot backend is
    reset per test to keep snapshots inside tmp_path.

    ``write`` is not in ``default_specs()`` (see WRITE_TOOL), so these tests
    dispatch through an explicit registry containing every tool. The
    implementation stays covered while the tool is withheld from the model.
    """

    @pytest.fixture(autouse=True)
    def _isolate_backend(self, project, monkeypatch):
        from aye.model import snapshot

        monkeypatch.chdir(project)
        snapshot.reset_backend()
        yield
        snapshot.reset_backend()

    @staticmethod
    def write(arguments, root):
        """Dispatch a write through the full registry."""
        return execute_tool(
            "write", arguments, root, registry=build_registry(ALL_TOOLS)
        )

    def test_creates_a_new_file(self, project):
        out = self.write({"path": "new.py", "content": "x = 1\n"}, project)
        assert "Created new.py" in out
        assert (project / "new.py").read_text(encoding="utf-8") == "x = 1\n"

    def test_updates_an_existing_file(self, project):
        out = self.write({"path": "src/main.py", "content": "replaced\n"}, project)
        assert "Updated src/main.py" in out
        assert (project / "src" / "main.py").read_text(encoding="utf-8") == "replaced\n"

    def test_snapshots_previous_state(self, project):
        """The snapshot is what makes `restore` able to undo a tool write."""
        from aye.model.snapshot import list_all_snapshots

        before = len(list_all_snapshots())
        self.write({"path": "src/main.py", "content": "new\n"}, project)
        assert len(list_all_snapshots()) == before + 1

    def test_mentions_restore_in_the_result(self, project):
        out = self.write({"path": "a.py", "content": "y = 2\n"}, project)
        assert "restore" in out

    def test_creates_parent_directories(self, project):
        self.write({"path": "deep/nested/f.py", "content": "pass\n"}, project)
        assert (project / "deep" / "nested" / "f.py").is_file()

    def test_empty_content_is_allowed(self, project):
        """An empty file is a legitimate write, so presence is checked, not truth."""
        out = self.write({"path": "empty.py", "content": ""}, project)
        assert "Error" not in out
        assert (project / "empty.py").read_text(encoding="utf-8") == ""

    def test_missing_content_is_an_error(self, project):
        out = self.write({"path": "a.py"}, project)
        assert "requires" in out and "content" in out

    def test_non_string_content_is_an_error(self, project):
        out = self.write({"path": "a.py", "content": 42}, project)
        assert "content must be a string" in out

    def test_oversized_content_is_refused(self, project):
        out = self.write(
            {"path": "a.py", "content": "x" * (MAX_WRITE_BYTES + 1)}, project
        )
        assert "over the" in out
        assert not (project / "a.py").exists()

    def test_traversal_is_refused(self, project):
        out = self.write({"path": "../escape.txt", "content": "bad\n"}, project)
        assert "escapes the project root" in out
        assert not (project.parent / "escape.txt").exists()

    def test_strict_mode_blocks_ignored_files(self, project, monkeypatch):
        monkeypatch.setenv("AYE_BLOCK_IGNORED_FILE_WRITES", "on")
        out = self.write({"path": "secret.txt", "content": "leak\n"}, project)
        assert "strict mode is on" in out
        assert "leak" not in (project / "secret.txt").read_text(encoding="utf-8")

    def test_ignored_files_allowed_when_not_strict(self, project, monkeypatch):
        monkeypatch.setenv("AYE_BLOCK_IGNORED_FILE_WRITES", "off")
        out = self.write({"path": "secret.txt", "content": "changed\n"}, project)
        assert "Error" not in out


# ---------------------------------------------------------------------------
# bash / cmd
# ---------------------------------------------------------------------------

@pytest.fixture
def shell_registry():
    """The full registry; shell tools are present in every permission mode."""
    return build_registry(ALL_TOOLS)


class TestPermissionMode:
    def test_default_mode_by_default(self, monkeypatch):
        monkeypatch.delenv("AYE_TOOL_PERMISSION", raising=False)
        monkeypatch.setattr(
            "aye.model.tools.get_user_config", lambda key, default=None: default
        )
        assert permission_mode() == PERMISSION_DEFAULT

    def test_full_mode_from_config(self, monkeypatch):
        monkeypatch.setenv("AYE_TOOL_PERMISSION", "full")
        assert permission_mode() == PERMISSION_FULL

    @pytest.mark.parametrize("value", ["FULL", "Full", "  full  "])
    def test_full_spellings_are_normalized(self, monkeypatch, value):
        monkeypatch.setenv("AYE_TOOL_PERMISSION", value)
        assert permission_mode() == PERMISSION_FULL

    def test_unrecognized_value_falls_back_to_default(self, monkeypatch):
        """A typo must never silently grant unattended shell access."""
        monkeypatch.setenv("AYE_TOOL_PERMISSION", "ful")
        assert permission_mode() == PERMISSION_DEFAULT

    def test_shell_tools_present_in_both_modes(self, monkeypatch):
        expected = {"cmd"} if platform.system() == "Windows" else {"bash"}
        for value in (PERMISSION_DEFAULT, PERMISSION_FULL):
            monkeypatch.setenv("AYE_TOOL_PERMISSION", value)
            assert expected <= set(build_registry())

    def test_only_the_current_platform_shell_is_offered(self):
        if platform.system() == "Windows":
            assert "cmd" in build_registry()
            assert "bash" not in build_registry()
        else:
            assert "bash" in build_registry()
            assert "cmd" not in build_registry()

    def test_excluded_from_read_only_registry(self):
        assert "bash" not in read_only_registry()
        assert "cmd" not in read_only_registry()
        assert "write" not in read_only_registry()


class TestShellApproval:
    @pytest.fixture(autouse=True)
    def _default_mode(self, monkeypatch):
        """Pin the permission mode to ``default``.

        ``permission_mode()`` falls back to the user's ``~/.ayecfg``, so a
        developer with ``tool_permission = full`` would otherwise see these
        tests fail for reasons unrelated to the code under test.
        """
        monkeypatch.delenv("AYE_TOOL_PERMISSION", raising=False)
        monkeypatch.setattr(
            "aye.model.tools.get_user_config",
            lambda key, default=None: PERMISSION_DEFAULT if key == "tool_permission" else default,
        )

    def test_shell_tools_prompt_in_default_mode(self, shell_registry):
        assert needs_confirmation("bash", shell_registry) is True
        assert needs_confirmation("cmd", shell_registry) is True

    def test_file_tools_never_prompt_in_default_mode(self, shell_registry):
        for name in ("read", "glob", "grep", "write"):
            assert needs_confirmation(name, shell_registry) is False

    def test_nothing_prompts_in_full_mode(self, shell_registry):
        for name in shell_registry:
            assert needs_confirmation(name, shell_registry, mode=PERMISSION_FULL) is False

    def test_unknown_tool_does_not_prompt(self, shell_registry):
        assert needs_confirmation("nope", shell_registry) is False

    def test_shell_tools_are_marked_mutating(self):
        assert all(spec.mutating for spec in SHELL_TOOLS)

    def test_only_shell_tools_prompt_by_default(self):
        prompting = {s.name for s in ALL_TOOLS if s.prompts_by_default}
        assert prompting == {"bash", "cmd"}


class TestShellExecution:
    """Uses `cmd`, which falls back to the default shell off Windows, so these
    run on any platform."""

    def test_captures_stdout_and_exit_code(self, project, shell_registry):
        out = execute_tool(
            "cmd", {"command": "echo marker_value"}, project, registry=shell_registry
        )
        assert "exit code: 0" in out
        assert "marker_value" in out

    def test_reports_nonzero_exit_code(self, project, shell_registry):
        out = execute_tool("cmd", {"command": "exit 3"}, project, registry=shell_registry)
        assert "exit code: 3" in out

    def test_echoes_the_command(self, project, shell_registry):
        out = execute_tool(
            "cmd", {"command": "echo abc"}, project, registry=shell_registry
        )
        assert "$ echo abc" in out

    def test_runs_in_the_project_root(self, project, shell_registry):
        """The child process must see the project, not the caller's cwd."""
        (project / "sentinel_file.txt").write_text("x", encoding="utf-8")
        command = "dir" if platform.system() == "Windows" else "ls"
        out = execute_tool(
            "cmd", {"command": command}, project, registry=shell_registry
        )
        assert "sentinel_file.txt" in out

    def test_empty_command_is_refused(self, project, shell_registry):
        out = execute_tool("cmd", {"command": "   "}, project, registry=shell_registry)
        assert "command is required" in out

    def test_missing_command_is_refused(self, project, shell_registry):
        out = execute_tool("cmd", {}, project, registry=shell_registry)
        assert "requires" in out

    def test_bash_empty_command_reports_argument_error(self, project, shell_registry):
        """The argument error must win over a missing-interpreter message."""
        out = execute_tool("bash", {"command": ""}, project, registry=shell_registry)
        assert "command is required" in out

    def test_timeout_is_reported(self, project, shell_registry, monkeypatch):
        import subprocess as sp

        def fake_run(*args, **kwargs):
            raise sp.TimeoutExpired(cmd="sleep", timeout=1)

        monkeypatch.setattr("aye.model.tools.subprocess.run", fake_run)
        out = execute_tool(
            "cmd", {"command": "sleep 999"}, project, registry=shell_registry
        )
        assert "timeout" in out and "killed" in out

    def test_missing_interpreter_is_reported(self, project, shell_registry, monkeypatch):
        monkeypatch.setattr("aye.model.tools.shutil.which", lambda name: None)
        out = execute_tool(
            "bash", {"command": "echo hi"}, project, registry=shell_registry
        )
        assert "bash is not available" in out


class TestFormatShellResult:
    def test_includes_both_streams(self):
        out = _format_shell_result("cmd", 1, "out text", "err text")
        assert "--- stdout ---" in out and "out text" in out
        assert "--- stderr ---" in out and "err text" in out

    def test_omits_empty_streams(self):
        out = _format_shell_result("cmd", 0, "only stdout", "")
        assert "--- stderr ---" not in out

    def test_labels_silent_commands(self):
        assert "(no output)" in _format_shell_result("cmd", 0, "", "")

    def test_truncates_long_output(self):
        out = _format_shell_result("cmd", 0, "x" * (MAX_SHELL_OUTPUT_BYTES + 500), "")
        assert "truncated at" in out
        assert len(out) < MAX_SHELL_OUTPUT_BYTES + 300


# ---------------------------------------------------------------------------
# Registry / dispatch
# ---------------------------------------------------------------------------

class TestRegistry:
    def test_default_registry_omits_the_write_tool(self, monkeypatch):
        """``write`` is withheld pending the sandboxed test flow (WRITE_TOOL)."""
        monkeypatch.delenv("AYE_TOOL_PERMISSION", raising=False)
        shell = "cmd" if platform.system() == "Windows" else "bash"
        assert set(build_registry()) == {"read", "glob", "grep", shell, "web_search"}

    def test_write_still_exists_and_is_dispatchable(self):
        """The implementation is kept intact, just not offered to the model."""
        assert "write" in {spec.name for spec in ALL_TOOLS}
        assert "write" in build_registry(ALL_TOOLS)

    def test_read_only_registry_drops_mutating_tools(self):
        assert set(read_only_registry()) == {"read", "glob", "grep", "web_search"}

    def test_write_and_shell_are_the_mutating_tools(self):
        mutating = {s.name for s in ALL_TOOLS if s.mutating}
        assert mutating == {"write", "bash", "cmd"}

    def test_every_required_param_is_declared(self):
        for spec in ALL_TOOLS:
            for name in spec.required:
                assert name in spec.parameters, f"{spec.name}.{name} undocumented"

    def test_tool_names_are_unique(self):
        names = [s.name for s in ALL_TOOLS]
        assert len(names) == len(set(names))


class TestExecuteTool:
    def test_unknown_tool_lists_alternatives(self, project):
        out = execute_tool("bogus", {}, project)
        assert "unknown tool" in out
        assert "grep" in out

    def test_missing_required_argument_is_reported(self, project):
        assert "requires" in execute_tool("read", {}, project)

    def test_non_dict_arguments_are_tolerated(self, project):
        assert "requires" in execute_tool("read", "not a dict", project)

    def test_tool_error_is_returned_not_raised(self, project):
        out = execute_tool("read", {"path": "missing.py"}, project)
        assert out.startswith("Error:")

    def test_unexpected_exception_is_contained(self, project):
        """A tool bug must not abort the chat turn."""
        from aye.model.tools import ToolSpec

        def boom(arguments, root):
            raise RuntimeError("kaboom")

        registry = {
            "boom": ToolSpec("boom", "d", {}, (), boom),
        }
        out = execute_tool("boom", {}, project, registry=registry)
        assert "failed unexpectedly" in out
        assert "kaboom" in out

    def test_write_absent_from_read_only_registry(self, project):
        out = execute_tool(
            "write", {"path": "a.py", "content": "x"}, project,
            registry=read_only_registry(),
        )
        assert "unknown tool" in out


# ---------------------------------------------------------------------------
# Protocol: prompt block
# ---------------------------------------------------------------------------

class TestBuildToolsPrompt:
    def test_empty_specs_produce_no_block(self):
        assert build_tools_prompt([]) == ""

    def test_lists_every_tool_and_parameter(self):
        block = build_tools_prompt(FILE_TOOLS)
        for spec in FILE_TOOLS:
            assert spec.name in block
            for param in spec.parameters:
                assert param in block

    def test_marks_required_and_optional(self):
        block = build_tools_prompt(FILE_TOOLS)
        assert "path (required)" in block
        assert "start (optional)" in block

    def test_documents_the_request_shape(self):
        assert '"tool_calls"' in build_tools_prompt(FILE_TOOLS)

    def test_warns_web_search_defaults_to_duckduckgo_and_can_fail(self):
        block = build_tools_prompt(FILE_TOOLS + [spec for spec in ALL_TOOLS if spec.name == "web_search"])
        assert "`web_search`" in block
        assert "DuckDuckGo" in block
        assert "Never invent results or URLs" in block

    def test_final_round_forbids_more_calls(self):
        block = build_tools_prompt(FILE_TOOLS, is_final_round=True)
        assert "reached the tool call limit" in block

    def test_normal_round_has_no_limit_notice(self):
        assert "reached the tool call limit" not in build_tools_prompt(FILE_TOOLS)


# ---------------------------------------------------------------------------
# Protocol: parsing
# ---------------------------------------------------------------------------

class TestParseToolCalls:
    def test_prose_is_not_a_tool_call(self):
        assert parse_tool_calls("The capital of France is Paris.") == []

    def test_plural_array_form(self):
        calls = parse_tool_calls(
            '{"tool_calls":[{"name":"grep","arguments":{"pattern":"def foo"}}]}'
        )
        assert calls == [ToolCall("grep", {"pattern": "def foo"})]

    def test_singular_key_is_tolerated(self):
        """Models reach for the singular form despite the documented schema."""
        calls = parse_tool_calls(
            '{"tool_call":{"name":"read","arguments":{"path":"a.py"}}}'
        )
        assert calls == [ToolCall("read", {"path": "a.py"})]

    def test_bare_dict_instead_of_array(self):
        calls = parse_tool_calls(
            '{"tool_calls":{"name":"read","arguments":{"path":"b.py"}}}'
        )
        assert len(calls) == 1

    def test_code_fence_is_stripped(self):
        calls = parse_tool_calls(
            '```json\n{"tool_calls":[{"name":"glob","arguments":{"pattern":"*.py"}}]}\n```'
        )
        assert calls == [ToolCall("glob", {"pattern": "*.py"})]

    def test_missing_arguments_defaults_to_empty(self):
        assert parse_tool_calls('{"tool_calls":[{"name":"glob"}]}') == [
            ToolCall("glob", {})
        ]

    def test_entry_without_name_is_skipped(self):
        assert parse_tool_calls('{"tool_calls":[{"arguments":{"a":1}}]}') == []

    def test_identical_calls_are_deduplicated(self):
        calls = parse_tool_calls(
            '{"tool_calls":[{"name":"read","arguments":{"path":"a"}},'
            '{"name":"read","arguments":{"path":"a"}}]}'
        )
        assert len(calls) == 1

    def test_differing_arguments_are_kept(self):
        calls = parse_tool_calls(
            '{"tool_calls":[{"name":"read","arguments":{"path":"a"}},'
            '{"name":"read","arguments":{"path":"b"}}]}'
        )
        assert len(calls) == 2

    def test_calls_are_capped(self):
        entries = ",".join(
            '{"name":"read","arguments":{"path":"f%d"}}' % i for i in range(12)
        )
        assert len(parse_tool_calls("{\"tool_calls\":[%s]}" % entries)) == MAX_CALLS_PER_ROUND

    def test_malformed_json_is_not_a_tool_call(self):
        assert parse_tool_calls('{"tool_calls": [oops') == []

    def test_json_without_tool_calls_is_not_a_tool_call(self):
        assert parse_tool_calls('{"answer_summary":"hello"}') == []

    def test_json_array_at_top_level_is_ignored(self):
        assert parse_tool_calls('[{"name":"read"}]') == []

    def test_empty_and_none_are_safe(self):
        assert parse_tool_calls("") == []
        assert parse_tool_calls(None) == []
        assert parse_tool_calls("   ") == []

    def test_is_tool_request_mirrors_parse(self):
        assert is_tool_request('{"tool_calls":[{"name":"glob"}]}') is True
        assert is_tool_request("just prose") is False

    def test_json_embedded_in_prose_is_extracted(self):
        calls = parse_tool_calls(
            'I will use the tools now. {"tool_calls":[{"name":"grep",'
            '"arguments":{"pattern":"def foo"}}]} That is all.'
        )
        assert calls == [ToolCall("grep", {"pattern": "def foo"})]


class TestLooksLikeStub:
    def test_short_investigate_placeholder_is_a_stub(self):
        assert looks_like_stub("Let me investigate the texture issue") is True
        assert looks_like_stub("I'll look into this and get back to you.") is True

    def test_plain_answers_are_not_stubs(self):
        assert looks_like_stub("The texture issue is caused by a missing layer.") is False
        assert looks_like_stub("") is False
        assert looks_like_stub(None) is False

    def test_long_explanations_are_not_stubs(self):
        long = "Let me investigate " + "details " * 60
        assert looks_like_stub(long) is False

    def test_stub_with_tool_calls_is_not_a_stub(self):
        assert (
            looks_like_stub(
                'Let me investigate. {"tool_calls":[{"name":"grep",'
                '"arguments":{"pattern":"def"}}]}'
            )
            is False
        )

    @pytest.mark.parametrize(
        "reply",
        [
            # The reply that shipped this fix: the model has glob/read but asks
            # the user to run them, so nothing is investigated.
            "I don't have the repository tool output yet. Please run the "
            "glob/read tool so I can see the current components/ structure, "
            "then I can create the components/AgentProgress.tsx file "
            "appropriately.",
            "Please provide the output of the read tool.",
            "Could you please share the contents of components/?",
            "I need you to run glob so I can see the structure.",
            "You need to provide the file contents first.",
            "You'll have to share the repo structure.",
            "I do not have access to the repository contents yet.",
            "I cannot see the files. Can you paste the directory structure?",
            "I haven't received the grep results yet.",
            "Waiting for the tool results before I can proceed.",
        ],
    )
    def test_deflections_are_stubs(self, reply):
        """Asking the user to supply tool output is a failure to call tools."""
        assert looks_like_stub(reply) is True

    @pytest.mark.parametrize(
        "reply",
        [
            # Mentions a tool, but reports work already done.
            "I read hooks/useResearchStream.ts and it exposes a `stages` "
            "array; the component maps over it.",
            "I checked the tests and they pass.",
            # "You need to run" pointing at the user's own verification step,
            # not at fetching context for the model.
            "You need to run pytest to confirm, but the fix is in place at "
            "src/app.py:42.",
            # Documentation-style prose that happens to name a tool.
            "Please note that the write tool snapshots the previous state "
            "automatically.",
            "AgentProgress.tsx now renders a generic progress list driven by "
            "the `stages` prop.",
        ],
    )
    def test_real_answers_mentioning_tools_are_not_stubs(self, reply):
        assert looks_like_stub(reply) is False

    def test_deflection_with_tool_calls_is_not_a_stub(self):
        assert (
            looks_like_stub(
                'I do not have the file contents. '
                '{"tool_calls":[{"name":"read","arguments":{"path":"a.py"}}]}'
            )
            is False
        )

    def test_very_long_deflection_is_not_a_stub(self):
        """A real answer can discuss missing context at length; only terse
        deflections are nudged."""
        long = "I do not have the repository contents. " + "detail " * 100
        assert looks_like_stub(long) is False


class TestLooksLikeProtocolJson:
    def test_valid_protocol_object_is_detected(self):
        assert looks_like_protocol_json('{"tool_calls":[{"name":"grep"}]}') is True

    def test_malformed_protocol_object_is_still_detected(self):
        assert looks_like_protocol_json('{"tool_calls": "oops"}') is True

    def test_plain_prose_is_not_protocol(self):
        assert looks_like_protocol_json("Everything works now.") is False
        assert looks_like_protocol_json("") is False
        assert looks_like_protocol_json(None) is False

    def test_non_brace_text_mentioning_tool_calls_is_not_protocol(self):
        assert looks_like_protocol_json("I saw tool_calls in the output.") is False

    @pytest.mark.parametrize(
        "summary",
        [
            'Let me investigate.\n{"tool_calls":[{"name":"grep"}]}',
            'I will read it.\n{"tool_call": {"name":"read"}}',
            'First, the structure.\n\n{"tool_calls": [',
        ],
    )
    def test_protocol_object_after_narration_is_detected(self, summary):
        """Models narrate before emitting the request; a leading-brace-only
        check let that JSON reach the user as an answer."""
        assert looks_like_protocol_json(summary) is True

    def test_json_literal_in_prose_is_not_protocol(self):
        assert looks_like_protocol_json('Use {"a": 1} as the config.') is False


class TestSummaryWithToolCalls:
    def test_structured_field_wins_when_valid(self):
        calls = [{"name": "grep", "arguments": {"pattern": "x"}}]
        out = summary_with_tool_calls("checking now", calls)
        assert json.loads(out)["tool_calls"] == calls

    def test_malformed_field_falls_back_to_summary(self):
        assert summary_with_tool_calls("done", "oops") == "done"

    def test_singular_dict_field_is_wrapped(self):
        out = summary_with_tool_calls(
            "checking", {"name": "grep", "arguments": {"pattern": "x"}}
        )
        assert json.loads(out)["tool_calls"] == {
            "name": "grep",
            "arguments": {"pattern": "x"},
        }
        assert parse_tool_calls(out) == [ToolCall("grep", {"pattern": "x"})]

    def test_empty_field_keeps_summary(self):
        assert summary_with_tool_calls("hello world", None) == "hello world"
        assert summary_with_tool_calls("hello world", []) == "hello world"


# ---------------------------------------------------------------------------
# Protocol: result formatting
# ---------------------------------------------------------------------------

class TestDescribeCall:
    def test_renders_name_and_arguments(self):
        assert describe_call(ToolCall("read", {"path": "a.py"})) == "read(path='a.py')"

    def test_no_arguments(self):
        assert describe_call(ToolCall("glob", {})) == "glob()"

    def test_long_values_are_clipped(self):
        out = describe_call(ToolCall("grep", {"pattern": "x" * 200}))
        assert "\u2026" in out
        assert len(out) < 120


class TestFormatToolResults:
    def test_includes_output_and_restates_the_question(self):
        out = format_tool_results(
            "who calls foo?",
            [(ToolCall("grep", {"pattern": "foo"}), "src/a.py:1: foo()")],
        )
        assert "src/a.py:1: foo()" in out
        assert "who calls foo?" in out
        assert "grep(pattern='foo')" in out

    def test_multiple_results_are_all_present(self):
        out = format_tool_results(
            "q",
            [
                (ToolCall("glob", {"pattern": "*.py"}), "a.py"),
                (ToolCall("read", {"path": "a.py"}), "1: pass"),
            ],
        )
        assert "a.py" in out
        assert "1: pass" in out

    def test_empty_output_is_labelled(self):
        out = format_tool_results("q", [(ToolCall("glob", {}), "")])
        assert "(no output)" in out


# ---------------------------------------------------------------------------
# web_search
# ---------------------------------------------------------------------------

class TestRunWebSearch:
    def test_requires_query(self, tmp_path):
        with pytest.raises(ToolError, match="query is required"):
            run_web_search({}, tmp_path)

    def test_unknown_provider(self, tmp_path, monkeypatch):
        monkeypatch.setattr(
            "aye.model.tools.get_user_config",
            lambda key, default=None: "shodan" if key == "search_provider" else default,
        )
        with pytest.raises(ToolError, match="unknown search_provider"):
            run_web_search({"query": "hello"}, tmp_path)

    def test_duckduckgo_parses_html_results(self, tmp_path, monkeypatch):
        monkeypatch.setattr(
            "aye.model.tools.get_user_config",
            lambda key, default=None: (
                "duckduckgo" if key == "search_provider" else default
            ),
        )
        html = (
            '<div class="result"><a rel="nofollow" class="result__a" '
            'href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.com%2F&rut=1">'
            "Example <b>Site</b></a>"
            '<a class="result__snippet" href="x">A useful <b>blurb</b>.</a></div>'
        )

        class FakeResponse:
            status_code = 200
            text = html
            content = html.encode("utf-8")

        monkeypatch.setattr(
            "aye.model.tools.httpx.get",
            lambda *a, **k: FakeResponse(),
        )
        out = run_web_search({"query": "example", "max_results": 1}, tmp_path)
        assert "DuckDuckGo results for 'example'" in out
        assert "Example Site" in out
        assert "https://example.com/" in out
        assert "A useful blurb." in out

    def test_duckduckgo_error_is_reported(self, tmp_path, monkeypatch):
        monkeypatch.setattr(
            "aye.model.tools.get_user_config",
            lambda key, default=None: (
                "duckduckgo" if key == "search_provider" else default
            ),
        )

        def fail(*args, **kwargs):
            raise httpx.ConnectError("no network")

        monkeypatch.setattr("aye.model.tools.httpx.get", fail)
        with pytest.raises(ToolError, match="DuckDuckGo request failed"):
            run_web_search({"query": "example"}, tmp_path)

    def test_tavily_requires_key(self, tmp_path, monkeypatch):
        monkeypatch.setattr(
            "aye.model.tools.get_user_config",
            lambda key, default=None: (
                "tavily" if key == "search_provider" else default
            ),
        )
        with pytest.raises(ToolError, match="tavily_api_key is not set"):
            run_web_search({"query": "example"}, tmp_path)

    def test_brave_requires_key(self, tmp_path, monkeypatch):
        monkeypatch.setattr(
            "aye.model.tools.get_user_config",
            lambda key, default=None: (
                "brave" if key == "search_provider" else default
            ),
        )
        with pytest.raises(ToolError, match="brave_api_key is not set"):
            run_web_search({"query": "example"}, tmp_path)

    def test_registry_includes_web_search(self):
        registry = build_registry()
        assert "web_search" in registry
        assert registry["web_search"].mutating is False
        assert registry["web_search"].prompts_by_default is False
