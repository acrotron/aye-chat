import unittest
from unittest.mock import patch, MagicMock
import os

import aye.model.onnx_manager as onnx_manager

class TestOnnxManager(unittest.TestCase):
    def setUp(self):
        # Reset global state
        onnx_manager._status = 'NOT_CHECKED'
        # Ensure flag file doesn't exist
        if onnx_manager._model_flag_file.exists():
            onnx_manager._model_flag_file.unlink()

    def tearDown(self):
        # Clean up
        if onnx_manager._model_flag_file.exists():
            onnx_manager._model_flag_file.unlink()

    def test_get_model_status_initial(self):
        self.assertEqual(onnx_manager.get_model_status(), 'NOT_DOWNLOADED')

    @patch('chromadb.utils.embedding_functions.ONNXMiniLM_L6_V2')
    def test_get_model_status_downloading(self, mock_onnx):
        # Simulate download in progress by setting status manually
        onnx_manager._status = 'DOWNLOADING'
        status = onnx_manager.get_model_status()
        self.assertEqual(status, 'DOWNLOADING')
        # Call again to check caching
        status = onnx_manager.get_model_status()
        self.assertEqual(status, 'DOWNLOADING')

    @patch('chromadb.utils.embedding_functions.ONNXMiniLM_L6_V2')
    def test_get_model_status_ready(self, mock_onnx):
        # Create the flag file to simulate ready status
        onnx_manager._model_flag_file.parent.mkdir(parents=True, exist_ok=True)
        onnx_manager._model_flag_file.touch()
        status = onnx_manager.get_model_status()
        self.assertEqual(status, 'READY')

    @patch('chromadb.utils.embedding_functions.ONNXMiniLM_L6_V2')
    def test_get_model_status_failed(self, mock_onnx):
        # Simulate failed by setting status manually
        onnx_manager._status = 'FAILED'
        status = onnx_manager.get_model_status()
        self.assertEqual(status, 'FAILED')

    @patch('chromadb.utils.embedding_functions.ONNXMiniLM_L6_V2')
    def test_get_model_status_thread_safety(self, mock_onnx):
        # Simulate concurrent calls
        import threading
        results = []
        def call_get_status():
            results.append(onnx_manager.get_model_status())
        threads = [threading.Thread(target=call_get_status) for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        # All should be consistent
        self.assertTrue(all(r == results[0] for r in results))

if __name__ == '__main__':
    unittest.main()