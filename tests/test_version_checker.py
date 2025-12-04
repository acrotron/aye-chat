# Test suite for aye.model.version_checker module
import io
import sys
from unittest import TestCase
from unittest.mock import patch, MagicMock

from aye.model import version_checker


class TestVersionChecker(TestCase):
    """Test suite for version checking functionality."""

    def test_get_current_version_success(self):
        """Test getting current version when package is installed."""
        with patch("importlib.metadata.version") as mock_version:
            mock_version.return_value = "0.26.0"
            result = version_checker.get_current_version()
            self.assertEqual(result, "0.26.0")

    def test_get_current_version_package_not_found(self):
        """Test fallback when package is not found."""
        from importlib.metadata import PackageNotFoundError
        with patch("importlib.metadata.version") as mock_version:
            mock_version.side_effect = PackageNotFoundError()
            result = version_checker.get_current_version()
            self.assertEqual(result, "0.0.0")

    def test_get_latest_stable_version_info_success(self):
        """Test fetching latest stable version from PyPI successfully."""
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "info": {
                "version": "0.27.0",
                "requires_python": ">=3.8, <3.14"
            },
            "releases": {
                "0.25.0": [],
                "0.26.0": [],
                "0.27.0": [],
                "0.28.0a1": [],  # Prerelease should be excluded
            }
        }

        with patch("aye.model.version_checker.httpx.get") as mock_get:
            mock_get.return_value = mock_response
            result = version_checker.get_latest_stable_version_info()

            self.assertIsNotNone(result)
            version_str, python_requires = result
            self.assertEqual(version_str, "0.27.0")
            self.assertEqual(python_requires, ">=3.8, <3.14")

    def test_get_latest_stable_version_info_excludes_prereleases(self):
        """Test that prereleases are excluded from version selection."""
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "info": {
                "version": "0.28.0a1",
                "requires_python": ">=3.8, <3.14"
            },
            "releases": {
                "0.25.0": [],
                "0.26.0": [],
                "0.27.0": [],
                "0.28.0a1": [],  # Latest is prerelease
                "0.28.0b1": [],  # Beta
                "0.28.0rc1": [],  # Release candidate
            }
        }

        with patch("aye.model.version_checker.httpx.get") as mock_get:
            mock_get.return_value = mock_response
            result = version_checker.get_latest_stable_version_info()

            self.assertIsNotNone(result)
            version_str, python_requires = result
            # Should return 0.27.0, not the 0.28.0 prereleases
            self.assertEqual(version_str, "0.27.0")

    def test_get_latest_stable_version_info_network_failure(self):
        """Test graceful failure when network request fails."""
        with patch("aye.model.version_checker.httpx.get") as mock_get:
            mock_get.side_effect = Exception("Network error")
            result = version_checker.get_latest_stable_version_info()
            self.assertIsNone(result)

    def test_get_latest_stable_version_info_timeout(self):
        """Test graceful failure on timeout."""
        with patch("aye.model.version_checker.httpx.get") as mock_get:
            import httpx
            mock_get.side_effect = httpx.TimeoutException("Timeout")
            result = version_checker.get_latest_stable_version_info()
            self.assertIsNone(result)

    def test_is_newer_version_available_true(self):
        """Test detection when newer version is available."""
        with patch("aye.model.version_checker.get_current_version") as mock_current, \
             patch("aye.model.version_checker.get_latest_stable_version_info") as mock_latest:
            mock_current.return_value = "0.26.0"
            mock_latest.return_value = ("0.27.0", ">=3.8, <3.14")

            is_newer, latest, current, python_requires = version_checker.is_newer_version_available()

            self.assertTrue(is_newer)
            self.assertEqual(latest, "0.27.0")
            self.assertEqual(current, "0.26.0")
            self.assertEqual(python_requires, ">=3.8, <3.14")

    def test_is_newer_version_available_false_same_version(self):
        """Test when current version equals latest."""
        with patch("aye.model.version_checker.get_current_version") as mock_current, \
             patch("aye.model.version_checker.get_latest_stable_version_info") as mock_latest:
            mock_current.return_value = "0.26.0"
            mock_latest.return_value = ("0.26.0", ">=3.8, <3.14")

            is_newer, latest, current, python_requires = version_checker.is_newer_version_available()

            self.assertFalse(is_newer)
            self.assertEqual(latest, "0.26.0")
            self.assertEqual(current, "0.26.0")

    def test_is_newer_version_available_false_older_version(self):
        """Test when current version is newer than PyPI (dev version)."""
        with patch("aye.model.version_checker.get_current_version") as mock_current, \
             patch("aye.model.version_checker.get_latest_stable_version_info") as mock_latest:
            mock_current.return_value = "0.28.0"
            mock_latest.return_value = ("0.27.0", ">=3.8, <3.14")

            is_newer, latest, current, python_requires = version_checker.is_newer_version_available()

            self.assertFalse(is_newer)
            self.assertEqual(latest, "0.27.0")
            self.assertEqual(current, "0.28.0")

    def test_is_newer_version_available_network_failure(self):
        """Test when latest version cannot be fetched."""
        with patch("aye.model.version_checker.get_current_version") as mock_current, \
             patch("aye.model.version_checker.get_latest_stable_version_info") as mock_latest:
            mock_current.return_value = "0.26.0"
            mock_latest.return_value = None

            is_newer, latest, current, python_requires = version_checker.is_newer_version_available()

            self.assertFalse(is_newer)
            self.assertIsNone(latest)
            self.assertEqual(current, "0.26.0")
            self.assertIsNone(python_requires)

    def test_is_newer_version_available_excludes_prerelease(self):
        """Test that prereleases are not considered as newer versions."""
        with patch("aye.model.version_checker.get_current_version") as mock_current, \
             patch("aye.model.version_checker.get_latest_stable_version_info") as mock_latest:
            mock_current.return_value = "0.26.0"
            # Latest stable is 0.26.0, even though 0.27.0a1 exists
            mock_latest.return_value = ("0.26.0", ">=3.8, <3.14")

            is_newer, latest, current, python_requires = version_checker.is_newer_version_available()

            # Should not report an update since prereleases are excluded
            self.assertFalse(is_newer)
            self.assertEqual(latest, "0.26.0")
            self.assertEqual(current, "0.26.0")

    def test_parse_python_version_max_exclusive_upper_bound(self):
        """Test parsing exclusive upper bound like <3.14."""
        result = version_checker._parse_python_version_max(">=3.8, <3.14")
        self.assertEqual(result, "3.13")

    def test_parse_python_version_max_inclusive_upper_bound(self):
        """Test parsing inclusive upper bound like <=3.13."""
        result = version_checker._parse_python_version_max(">=3.8, <=3.13")
        self.assertEqual(result, "3.13")

    def test_parse_python_version_max_no_upper_bound(self):
        """Test when there's no upper bound specified."""
        result = version_checker._parse_python_version_max(">=3.8")
        self.assertIsNone(result)

    def test_parse_python_version_max_none_input(self):
        """Test with None input."""
        result = version_checker._parse_python_version_max(None)
        self.assertIsNone(result)

    def test_check_version_and_print_warning_with_update(self):
        """Test that warning is printed when update is available."""
        with patch("aye.model.version_checker.is_newer_version_available") as mock_check:
            mock_check.return_value = (True, "0.27.0", "0.26.0", ">=3.8, <3.14")

            # Capture stdout
            captured_output = io.StringIO()
            sys.stdout = captured_output

            try:
                version_checker.check_version_and_print_warning()
                output = captured_output.getvalue()

                # Verify warning message components
                self.assertIn("notice", output)
                self.assertIn("0.26.0", output)
                self.assertIn("0.27.0", output)
                self.assertIn("pip install --upgrade ayechat", output)
            finally:
                sys.stdout = sys.__stdout__

    def test_check_version_and_print_warning_with_update_no_python_info(self):
        """Test warning without Python version information."""
        with patch("aye.model.version_checker.is_newer_version_available") as mock_check:
            mock_check.return_value = (True, "0.27.0", "0.26.0", None)

            # Capture stdout
            captured_output = io.StringIO()
            sys.stdout = captured_output

            try:
                version_checker.check_version_and_print_warning()
                output = captured_output.getvalue()

                # Verify warning message components
                self.assertIn("notice", output)
                self.assertIn("0.26.0", output)
                self.assertIn("0.27.0", output)
                # Should not contain Python version info
                self.assertNotIn("Supports Python", output)
            finally:
                sys.stdout = sys.__stdout__

    def test_check_version_and_print_warning_no_update(self):
        """Test that no warning is printed when no update is available."""
        with patch("aye.model.version_checker.is_newer_version_available") as mock_check:
            mock_check.return_value = (False, "0.26.0", "0.26.0", ">=3.8, <3.14")

            # Capture stdout
            captured_output = io.StringIO()
            sys.stdout = captured_output

            try:
                version_checker.check_version_and_print_warning()
                output = captured_output.getvalue()

                # Should be empty or minimal output
                self.assertEqual(output, "")
            finally:
                sys.stdout = sys.__stdout__

    def test_check_version_and_print_warning_network_failure(self):
        """Test that no error is shown when version check fails."""
        with patch("aye.model.version_checker.is_newer_version_available") as mock_check:
            mock_check.return_value = (False, None, "0.26.0", None)

            # Capture stdout
            captured_output = io.StringIO()
            sys.stdout = captured_output

            try:
                version_checker.check_version_and_print_warning()
                output = captured_output.getvalue()

                # Should not print anything on network failure
                self.assertEqual(output, "")
            finally:
                sys.stdout = sys.__stdout__
