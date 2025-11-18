import pytest
import tempfile
from pathlib import Path
from unittest.mock import patch
import typer
import os

import aye.model.auth as auth
import aye.controller.commands as commands
import aye.__main__ as main_cli


@pytest.fixture
def temp_config_file():
    """Create a temporary config file for testing, isolated from user's real ~/.ayecfg."""
    tmp_dir = tempfile.TemporaryDirectory()
    config_path = Path(tmp_dir.name) / '.ayecfg'
    with patch('aye.model.auth.TOKEN_FILE', config_path):
        yield config_path
    tmp_dir.cleanup()


def test_uat_1_1_successful_login_with_valid_token(temp_config_file):
    """UAT-1.1: Successful Login with Valid Token
    
    Given: No existing token.
    When: User runs `aye auth login` and enters a valid token.
    Then: Stores token, shows success, attempts plugin download.
    """
    with patch('aye.model.auth.typer.prompt', return_value='valid_personal_access_token') as mock_prompt, \
         patch('aye.model.auth.typer.secho') as mock_secho, \
         patch('aye.controller.commands.download_plugins.fetch_plugins') as mock_fetch_plugins:
        
        assert not temp_config_file.exists()
        
        main_cli.login()
        
        mock_prompt.assert_called_once_with('Paste your token', hide_input=True)
        mock_secho.assert_called_once_with('✅ Token saved.', fg=typer.colors.GREEN)
        
        config_content = temp_config_file.read_text(encoding='utf-8')
        assert '[default]' in config_content
        assert 'token=valid_personal_access_token' in config_content
        
        mock_fetch_plugins.assert_called_once()


def test_uat_1_2_login_with_invalid_token(temp_config_file):
    """UAT-1.2: Login with Invalid Token
    
    Given: No existing token is stored.
    When: User runs `aye auth login` and enters an invalid token.
    Then: Stores the token anyway, displays success, but fails to download plugins.
    """
    with patch('aye.model.auth.typer.prompt', return_value='invalid_token') as mock_prompt, \
         patch('aye.model.auth.typer.secho') as mock_secho, \
         patch('aye.presenter.cli_ui.print_generic_message') as mock_print_error, \
         patch('aye.controller.commands.download_plugins.fetch_plugins', side_effect=Exception('API error message')) as mock_fetch_plugins:
        
        assert not temp_config_file.exists()
        
        main_cli.login()
        
        mock_prompt.assert_called_once_with('Paste your token', hide_input=True)
        mock_secho.assert_called_once_with('✅ Token saved.', fg=typer.colors.GREEN)
        
        config_content = temp_config_file.read_text(encoding='utf-8')
        assert '[default]' in config_content
        assert 'token=invalid_token' in config_content
        
        mock_fetch_plugins.assert_called_once()
        mock_print_error.assert_called_with('Login failed: API error message', is_error=True)


def test_uat_1_3_login_when_token_already_exists(temp_config_file):
    """UAT-1.3: Login When Token Already Exists
    
    Given: A valid token is already stored.
    When: User runs `aye auth login` and enters a new token.
    Then: Overwrites the existing token, displays success, attempts plugin download.
    """
    auth.set_user_config('token', 'old_token')
    assert temp_config_file.exists()
    initial_content = temp_config_file.read_text(encoding='utf-8')
    assert 'token=old_token' in initial_content
    
    with patch('aye.model.auth.typer.prompt', return_value='new_token') as mock_prompt, \
         patch('aye.model.auth.typer.secho') as mock_secho, \
         patch('aye.controller.commands.download_plugins.fetch_plugins') as mock_fetch_plugins:
        
        main_cli.login()
        
        mock_prompt.assert_called_once_with('Paste your token', hide_input=True)
        mock_secho.assert_called_once_with('✅ Token saved.', fg=typer.colors.GREEN)
        
        updated_content = temp_config_file.read_text(encoding='utf-8')
        assert '[default]' in updated_content
        assert 'token=new_token' in updated_content
        assert 'token=old_token' not in updated_content
        
        mock_fetch_plugins.assert_called_once()


def test_uat_1_4_login_with_network_failure_during_plugin_download(temp_config_file):
    """UAT-1.4: Login with Network Failure During Plugin Download
    
    Given: No existing token is stored.
    When: User runs `aye auth login` and enters a valid token, but plugin download fails due to network issues.
    Then: Stores the token, displays success for token saving, but shows an error for plugin download failure.
    """
    with patch('aye.model.auth.typer.prompt', return_value='valid_personal_access_token') as mock_prompt, \
         patch('aye.model.auth.typer.secho') as mock_secho, \
         patch('aye.presenter.cli_ui.print_generic_message') as mock_print_error, \
         patch('aye.controller.commands.download_plugins.fetch_plugins', side_effect=Exception('Network error')) as mock_fetch_plugins:
        
        assert not temp_config_file.exists()
        
        main_cli.login()
        
        mock_prompt.assert_called_once_with('Paste your token', hide_input=True)
        mock_secho.assert_called_once_with('✅ Token saved.', fg=typer.colors.GREEN)
        
        config_content = temp_config_file.read_text(encoding='utf-8')
        assert '[default]' in config_content
        assert 'token=valid_personal_access_token' in config_content
        
        mock_fetch_plugins.assert_called_once()
        mock_print_error.assert_called_with('Login failed: Network error', is_error=True)


