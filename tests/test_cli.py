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


@patch('aye.controller.commands.get_all_config')
@patch('aye.presenter.cli_ui.print_config_list')
def test_config_list(mock_print, mock_get_all):
    result = runner.invoke(app, ["config", "list"])
    assert result.exit_code == 0
    mock_get_all.assert_called_once()
    mock_print.assert_called_once()


@patch('aye.controller.commands.set_config_value')
@patch('aye.presenter.cli_ui.print_generic_message')
def test_config_set(mock_print, mock_set):
    result = runner.invoke(app, ["config", "set", "mykey", "myvalue"])
    assert result.exit_code == 0
    mock_set.assert_called_once_with("mykey", "myvalue")
    mock_print.assert_called_once_with("Configuration 'mykey' set.")


@patch('aye.controller.commands.delete_config_value', return_value=False)
@patch('aye.presenter.cli_ui.print_generic_message')
def test_config_delete_not_found(mock_print, mock_delete):
    result = runner.invoke(app, ["config", "delete", "mykey"])
    assert result.exit_code == 0
    mock_delete.assert_called_once_with("mykey")
    mock_print.assert_called_once_with("Configuration key 'mykey' not found.", is_error=True)


def test_config_invalid_action():
    result = runner.invoke(app, ["config", "invalid_action"])
    assert result.exit_code != 0
    assert "Invalid action 'invalid_action'" in result.stdout


def test_config_missing_args():
    result_get = runner.invoke(app, ["config", "get"])
    assert result_get.exit_code != 0
    assert "Key is required" in result_get.stdout

    result_set = runner.invoke(app, ["config", "set", "key_only"])
    assert result_set.exit_code != 0
    assert "Key and value are required" in result_set.stdout

    result_delete = runner.invoke(app, ["config", "delete"])
    assert result_delete.exit_code != 0
    assert "Key is required" in result_delete.stdout
