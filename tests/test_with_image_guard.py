"""Tests for Phase 6: the `with` command rejects image files.

Covers:
- ``with screenshot.png: ...`` direct refs are rejected with a clear message.
- ``with *.png: ...`` image-targeted globs are rejected.
- ``with main.py: ...`` plain source refs still work (no regression).
- Mixed source + image lists are rejected (don't silently drop images).
- Generic globs (``*.*``) that pull in images are caught post-expansion.
"""

import shutil
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from aye.controller import command_handlers
from aye.controller.command_handlers import handle_with_command


@pytest.fixture
def temp_project():
    """Create a tiny project root with one Python file and one image."""
    tmp = tempfile.mkdtemp()
    root = Path(tmp)
    (root / "main.py").write_text("print('hi')")
    (root / "screenshot.png").write_bytes(b"\x89PNG\r\n\x1a\nfakeimage")
    (root / "util.py").write_text("def f(): pass")
    yield root
    shutil.rmtree(tmp)


@pytest.fixture
def mock_conf(temp_project):
    conf = MagicMock()
    conf.root = temp_project
    conf.verbose = False
    conf.plugin_manager = MagicMock()
    return conf


def _run(mock_conf, prompt):
    """Helper: run handle_with_command with invoke_llm patched out."""
    with patch.object(command_handlers, "invoke_llm") as mock_invoke, \
         patch.object(command_handlers, "process_llm_response", return_value=42), \
         patch.object(command_handlers, "maybe_attach_shell_result", side_effect=lambda c, p: p):
        mock_invoke.return_value = MagicMock(chat_id=99)
        result = handle_with_command(
            prompt=prompt,
            conf=mock_conf,
            console=MagicMock(),
            chat_id=-1,
            chat_id_file=Path(".aye/chat_id.tmp"),
        )
        return result, mock_invoke


class TestWithImageGuard:
    def test_direct_image_ref_rejected(self, mock_conf, capsys):
        result, mock_invoke = _run(mock_conf, "with screenshot.png: describe this")

        assert result is None
        assert not mock_invoke.called
        out = capsys.readouterr().out
        assert "Image files are not supported" in out
        assert "@filename" in out
        assert "screenshot.png" in out

    def test_image_glob_rejected(self, mock_conf, capsys):
        result, mock_invoke = _run(mock_conf, "with *.png: explain")

        assert result is None
        assert not mock_invoke.called
        out = capsys.readouterr().out
        assert "Image files are not supported" in out
        assert "*.png" in out

    def test_mixed_source_and_image_rejected(self, mock_conf, capsys):
        result, mock_invoke = _run(
            mock_conf, "with main.py, screenshot.png: explain both"
        )

        # Should be rejected outright (don't silently drop images).
        assert result is None
        assert not mock_invoke.called
        out = capsys.readouterr().out
        assert "Image files are not supported" in out
        assert "screenshot.png" in out

    def test_uppercase_extension_rejected(self, mock_conf, capsys):
        # Add an uppercase-named image
        (mock_conf.root / "SHOT.PNG").write_bytes(b"\x89PNGfake")

        result, mock_invoke = _run(mock_conf, "with SHOT.PNG: describe")

        assert result is None
        assert not mock_invoke.called
        out = capsys.readouterr().out
        assert "Image files are not supported" in out

    def test_source_only_still_works(self, mock_conf):
        result, mock_invoke = _run(mock_conf, "with main.py: explain this")

        assert mock_invoke.called
        # Source file passed through explicit_source_files
        kwargs = mock_invoke.call_args.kwargs
        assert "main.py" in kwargs["explicit_source_files"]
        # Returned chat id from process_llm_response
        assert result == 42

    def test_multiple_source_files_still_work(self, mock_conf):
        result, mock_invoke = _run(mock_conf, "with main.py, util.py: refactor")

        assert mock_invoke.called
        kwargs = mock_invoke.call_args.kwargs
        assert set(kwargs["explicit_source_files"].keys()) == {"main.py", "util.py"}

    def test_source_glob_still_works(self, mock_conf):
        result, mock_invoke = _run(mock_conf, "with *.py: list all")

        assert mock_invoke.called
        kwargs = mock_invoke.call_args.kwargs
        # Both .py files should be included
        included = set(kwargs["explicit_source_files"].keys())
        assert "main.py" in included
        assert "util.py" in included
        # No image file leaked in
        assert "screenshot.png" not in included

    def test_generic_glob_pulling_image_is_rejected(self, mock_conf, capsys):
        """`*.*` would pull in screenshot.png too \u2014 post-expansion sweep catches it."""
        result, mock_invoke = _run(mock_conf, "with *.*: review everything")

        assert result is None
        assert not mock_invoke.called
        out = capsys.readouterr().out
        assert "Image files are not supported" in out
        assert "screenshot.png" in out

    def test_empty_file_list_unchanged(self, mock_conf, capsys):
        result, mock_invoke = _run(mock_conf, "with : hello")
        assert result is None
        assert not mock_invoke.called
        out = capsys.readouterr().out
        assert "File list cannot be empty" in out

    def test_empty_prompt_unchanged(self, mock_conf, capsys):
        result, mock_invoke = _run(mock_conf, "with main.py: ")
        assert result is None
        assert not mock_invoke.called
        out = capsys.readouterr().out
        assert "Prompt cannot be empty" in out
