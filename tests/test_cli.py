from pathlib import Path
from unittest.mock import patch, MagicMock

import typer
from typer.testing import CliRunner

import aye.controller.commands as commands
from aye.__main__ import app

runner = CliRunner()


def test_version_callback():
    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0
    assert "." in result.stdout


@patch("aye.__main__._get_package_version")
def test_version_callback_ayechat(mock_get_version):
    """Test --version reports correct version for ayechat package."""
    mock_get_version.return_value = "1.2.3"
    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0
    assert "1.2.3" in result.stdout


@patch("aye.__main__._get_package_version")
def test_version_callback_ayechat_dev(mock_get_version):
    """Test --version reports correct version for ayechat-dev package."""
    mock_get_version.return_value = "0.36.5.20260108214830"
    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0
    assert "0.36.5.20260108214830" in result.stdout


def test_main_no_command():
    result = runner.invoke(app)
    assert result.exit_code == 0
    assert "Run 'aye --help'" in result.stdout


@patch('aye.controller.commands.login_and_fetch_plugins', side_effect=Exception("API Error"))
@patch('aye.presenter.cli_ui.print_generic_message')
def test_login_failure(mock_print, mock_login):
    result = runner.invoke(app, ["auth", "login"])
    assert result.exit_code == 0
    mock_login.assert_called_once()
    mock_print.assert_called_once_with("Login failed: API Error", is_error=True)


@patch('aye.controller.commands.get_auth_status_token', side_effect=Exception("Some Error"))
@patch('aye.presenter.cli_ui.print_generic_message')
def test_auth_status_error(mock_print, mock_status):
    result = runner.invoke(app, ["auth", "status"])
    assert result.exit_code == 0
    mock_status.assert_called_once()
    mock_print.assert_called_once_with("Error checking auth status: Some Error", is_error=True)


@patch('aye.controller.repl.chat_repl')
def test_chat_command(mock_chat_repl):
    result = runner.invoke(app, ["chat", "--root", "/tmp", "--include", "*.js"])
    assert result.exit_code == 0
    mock_chat_repl.assert_called_once()
    conf = mock_chat_repl.call_args[0][0]
    #assert conf.root == Path("/tmp")
    assert conf.file_mask == "*.js"


@patch('aye.controller.commands.get_snapshot_content', return_value="file content")
@patch('aye.presenter.cli_ui.print_snapshot_content')
def test_snap_show(mock_print, mock_get):
    result = runner.invoke(app, ["snap", "show", "file.py", "001"])
    assert result.exit_code == 0
    mock_get.assert_called_once_with(Path("file.py"), "001")
    mock_print.assert_called_once_with("file content")


@patch('aye.controller.commands.restore_from_snapshot', side_effect=ValueError("Not found"))
@patch('aye.presenter.cli_ui.print_generic_message')
def test_snap_restore_error(mock_print, mock_restore):
    result = runner.invoke(app, ["snap", "restore", "999"])
    assert result.exit_code == 0
    mock_restore.assert_called_once_with("999", None)
    mock_print.assert_called_once_with("Error: Not found", is_error=True)


@patch('aye.controller.commands.prune_snapshots', side_effect=Exception("Prune failed"))
@patch('aye.presenter.cli_ui.print_generic_message')
def test_snap_keep_error(mock_print, mock_prune):
    result = runner.invoke(app, ["snap", "keep", "--num", "5"])
    assert result.exit_code == 0
    mock_prune.assert_called_once_with(5)
    mock_print.assert_called_once_with("Error pruning snapshots: Prune failed", is_error=True)


@patch('aye.controller.commands.cleanup_old_snapshots', side_effect=Exception("Cleanup failed"))
@patch('aye.presenter.cli_ui.print_generic_message')
def test_snap_cleanup_error(mock_print, mock_cleanup):
    result = runner.invoke(app, ["snap", "cleanup", "--days", "15"])
    assert result.exit_code == 0
    mock_cleanup.assert_called_once_with(15)
    mock_print.assert_called_once_with("Error cleaning up snapshots: Cleanup failed", is_error=True)


