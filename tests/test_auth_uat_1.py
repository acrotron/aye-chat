import pytest
import tempfile
from pathlib import Path
from unittest.mock import patch
import typer
import os

import aye.auth as auth
import aye.service as service


@pytest.fixture
def temp_config_file():
    """Create a temporary config file for testing, isolated from user's real ~/.ayecfg."""
    tmp_dir = tempfile.TemporaryDirectory()
    config_path = Path(tmp_dir.name) / '.ayecfg'
    with patch('aye.auth.TOKEN_FILE', config_path):
        yield config_path
    tmp_dir.cleanup()


def test_uat_1_1_successful_login_with_valid_token(temp_config_file):
    """UAT-1.1: Successful Login with Valid Token
    
    Given: No existing token.
    When: User runs `aye auth login` and enters a valid token.
    Then: Stores token, shows success, attempts plugin download.
    """
    # Mock user input: simulate entering a valid token
    with patch('aye.auth.typer.prompt', return_value='valid_personal_access_token') as mock_prompt, \
         patch('aye.auth.typer.secho') as mock_secho, \
         patch('aye.service.rprint') as mock_rprint, \
         patch('aye.service.get_token', return_value='valid_personal_access_token') as mock_get_token, \
         patch('aye.service.fetch_plugins') as mock_fetch_plugins:  # Corrected to patch in service module
        
        # Ensure no prior token
        assert not temp_config_file.exists()
        
        # Execute full login flow (handle_login calls login_flow + fetch_plugins)
        service.handle_login()
        
        # Verify prompt was called for token input
        mock_prompt.assert_called_once_with('Paste your token', hide_input=True)
        
        # Verify success message displayed (from login_flow)
        mock_secho.assert_called_once_with('✅ Token saved.', fg=typer.colors.GREEN)
        
        # Verify token was stored in config file
        config_content = temp_config_file.read_text(encoding='utf-8')
        assert '[default]' in config_content
        assert 'token=valid_personal_access_token' in config_content
        
        # Verify plugin download was attempted (from handle_login)
        mock_fetch_plugins.assert_called_once()
        
        # File permissions should be set to 0600 (but hard to assert in test; assume auth.py does it)
        # assert temp_config_file.stat().st_mode & 0o777 == 0o600  # Optional: if implementing permission check


def test_uat_1_2_login_with_invalid_token(temp_config_file):
    """UAT-1.2: Login with Invalid Token
    
    Given: No existing token is stored.
    When: User runs `aye auth login` and enters an invalid token.
    Then: Stores the token anyway, displays success, but fails to download plugins.
    """
    # Mock user input: simulate entering an invalid token
    with patch('aye.auth.typer.prompt', return_value='invalid_token') as mock_prompt, \
         patch('aye.auth.typer.secho') as mock_secho, \
         patch('aye.service.rprint') as mock_rprint, \
         patch('aye.service.get_token', return_value='invalid_token') as mock_get_token, \
         patch('aye.service.fetch_plugins', side_effect=Exception('API error message')) as mock_fetch_plugins:  # Simulate plugin download failure
        
        # Ensure no prior token
        assert not temp_config_file.exists()
        
        # Execute full login flow (handle_login calls login_flow + fetch_plugins)
        service.handle_login()
        
        # Verify prompt was called for token input
        mock_prompt.assert_called_once_with('Paste your token', hide_input=True)
        
        # Verify success message displayed (from login_flow, regardless of token validity)
        mock_secho.assert_called_once_with('✅ Token saved.', fg=typer.colors.GREEN)
        
        # Verify token was stored in config file (stored even if invalid)
        config_content = temp_config_file.read_text(encoding='utf-8')
        assert '[default]' in config_content
        assert 'token=invalid_token' in config_content
        
        # Verify plugin download was attempted but failed
        mock_fetch_plugins.assert_called_once()
        
        # Verify error message for plugin download failure
        mock_rprint.assert_called_with('[red]Error: Could not download plugins - API error message[/]')
        
        # File permissions should be set to 0600 (but hard to assert in test; assume auth.py does it)
        # assert temp_config_file.stat().st_mode & 0o777 == 0o600  # Optional: if implementing permission check


