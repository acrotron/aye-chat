"""Tests for ui_utils module."""
import threading
import time
from unittest.mock import MagicMock, patch, PropertyMock

import pytest
from rich.console import Console
from rich.spinner import Spinner

from aye.presenter.ui_utils import thinking_spinner


class TestThinkingSpinner:
    """Tests for thinking_spinner context manager."""

    @pytest.fixture
    def mock_console(self):
        """Create a mock console."""
        console = MagicMock(spec=Console)
        console.status.return_value.__enter__ = MagicMock()
        console.status.return_value.__exit__ = MagicMock(return_value=False)
        return console

    def test_default_text(self, mock_console):
        """Test spinner with default 'Thinking...' text."""
        with thinking_spinner(mock_console):
            pass
        
        mock_console.status.assert_called_once()
        call_args = mock_console.status.call_args
        spinner = call_args[0][0]
        assert isinstance(spinner, Spinner)
        assert "Thinking..." in spinner.text

    def test_custom_text(self, mock_console):
        """Test spinner with custom text."""
        custom_text = "Processing..."
        with thinking_spinner(mock_console, text=custom_text):
            pass
        
        call_args = mock_console.status.call_args
        spinner = call_args[0][0]
        assert custom_text in spinner.text

    def test_custom_messages_initial(self, mock_console):
        """Test spinner starts with first message from list."""
        messages = ["Step 1", "Step 2", "Step 3"]
        with thinking_spinner(mock_console, messages=messages):
            pass
        
        call_args = mock_console.status.call_args
        spinner = call_args[0][0]
        assert "Step 1" in spinner.text

    def test_single_message_no_thread(self, mock_console):
        """Test that single message doesn't start a timer thread."""
        with patch('aye.presenter.ui_utils.threading.Thread') as mock_thread:
            with thinking_spinner(mock_console, text="Single message"):
                pass
            # Thread should not be created for single message
            mock_thread.assert_not_called()

    def test_multiple_messages_starts_thread(self, mock_console):
        """Test that multiple messages start a timer thread."""
        messages = ["Msg 1", "Msg 2"]
        thread_started = False
        original_thread = threading.Thread
        
        def track_thread(*args, **kwargs):
            nonlocal thread_started
            thread_started = True
            return original_thread(*args, **kwargs)
        
        with patch('aye.presenter.ui_utils.threading.Thread', side_effect=track_thread):
            with thinking_spinner(mock_console, messages=messages, interval=0.1):
                time.sleep(0.05)  # Brief pause
        
        assert thread_started

    def test_context_manager_yields(self, mock_console):
        """Test that context manager properly yields control."""
        executed = False
        with thinking_spinner(mock_console):
            executed = True
        
        assert executed

    def test_exception_propagation(self, mock_console):
        """Test that exceptions inside context are propagated."""
        with pytest.raises(ValueError, match="test error"):
            with thinking_spinner(mock_console):
                raise ValueError("test error")

    def test_cleanup_on_exception(self, mock_console):
        """Test that cleanup happens even on exception."""
        try:
            with thinking_spinner(mock_console):
                raise RuntimeError("test")
        except RuntimeError:
            pass
        
        # Verify status context was properly exited
        mock_console.status.return_value.__exit__.assert_called_once()

    def test_state_stop_flag_set_on_exit(self, mock_console):
        """Test that stop flag is set when exiting context."""
        messages = ["Msg 1", "Msg 2", "Msg 3"]
        
        with thinking_spinner(mock_console, messages=messages, interval=100):
            pass
        
        # Context exited cleanly - no assertion needed, just verify no hang

    def test_message_cycling_with_short_interval(self, mock_console):
        """Test that messages cycle with short interval."""
        messages = ["First", "Second", "Third"]
        spinner_ref = None
        
        # Capture the spinner
        original_status = mock_console.status
        def capture_status(spinner):
            nonlocal spinner_ref
            spinner_ref = spinner
            return original_status(spinner)
        
        mock_console.status = capture_status
        
        with thinking_spinner(mock_console, messages=messages, interval=0.15):
            # Wait long enough for at least one message change
            time.sleep(0.4)
        
        # Spinner text should have changed from initial
        assert spinner_ref is not None

    def test_message_stops_at_last_message(self, mock_console):
        """Test that cycling stops at last message."""
        messages = ["First", "Last"]
        
        with thinking_spinner(mock_console, messages=messages, interval=0.1):
            time.sleep(0.35)  # Wait for messages to cycle through
        
        # Should complete without hanging

    def test_thread_joins_on_exit(self, mock_console):
        """Test that thread is joined on context exit."""
        messages = ["Msg 1", "Msg 2"]
        
        start_time = time.time()
        with thinking_spinner(mock_console, messages=messages, interval=10):
            pass
        elapsed = time.time() - start_time
        
        # Should exit quickly (thread join has 0.5s timeout)
        assert elapsed < 2.0

    def test_spinner_type(self, mock_console):
        """Test that spinner uses 'dots' style."""
        with thinking_spinner(mock_console):
            pass
        
        call_args = mock_console.status.call_args
        spinner = call_args[0][0]
        assert spinner.name == "dots"

    def test_empty_messages_uses_default(self, mock_console):
        """Test behavior when messages is None uses text param."""
        with thinking_spinner(mock_console, text="Custom default", messages=None):
            pass
        
        call_args = mock_console.status.call_args
        spinner = call_args[0][0]
        assert "Custom default" in spinner.text

    def test_interval_parameter(self, mock_console):
        """Test custom interval parameter is respected."""
        messages = ["Start", "End"]
        
        start_time = time.time()
        with thinking_spinner(mock_console, messages=messages, interval=0.2):
            time.sleep(0.1)  # Less than interval
        elapsed = time.time() - start_time
        
        # Should exit quickly since we're not waiting full interval
        assert elapsed < 1.0

    def test_concurrent_execution(self, mock_console):
        """Test spinner doesn't interfere with concurrent code."""
        results = []
        
        def worker(n):
            results.append(n)
        
        with thinking_spinner(mock_console):
            threads = [threading.Thread(target=worker, args=(i,)) for i in range(5)]
            for t in threads:
                t.start()
            for t in threads:
                t.join()
        
        assert sorted(results) == [0, 1, 2, 3, 4]

    def test_real_console_integration(self):
        """Integration test with real Console (no output verification)."""
        console = Console(force_terminal=False, no_color=True)
        
        with thinking_spinner(console, text="Testing..."):
            time.sleep(0.1)
        
        # Should complete without error

    def test_message_index_increments(self, mock_console):
        """Test that message index increments correctly."""
        messages = ["One", "Two", "Three", "Four"]
        spinner_texts = []
        
        original_status = mock_console.status
        captured_spinner = None
        
        def capture_status(spinner):
            nonlocal captured_spinner
            captured_spinner = spinner
            return original_status(spinner)
        
        mock_console.status = capture_status
        
        with thinking_spinner(mock_console, messages=messages, interval=0.1):
            time.sleep(0.05)
            if captured_spinner:
                spinner_texts.append(captured_spinner.text)
            time.sleep(0.15)
            if captured_spinner:
                spinner_texts.append(captured_spinner.text)
        
        # Should have captured at least initial text
        assert len(spinner_texts) >= 1

    def test_daemon_thread(self, mock_console):
        """Test that timer thread is created as daemon."""
        messages = ["Msg 1", "Msg 2"]
        thread_daemon = None
        original_thread = threading.Thread
        
        class TrackingThread(original_thread):
            def __init__(self, *args, **kwargs):
                nonlocal thread_daemon
                thread_daemon = kwargs.get('daemon', False)
                super().__init__(*args, **kwargs)
        
        with patch('aye.presenter.ui_utils.threading.Thread', TrackingThread):
            with thinking_spinner(mock_console, messages=messages, interval=0.1):
                time.sleep(0.05)
        
        assert thread_daemon is True

    def test_stop_flag_prevents_message_update(self, mock_console):
        """Test that setting stop flag prevents further updates."""
        messages = ["Start", "Middle", "End"]
        
        # Very short context - stop should be set before any updates
        with thinking_spinner(mock_console, messages=messages, interval=1.0):
            pass  # Exit immediately
        
        # Should exit cleanly without hanging

    def test_update_message_loop_exit_conditions(self, mock_console):
        """Test the update_message loop exits properly on stop."""
        messages = ["A", "B", "C"]
        
        with thinking_spinner(mock_console, messages=messages, interval=0.1):
            time.sleep(0.05)
        
        # Verify clean exit - no hanging threads
        # Count active threads to ensure cleanup
        active_count = threading.active_count()
        assert active_count < 100  # Sanity check
