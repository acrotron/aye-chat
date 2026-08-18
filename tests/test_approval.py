"""Tests for interactive shell-command confirmation and permission toggling."""

import os

from aye.controller.approval import (
    PERMISSION_DEFAULT,
    PERMISSION_FULL,
    build_confirm_panel,
    confirm_command,
    toggle_permission_mode,
    _build_confirm_bindings,
)


class TestConfirmCommand:
    def test_enter_approves(self, capsys):
        assert confirm_command("pytest -q", read_key=lambda: True) is True
        out = capsys.readouterr().out
        assert "pytest -q" in out

    def test_escape_declines(self):
        assert confirm_command("rm -rf .", read_key=lambda: False) is False

    def test_ctrl_c_declines(self):
        def interrupted():
            raise KeyboardInterrupt

        assert confirm_command("pytest", read_key=interrupted) is False

    def test_panel_shows_command_and_key_hints(self):
        panel = build_confirm_panel("pytest -q")
        rendered = panel.renderable  # rich markup object, not plain text
        text = str(rendered)
        assert "pytest -q" in text

    def test_approval_bindings_cover_enter_escape_ctrl_c(self):
        bound = {key for b in _build_confirm_bindings().bindings for key in b.keys}
        assert "c-m" in bound  # Enter
        assert "escape" in bound
        assert "c-c" in bound


class TestTogglePermissionMode:
    def test_toggles_default_to_full(self, monkeypatch):
        monkeypatch.setattr(
            "aye.controller.approval.permission_mode", lambda: PERMISSION_DEFAULT
        )
        written = []
        monkeypatch.setattr(
            "aye.controller.approval.set_user_config",
            lambda k, v: written.append((k, v)),
        )
        assert toggle_permission_mode() == PERMISSION_FULL
        assert ("tool_permission", PERMISSION_FULL) in written

    def test_toggles_full_back_to_default(self, monkeypatch):
        monkeypatch.setattr(
            "aye.controller.approval.permission_mode", lambda: PERMISSION_FULL
        )
        written = []
        monkeypatch.setattr(
            "aye.controller.approval.set_user_config",
            lambda k, v: written.append((k, v)),
        )
        assert toggle_permission_mode() == PERMISSION_DEFAULT
        assert ("tool_permission", PERMISSION_DEFAULT) in written

    def test_updates_session_env_so_it_applies_immediately(self, monkeypatch):
        monkeypatch.setattr("os.environ", dict(os.environ))
        monkeypatch.setattr(
            "aye.controller.approval.permission_mode", lambda: PERMISSION_DEFAULT
        )
        monkeypatch.setattr("aye.controller.approval.set_user_config", lambda k, v: None)
        toggle_permission_mode()
        assert os.environ.get("AYE_TOOL_PERMISSION") == PERMISSION_FULL