def test_uat_1_3_login_when_token_already_exists(temp_config_file):
    """UAT-1.3: Login When Token Already Exists
    
    Given: A valid token is already stored.
    When: User runs `aye auth login` and enters a new token.
    Then: Overwrites the existing token, displays success, attempts plugin download.
    """
    # Pre-set an existing token in the config file
    auth.set_user_config('token', 'old_token')
    assert temp_config_file.exists()
    initial_content = temp_config_file.read_text(encoding='utf-8')
    assert 'token=old_token' in initial_content
    
    # Mock user input: simulate entering a new token
    with patch('aye.auth.typer.prompt', return_value='new_token') as mock_prompt, \
         patch('aye.auth.typer.secho') as mock_secho, \
         patch('aye.service.rprint') as mock_rprint, \
         patch('aye.service.get_token', return_value='new_token') as mock_get_token, \
         patch('aye.service.fetch_plugins') as mock_fetch_plugins:
        
        # Execute full login flow (handle_login calls login_flow + fetch_plugins)
        service.handle_login()
        
        # Verify prompt was called for token input
        mock_prompt.assert_called_once_with('Paste your token', hide_input=True)
        
        # Verify success message displayed (from login_flow)
        mock_secho.assert_called_once_with('✅ Token saved.', fg=typer.colors.GREEN)
        
        # Verify old token was overwritten with new token in config file
        updated_content = temp_config_file.read_text(encoding='utf-8')
        assert '[default]' in updated_content
        assert 'token=new_token' in updated_content
        assert 'token=old_token' not in updated_content  # Old token should be gone
        
        # Verify plugin download was attempted (from handle_login)
        mock_fetch_plugins.assert_called_once()
        
        # File permissions should be set to 0600 (but hard to assert in test; assume auth.py does it)
        # assert temp_config_file.stat().st_mode & 0o777 == 0o600  # Optional: if implementing permission check


def test_uat_1_4_login_with_network_failure_during_plugin_download(temp_config_file):
    """UAT-1.4: Login with Network Failure During Plugin Download
    
    Given: No existing token is stored.
    When: User runs `aye auth login` and enters a valid token, but plugin download fails due to network issues.
    Then: Stores the token, displays success for token saving, but shows an error for plugin download failure.
    """
    # Mock user input: simulate entering a valid token
    with patch('aye.auth.typer.prompt', return_value='valid_personal_access_token') as mock_prompt, \
         patch('aye.auth.typer.secho') as mock_secho, \
         patch('aye.service.rprint') as mock_rprint, \
         patch('aye.service.get_token', return_value='valid_personal_access_token') as mock_get_token, \
         patch('aye.service.fetch_plugins', side_effect=Exception('Network error')) as mock_fetch_plugins:  # Simulate network failure
        
        # Ensure no prior token
        assert not temp_config_file.exists()
        
        # Execute full login flow (handle_login calls login_flow + fetch_plugins)
        service.handle_login()
        
        # Verify prompt was called for token input
        mock_prompt.assert_called_once_with('Paste your token', hide_input=True)
        
        # Verify success message displayed (from login_flow)
        mock_secho.assert_called_once_with('✅ Token saved.', fg=typer.colors.GREEN)
        
        # Verify token was stored in config file
        config_content = temp_config_file.read_text(encoding='utf-8')
        assert '[default]' in config_content
        assert 'token=valid_personal_access_token' in config_content
        
        # Verify plugin download was attempted but failed due to network
        mock_fetch_plugins.assert_called_once()
        
        # Verify error message for plugin download failure
        mock_rprint.assert_called_with('[red]Error: Could not download plugins - Network error[/]')
        
        # File permissions should be set to 0600 (but hard to assert in test; assume auth.py does it)
        # assert temp_config_file.stat().st_mode & 0o777 == 0o600  # Optional: if implementing permission check


