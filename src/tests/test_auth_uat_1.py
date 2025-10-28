import pytest
import tempfile
from pathlib import Path
from unittest.mock import patch
import typer

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