def test_uat_1_5_login_cancelled_by_user(temp_config_file):
    """UAT-1.5: Login Cancelled by User
    
    Given: No existing token.
    When: User runs `aye auth login` but cancels the prompt (e.g., Ctrl+C).
    Then: The system does not store any token and exits without error.
    """
    with patch('aye.model.auth.typer.prompt', side_effect=typer.Abort) as mock_prompt, \
         patch('aye.model.auth.typer.secho') as mock_secho, \
         patch('aye.controller.commands.download_plugins.fetch_plugins') as mock_fetch_plugins:
        
        assert not temp_config_file.exists()
        
        # typer handles Abort internally, so we don't need to catch it
        # but the function will exit early.
        # We can't easily test the 'no error' part without running in a subprocess
        # so we just verify the side effects didn't happen.
        try:
            main_cli.login()
        except typer.Abort:
            pass # Expected
        
        mock_prompt.assert_called_once_with('Paste your token', hide_input=True)
        mock_secho.assert_not_called()
        assert not temp_config_file.exists()
        mock_fetch_plugins.assert_not_called()


def test_uat_1_6_login_with_environment_variable_override(temp_config_file):
    """UAT-1.6: Login with Environment Variable Override
    
    Given: AYE_TOKEN environment variable is set to a valid token.
    When: User runs `aye auth login` and enters a prompted token.
    Then: The system prioritizes the env var token for operations (e.g., plugin download), stores the prompted token.
    """
    os.environ['AYE_TOKEN'] = 'env_override_token'
    
    with patch('aye.model.auth.typer.prompt', return_value='prompted_token') as mock_prompt, \
         patch('aye.model.auth.typer.secho') as mock_secho, \
         patch('aye.controller.commands.download_plugins.fetch_plugins') as mock_fetch_plugins:
        
        assert not temp_config_file.exists()
        
        main_cli.login()
        
        mock_prompt.assert_called_once_with('Paste your token', hide_input=True)
        mock_secho.assert_called_once_with('✅ Token saved.', fg=typer.colors.GREEN)
        
        config_content = temp_config_file.read_text(encoding='utf-8')
        assert '[default]' in config_content
        assert 'token=prompted_token' in config_content
        
        # get_token will prioritize the environment variable
        assert auth.get_token() == 'env_override_token'
        
        mock_fetch_plugins.assert_called_once()
    
    del os.environ['AYE_TOKEN']


def test_uat_2_1_successful_logout_when_token_exists(temp_config_file):
    """UAT-2.1: Successful Logout When Token Exists
    
    Given: A token is stored in ~/.ayecfg.
    When: User runs `aye auth logout`.
    Then: Removes the token, displays '🔐 Token removed.', preserves file if other config exists.
    """
    auth.set_user_config('token', 'existing_token')
    assert temp_config_file.exists()
    
    with patch('aye.presenter.cli_ui.print_generic_message') as mock_print:
        main_cli.logout()
        mock_print.assert_called_once_with('🔐 Token removed.')
        
        if temp_config_file.exists():
            config_content = temp_config_file.read_text(encoding='utf-8')
            assert 'token=' not in config_content
        else:
            pass


def test_uat_2_2_logout_when_no_token_exists(temp_config_file):
    """UAT-2.2: Logout When No Token Exists
    
    Given: No token stored (empty or missing ~/.ayecfg).
    When: User runs `aye auth logout`.
    Then: Displays '🔐 Token removed.' (idempotent behavior).
    """
    assert not temp_config_file.exists()
    
    with patch('aye.presenter.cli_ui.print_generic_message') as mock_print:
        main_cli.logout()
        mock_print.assert_called_once_with('🔐 Token removed.')
        assert not temp_config_file.exists()


def test_uat_2_3_logout_preserves_other_config(temp_config_file):
    """UAT-2.3: Logout Preserves Other Config
    
    Given: ~/.ayecfg contains token and other settings (e.g., selected_model).
    When: User runs `aye auth logout`.
    Then: Removes only the token, preserves other settings, keeps the file.
    """
    auth.set_user_config('token', 'existing_token')
    auth.set_user_config('selected_model', 'x-ai/grok')
    assert temp_config_file.exists()
    
    with patch('aye.presenter.cli_ui.print_generic_message') as mock_print:
        main_cli.logout()
        mock_print.assert_called_once_with('🔐 Token removed.')
        
        assert temp_config_file.exists()
        updated_content = temp_config_file.read_text(encoding='utf-8')
        assert 'token=' not in updated_content
        assert 'selected_model=x-ai/grok' in updated_content


def test_uat_2_4_logout_with_environment_variable_set(temp_config_file):
    """UAT-2.4: Logout with Environment Variable Set
    
    Given: AYE_TOKEN is set, but no file-based token.
    When: User runs `aye auth logout`.
    Then: Displays '🔐 Token removed.' but env var remains (since it doesn't control env vars).
    """
    os.environ['AYE_TOKEN'] = 'env_token'
    assert not temp_config_file.exists()
    
    with patch('aye.presenter.cli_ui.print_generic_message') as mock_print:
        main_cli.logout()
        mock_print.assert_called_once_with('🔐 Token removed.')
        assert os.environ.get('AYE_TOKEN') == 'env_token'
        assert not temp_config_file.exists()
    
    del os.environ['AYE_TOKEN']