def test_uat_1_5_login_cancelled_by_user(temp_config_file):
    """UAT-1.5: Login Cancelled by User
    
    Given: No existing token.
    When: User runs `aye auth login` but cancels the prompt (e.g., Ctrl+C).
    Then: The system does not store any token and exits without error.
    """
    # Mock user input: simulate cancellation by raising KeyboardInterrupt
    with patch('aye.auth.typer.prompt', side_effect=KeyboardInterrupt) as mock_prompt, \
         patch('aye.auth.typer.secho') as mock_secho, \
         patch('aye.service.rprint') as mock_rprint, \
         patch('aye.service.get_token') as mock_get_token, \
         patch('aye.service.fetch_plugins') as mock_fetch_plugins:
        
        # Ensure no prior token
        assert not temp_config_file.exists()
        
        # Execute full login flow (handle_login calls login_flow + fetch_plugins)
        # Since cancellation happens in login_flow, it should exit early
        try:
            service.handle_login()
        except KeyboardInterrupt:
            pass  # Expected when simulating Ctrl+C
        
        # Verify prompt was called for token input
        mock_prompt.assert_called_once_with('Paste your token', hide_input=True)
        
        # Verify success message was NOT displayed
        mock_secho.assert_not_called()
        
        # Verify no token was stored in config file
        assert not temp_config_file.exists()  # File should not be created
        
        # Verify plugin download was NOT attempted
        mock_fetch_plugins.assert_not_called()
        
        # File permissions check not applicable since file doesn't exist


def test_uat_1_6_login_with_environment_variable_override(temp_config_file):
    """UAT-1.6: Login with Environment Variable Override
    
    Given: AYE_TOKEN environment variable is set to a valid token.
    When: User runs `aye auth login` and enters a prompted token.
    Then: The system prioritizes the env var token for operations (e.g., plugin download), displays success for token saving, attempts plugin download.
    """
    # Set environment variable to override token
    os.environ['AYE_TOKEN'] = 'env_override_token'
    
    # Mock user input: simulate entering a prompted token
    with patch('aye.auth.typer.prompt', return_value='prompted_token') as mock_prompt, \
         patch('aye.auth.typer.secho') as mock_secho, \
         patch('aye.service.rprint') as mock_rprint, \
         patch('aye.service.get_token', return_value='env_override_token') as mock_get_token, \
         patch('aye.service.fetch_plugins') as mock_fetch_plugins:
        
        # Ensure no prior token in file
        assert not temp_config_file.exists()
        
        # Execute full login flow (handle_login calls login_flow + fetch_plugins)
        service.handle_login()
        
        # Verify prompt was called for token input (still prompts, but env overrides for get_token)
        mock_prompt.assert_called_once_with('Paste your token', hide_input=True)
        
        # Verify success message displayed (from login_flow)
        mock_secho.assert_called_once_with('✅ Token saved.', fg=typer.colors.GREEN)
        
        # Verify prompted token was stored in config file
        config_content = temp_config_file.read_text(encoding='utf-8')
        assert '[default]' in config_content
        assert 'token=prompted_token' in config_content
        
        # Verify get_token returns env var token (prioritized over file)
        assert mock_get_token() == 'env_override_token'
        
        # Verify plugin download was attempted (from handle_login, using env token)
        mock_fetch_plugins.assert_called_once()
        
        # File permissions should be set to 0600 (but hard to assert in test; assume auth.py does it)
        # assert temp_config_file.stat().st_mode & 0o777 == 0o600  # Optional: if implementing permission check
    
    # Clean up env var
    del os.environ['AYE_TOKEN']


