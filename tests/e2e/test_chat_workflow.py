import json
import os
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pexpect
import pytest

# --- Mock API Server ---

class MockAyeHandler(BaseHTTPRequestHandler):
    """
    Mock handler for Aye Chat API.
    Handles /plugins (startup), /time, and /invoke (chat).
    """
    
    def log_message(self, format, *args):
        # Silence server logs to keep test output clean
        pass

    def _send_json(self, data):
        self.send_response(200)
        self.send_header('Content-type', 'application/json')
        self.end_headers()
        self.wfile.write(json.dumps(data).encode('utf-8'))

    def do_GET(self):
        # Handle server time check which is often a GET
        response_data = {}
        if self.path.endswith('/time'):
            response_data = {"timestamp": int(time.time())}
        
        self._send_json(response_data)

    def do_POST(self):
        # Read body to consume the stream (avoid broken pipe), though we might ignore it
        content_length = int(self.headers.get('Content-Length', 0))
        if content_length > 0:
            self.rfile.read(content_length)
        
        response_data = {}
        
        if 'plugins' in self.path:
            # Return empty plugins list to satisfy startup check
            response_data = {
                "plugins": {}
            }
        else:
            # Assume chat invocation (e.g. /invoke_cli)
            # We simulate the AI response structure expected by Aye Chat
            
            # The inner JSON represents the LLM's decision to update a file
            inner_response = {
                "answer_summary": "I have updated hello.py as requested.",
                "source_files": [
                    {
                        "file_name": "hello.py",
                        "file_content": "print('AI was here')"
                    }
                ]
            }
            
            response_data = {
                "assistant_response": json.dumps(inner_response),
                "chat_id": 12345
            }

        self._send_json(response_data)

@pytest.fixture(scope="module")
def mock_api_server():
    """Starts a local HTTP server in a separate thread to mock the Aye Backend."""
    # Use port 0 to let OS select a free port
    server = HTTPServer(('localhost', 0), MockAyeHandler)
    port = server.server_port
    base_url = f"http://localhost:{port}"
    
    thread = threading.Thread(target=server.serve_forever)
    thread.daemon = True
    thread.start()
    
    yield base_url
    
    server.shutdown()
    server.server_close()

# --- E2E Tests ---

def XXXtest_chat_workflow(tmp_path, mock_api_server):
    """
    Test the full chat workflow against the installed 'aye' application.
    
    Scenario:
    1. Start aye chat in a temp directory.
    2. Request a change (intercepted by Mock API).
    3. Verify file is updated on disk.
    4. Run 'restore' command.
    5. Verify file is reverted.
    """
    
    # 1. Setup Project Structure
    project_root = tmp_path / "test_project"
    project_root.mkdir()
    
    # Create a dummy home dir to isolate config and plugins
    fake_home = tmp_path / "fake_home"
    fake_home.mkdir()
    
    # Initialize a source file
    hello_file = project_root / "hello.py"
    original_content = "print('Original Content')"
    hello_file.write_text(original_content, encoding='utf-8')
    
    # 2. Environment Setup
    env = os.environ.copy()
    env["AYE_CHAT_API_URL"] = mock_api_server
    env["AYE_TOKEN"] = "test_token_123"
    env["HOME"] = str(fake_home)
    env["PYTHONKEYRING_BACKEND"] = "keyring.backends.null.Keyring"
    
    # Crucial for pexpect on Windows/Pipes: force unbuffered IO and UTF-8
    env["PYTHONUNBUFFERED"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"

    # 3. Spawn the Application
    cmd = f"aye chat --root {project_root}"
    
    # On Windows, pexpect.spawn (pty) is not available, so we use PopenSpawn (pipes).
    if sys.platform.startswith('win'):
        from pexpect.popen_spawn import PopenSpawn
        child = PopenSpawn(cmd, env=env, encoding='utf-8', timeout=10)
    else:
        child = pexpect.spawn(cmd, env=env, encoding='utf-8', timeout=10)
    
    # Optional: Uncomment to see process output in test runner (use pytest -s)
    # child.logfile = sys.stdout 

    try:
        # 4. Wait for Startup
        # The prompt usually contains "»" (e.g., "(ツ» ")
        child.expect("»") 
        
        # 5. Send AI Request
        child.sendline("Update hello.py to show AI was here")
        
        # 6. Verify AI Response in UI
        # Our mock server returns this specific summary
        child.expect("I have updated hello.py")
        
        # Wait for control to return to prompt
        child.expect("»")
        
        # 7. Verify File System Change
        # The mock server payload dictated the file content to be "print('AI was here')"
        new_content = hello_file.read_text(encoding='utf-8')
        assert "AI was here" in new_content
        assert "Original Content" not in new_content
        
        # 8. Test Restore Command
        child.sendline("restore")
        
        # Expect confirmation message
        # Matches: "✅ All files restored to latest snapshot"
        child.expect("restored")
        child.expect("»")
        
        # 9. Verify File System Restoration
        restored_content = hello_file.read_text(encoding='utf-8')
        assert "Original Content" in restored_content
        assert "AI was here" not in restored_content
        
        # 10. Clean Exit
        child.sendline("exit")
        child.expect(pexpect.EOF)
        
    except pexpect.exceptions.TIMEOUT:
        # Helper for debugging timeouts
        print("\n\n--- PEXPECT TIMEOUT ---\n")
        # PopenSpawn might not have 'before' populated identically to spawn on timeout in some versions,
        # but usually it captures what was read so far.
        if hasattr(child, 'before'):
            print(f"Last output before timeout:\n{child.before}")
        raise
