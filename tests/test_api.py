# Test suite for aye.api module
import logging
import os
import base64

from unittest import TestCase
from unittest.mock import patch, MagicMock, mock_open
import unittest
from aye import api

class TestApi(unittest.TestCase):
    
    @classmethod
    def setUpClass(cls):
        pass


    @classmethod
    def tearDownClass(cls):
        pass


    def test_foo(self):
        # TODO: Add actual test logic here, e.g., mock httpx and test cli_invoke
        self.assertTrue(False)


def run_specific_tests():
    # Create a test suite
    suite = unittest.TestSuite()

    # Add specific test methods to the suite
    suite.addTest(TestApi('test_foo'))  # Fixed: Use TestApi instead of TestUtil

    # Create a test runner
    runner = unittest.TextTestRunner()

    # Run the specific test methods
    result = runner.run(suite)
    status = int(len(result.failures) + len(result.errors) > 0)
    return status


if __name__ == '__main__':
    unittest.main()