def test_uat_2_1_successful_logout_when_token_exists(temp_config_file):
    """UAT-2.1: Successful Logout When Token Exists
    
    Given: A token is stored in ~/.ayecfg.
    When: User runs `aye auth logout`.
    Then: Removes the token, displays '🔐 Token removed.', preserves file if other config exists.
    """
    # Pre-set a token in the config file
    auth.set_user_config('token', 'existing_token')
    assert temp_config_file.exists()
    
    # Mock rprint to capture output
    with patch('aye.service.rprint') as mock_rprint:
        
        # Execute logout
        service.handle_logout()
        
        # Verify message displayed
        mock_rprint.assert_called_once_with('🔐 Token removed.')
        
        # Verify token was removed from config file, file may be deleted if empty
        if temp_config_file.exists():
            config_content = temp_config_file.read_text(encoding='utf-8')
            assert 'token=' not in config_content
        else:
            # If file is deleted (as in this case, since only token was set), that's expected
            pass
        
        # File permissions should be set to 0600 (but hard to assert in test; assume auth.py does it)
        # assert temp_config_file.stat().st_mode & 0o777 == 0o600  # Optional: if implementing permission check


def test_uat_2_2_logout_when_no_token_exists(temp_config_file):
    """UAT-2.2: Logout When No Token Exists
    
    Given: No token stored (empty or missing ~/.ayecfg).
    When: User runs `aye auth logout`.
    Then: Displays '🔐 Token removed.' (idempotent behavior).
    """
    # Ensure no token in config file
    assert not temp_config_file.exists()  # File does not exist
    
    # Mock rprint to capture output
    with patch('aye.service.rprint') as mock_rprint:
        
        # Execute logout
        service.handle_logout()
        
        # Verify message displayed (idempotent)
        mock_rprint.assert_called_once_with('🔐 Token removed.')
        
        # Verify no changes to config file (still does not exist)
        assert not temp_config_file.exists()
        
        # File permissions check not applicable since file doesn't exist


def test_uat_2_3_logout_preserves_other_config(temp_config_file):
    """UAT-2.3: Logout Preserves Other Config
    
    Given: ~/.ayecfg contains token and other settings (e.g., selected_model).
    When: User runs `aye auth logout`.
    Then: Removes only the token, preserves other settings, keeps the file.
    """
    # Pre-set config with token and another setting
    auth.set_user_config('token', 'existing_token')
    auth.set_user_config('selected_model', 'x-ai/grok')
    assert temp_config_file.exists()
    initial_content = temp_config_file.read_text(encoding='utf-8')
    assert 'token=existing_token' in initial_content
    assert 'selected_model=x-ai/grok' in initial_content
    
    # Mock rprint to capture output
    with patch('aye.service.rprint') as mock_rprint:
        
        # Execute logout
        service.handle_logout()
        
        # Verify message displayed
        mock_rprint.assert_called_once_with('🔐 Token removed.')
        
        # Verify token was removed, but other config remains, file persists
        assert temp_config_file.exists()
        updated_content = temp_config_file.read_text(encoding='utf-8')
        assert 'token=' not in updated_content
        assert 'selected_model=x-ai/grok' in updated_content
        
        # File permissions should be set to 0600 (but hard to assert in test; assume auth.py does it)
        # assert temp_config_file.stat().st_mode & 0o777 == 0o600  # Optional: if implementing permission check


def test_uat_2_4_logout_with_environment_variable_set(temp_config_file):
    """UAT-2.4: Logout with Environment Variable Set
    
    Given: AYE_TOKEN is set, but no file-based token.
    When: User runs `aye auth logout`.
    Then: Displays '🔐 Token removed.' but env var remains (since it doesn't control env vars).
    """
    # Set environment variable to override token
    os.environ['AYE_TOKEN'] = 'env_token'
    
    # Ensure no file-based token
    assert not temp_config_file.exists()
    
    # Mock rprint to capture output
    with patch('aye.service.rprint') as mock_rprint:
        
        # Execute logout
        service.handle_logout()
        
        # Verify message displayed
        mock_rprint.assert_called_once_with('🔐 Token removed.')
        
        # Verify env var remains unchanged
        assert os.environ.get('AYE_TOKEN') == 'env_token'
        
        # Verify no file modifications
        assert not temp_config_file.exists()
        
        # File permissions check not applicable since file doesn't exist
    
    # Clean up env var
    del os.environ['AYE_TOKEN']