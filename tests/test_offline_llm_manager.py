import unittest
import threading
import tempfile
from pathlib import Path
from unittest.mock import patch, MagicMock, call

import aye.model.offline_llm_manager as offline_llm_manager


class TestOfflineLlmManager(unittest.TestCase):
    def setUp(self):
        # Reset global state
        offline_llm_manager._model_status.clear()
        
        # Create temporary cache directory
        self.tmpdir = tempfile.TemporaryDirectory()
        self.cache_dir = Path(self.tmpdir.name) / "offline_models"
        
        # Patch the cache directory function
        self.cache_dir_patcher = patch(
            'aye.model.offline_llm_manager._get_model_cache_dir',
            return_value=self.cache_dir
        )
        self.cache_dir_patcher.start()

    def tearDown(self):
        self.cache_dir_patcher.stop()
        self.tmpdir.cleanup()
        offline_llm_manager._model_status.clear()

    # --- get_model_status tests ---
    
    def test_get_model_status_not_downloaded(self):
        """Test status when model is not downloaded."""
        model_id = "offline/deepseek-coder-6.7b"
        status = offline_llm_manager.get_model_status(model_id)
        self.assertEqual(status, "NOT_DOWNLOADED")

    def test_get_model_status_ready(self):
        """Test status when flag file exists."""
        model_id = "offline/deepseek-coder-6.7b"
        flag_file = offline_llm_manager._get_model_flag_file(model_id)
        flag_file.parent.mkdir(parents=True, exist_ok=True)
        flag_file.touch()
        
        status = offline_llm_manager.get_model_status(model_id)
        self.assertEqual(status, "READY")

    def test_get_model_status_downloading(self):
        """Test status when model is being downloaded."""
        model_id = "offline/qwen2.5-coder-7b"
        offline_llm_manager._set_model_status(model_id, "DOWNLOADING")
        
        status = offline_llm_manager.get_model_status(model_id)
        self.assertEqual(status, "DOWNLOADING")

    def test_get_model_status_failed(self):
        """Test status when download failed."""
        model_id = "offline/deepseek-coder-6.7b"
        offline_llm_manager._set_model_status(model_id, "FAILED")
        
        status = offline_llm_manager.get_model_status(model_id)
        self.assertEqual(status, "FAILED")

    def test_get_model_status_caching(self):
        """Test that status is cached after first check."""
        model_id = "offline/deepseek-coder-6.7b"
        
        # First call checks flag file
        status1 = offline_llm_manager.get_model_status(model_id)
        self.assertEqual(status1, "NOT_DOWNLOADED")
        
        # Create flag file
        flag_file = offline_llm_manager._get_model_flag_file(model_id)
        flag_file.parent.mkdir(parents=True, exist_ok=True)
        flag_file.touch()
        
        # Second call should return cached status (still NOT_DOWNLOADED)
        status2 = offline_llm_manager.get_model_status(model_id)
        self.assertEqual(status2, "NOT_DOWNLOADED")

    def test_get_model_status_thread_safety(self):
        """Test concurrent status checks are thread-safe."""
        model_id = "offline/deepseek-coder-6.7b"
        results = []
        
        def check_status():
            results.append(offline_llm_manager.get_model_status(model_id))
        
        threads = [threading.Thread(target=check_status) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        
        # All results should be the same
        self.assertTrue(all(r == results[0] for r in results))

    # --- _set_model_status tests ---
    
    def test_set_model_status(self):
        """Test setting model status."""
        model_id = "offline/deepseek-coder-6.7b"
        offline_llm_manager._set_model_status(model_id, "DOWNLOADING")
        
        status = offline_llm_manager.get_model_status(model_id)
        self.assertEqual(status, "DOWNLOADING")

    # --- get_model_config tests ---
    
    def test_get_model_config_valid(self):
        """Test getting config for valid offline model."""
        model_id = "offline/deepseek-coder-6.7b"
        config = offline_llm_manager.get_model_config(model_id)
        
        self.assertIsNotNone(config)
        self.assertIn("repo_id", config)
        self.assertIn("filename", config)
        self.assertIn("size_gb", config)
        self.assertIn("context_length", config)
        self.assertEqual(config["context_length"], 16384)

    def test_get_model_config_invalid(self):
        """Test getting config for invalid model."""
        model_id = "offline/nonexistent-model"
        config = offline_llm_manager.get_model_config(model_id)
        
        self.assertIsNone(config)

    # --- get_model_path tests ---
    
    def test_get_model_path_not_downloaded(self):
        """Test getting path for non-downloaded model."""
        model_id = "offline/deepseek-coder-6.7b"
        path = offline_llm_manager.get_model_path(model_id)
        
        self.assertIsNone(path)

    def test_get_model_path_ready(self):
        """Test getting path for downloaded model."""
        model_id = "offline/deepseek-coder-6.7b"
        config = offline_llm_manager.OFFLINE_MODELS[model_id]
        
        # Create flag file and model file
        flag_file = offline_llm_manager._get_model_flag_file(model_id)
        flag_file.parent.mkdir(parents=True, exist_ok=True)
        flag_file.touch()
        
        model_file = self.cache_dir / config["filename"]
        model_file.parent.mkdir(parents=True, exist_ok=True)
        model_file.touch()
        
        path = offline_llm_manager.get_model_path(model_id)
        
        self.assertIsNotNone(path)
        self.assertEqual(path, model_file)
        self.assertTrue(path.exists())

    def test_get_model_path_flag_exists_but_file_missing(self):
        """Test when flag exists but actual model file is missing."""
        model_id = "offline/deepseek-coder-6.7b"
        
        # Create flag file but not model file
        flag_file = offline_llm_manager._get_model_flag_file(model_id)
        flag_file.parent.mkdir(parents=True, exist_ok=True)
        flag_file.touch()
        
        path = offline_llm_manager.get_model_path(model_id)
        
        self.assertIsNone(path)

    def test_get_model_path_invalid_model(self):
        """Test getting path for invalid model ID."""
        model_id = "offline/nonexistent-model"
        path = offline_llm_manager.get_model_path(model_id)
        
        self.assertIsNone(path)

    # --- is_offline_model tests ---
    
    def test_is_offline_model_true(self):
        """Test identifying offline models."""
        self.assertTrue(offline_llm_manager.is_offline_model("offline/deepseek-coder-6.7b"))
        self.assertTrue(offline_llm_manager.is_offline_model("offline/qwen2.5-coder-7b"))
        self.assertTrue(offline_llm_manager.is_offline_model("offline/anything"))

    def test_is_offline_model_false(self):
        """Test identifying non-offline models."""
        self.assertFalse(offline_llm_manager.is_offline_model("openai/gpt-4"))
        self.assertFalse(offline_llm_manager.is_offline_model("anthropic/claude"))
        self.assertFalse(offline_llm_manager.is_offline_model("deepseek-coder-6.7b"))

    # --- download_model_sync tests ---
    
    @patch('aye.model.offline_llm_manager._download_model_with_progress')
    def test_download_model_sync_success(self, mock_download):
        """Test successful model download."""
        model_id = "offline/deepseek-coder-6.7b"
        mock_download.return_value = True
        
        result = offline_llm_manager.download_model_sync(model_id)
        
        self.assertTrue(result)
        self.assertEqual(offline_llm_manager.get_model_status(model_id), "READY")
        mock_download.assert_called_once()

    @patch('aye.model.offline_llm_manager._download_model_with_progress')
    def test_download_model_sync_failure(self, mock_download):
        """Test failed model download."""
        model_id = "offline/deepseek-coder-6.7b"
        mock_download.return_value = False
        
        result = offline_llm_manager.download_model_sync(model_id)
        
        self.assertFalse(result)
        self.assertEqual(offline_llm_manager.get_model_status(model_id), "FAILED")

    @patch('aye.model.offline_llm_manager.rprint')
    def test_download_model_sync_invalid_model(self, mock_rprint):
        """Test download with invalid model ID."""
        model_id = "offline/nonexistent-model"
        
        result = offline_llm_manager.download_model_sync(model_id)
        
        self.assertFalse(result)
        mock_rprint.assert_called_once()
        self.assertIn("Unknown offline model", str(mock_rprint.call_args))

    # --- _download_model_with_progress tests ---
    
    @patch('aye.model.offline_llm_manager.rprint')
    def test_download_model_missing_dependency(self, mock_rprint):
        """Test download when huggingface_hub is not installed."""
        with patch.dict('sys.modules', {'huggingface_hub': None}):
            with patch('builtins.__import__', side_effect=ImportError):
                result = offline_llm_manager._download_model_with_progress(
                    "offline/deepseek-coder-6.7b",
                    "deepseek-ai/DeepSeek-Coder-6.7B-Instruct-GGUF",
                    "deepseek-coder-6.7b-instruct.Q4_K_M.gguf",
                    3.8
                )
                
                self.assertFalse(result)
                # Check that error message was printed
                calls = [str(call) for call in mock_rprint.call_args_list]
                self.assertTrue(any("huggingface_hub is required" in call for call in calls))

    @patch('huggingface_hub.hf_hub_download')
    @patch('aye.model.offline_llm_manager.rprint')
    def test_download_model_already_exists(self, mock_rprint, mock_hf_download):
        """Test download when model file already exists."""
        model_id = "offline/deepseek-coder-6.7b"
        config = offline_llm_manager.OFFLINE_MODELS[model_id]
        
        # Create model file
        model_file = self.cache_dir / config["filename"]
        model_file.parent.mkdir(parents=True, exist_ok=True)
        model_file.touch()
        
        result = offline_llm_manager._download_model_with_progress(
            model_id,
            config["repo_id"],
            config["filename"],
            config["size_gb"]
        )
        
        self.assertTrue(result)
        mock_hf_download.assert_not_called()

    @patch('huggingface_hub.hf_hub_download')
    @patch('aye.model.offline_llm_manager.rprint')
    def test_download_model_with_progress_success(self, mock_rprint, mock_hf_download):
        """Test successful download with progress."""
        model_id = "offline/deepseek-coder-6.7b"
        config = offline_llm_manager.OFFLINE_MODELS[model_id]
        
        mock_hf_download.return_value = str(self.cache_dir / config["filename"])
        
        result = offline_llm_manager._download_model_with_progress(
            model_id,
            config["repo_id"],
            config["filename"],
            config["size_gb"]
        )
        
        self.assertTrue(result)
        mock_hf_download.assert_called_once()
        
        # Check flag file was created
        flag_file = offline_llm_manager._get_model_flag_file(model_id)
        self.assertTrue(flag_file.exists())

    @patch('huggingface_hub.hf_hub_download')
    @patch('aye.model.offline_llm_manager.rprint')
    def test_download_model_http_error(self, mock_rprint, mock_hf_download):
        """Test download with HTTP error."""
        from huggingface_hub.utils import HfHubHTTPError
        
        model_id = "offline/deepseek-coder-6.7b"
        config = offline_llm_manager.OFFLINE_MODELS[model_id]
        
        mock_response = MagicMock()
        mock_response.status_code = 404
        mock_hf_download.side_effect = HfHubHTTPError("Not found", response=mock_response)
        
        result = offline_llm_manager._download_model_with_progress(
            model_id,
            config["repo_id"],
            config["filename"],
            config["size_gb"]
        )
        
        self.assertFalse(result)
        # Check error was printed
        calls = [str(call) for call in mock_rprint.call_args_list]
        self.assertTrue(any("Failed to download model" in call for call in calls))

    @patch('huggingface_hub.hf_hub_download')
    @patch('aye.model.offline_llm_manager.rprint')
    def test_download_model_generic_error(self, mock_rprint, mock_hf_download):
        """Test download with generic error."""
        model_id = "offline/deepseek-coder-6.7b"
        config = offline_llm_manager.OFFLINE_MODELS[model_id]
        
        mock_hf_download.side_effect = Exception("Network error")
        
        result = offline_llm_manager._download_model_with_progress(
            model_id,
            config["repo_id"],
            config["filename"],
            config["size_gb"]
        )
        
        self.assertFalse(result)
        calls = [str(call) for call in mock_rprint.call_args_list]
        self.assertTrue(any("Error downloading model" in call for call in calls))

    # --- _get_model_flag_file tests ---
    
    def test_get_model_flag_file(self):
        """Test flag file path generation."""
        model_id = "offline/deepseek-coder-6.7b"
        flag_file = offline_llm_manager._get_model_flag_file(model_id)
        
        self.assertEqual(flag_file.name, "offline_deepseek-coder-6.7b.downloaded")
        self.assertEqual(flag_file.parent, self.cache_dir)

    def test_get_model_flag_file_special_chars(self):
        """Test flag file with special characters in model ID."""
        model_id = "offline/model-with/slashes"
        flag_file = offline_llm_manager._get_model_flag_file(model_id)
        
        # Slashes should be replaced with underscores
        self.assertEqual(flag_file.name, "offline_model-with_slashes.downloaded")

    # --- Integration tests ---
    
    @patch('aye.model.offline_llm_manager._download_model_with_progress')
    def test_full_download_workflow(self, mock_download):
        """Test complete download workflow."""
        model_id = "offline/deepseek-coder-6.7b"
        mock_download.return_value = True
        
        # Initial status should be NOT_DOWNLOADED
        self.assertEqual(offline_llm_manager.get_model_status(model_id), "NOT_DOWNLOADED")
        
        # Download
        result = offline_llm_manager.download_model_sync(model_id)
        self.assertTrue(result)
        
        # Status should now be READY
        self.assertEqual(offline_llm_manager.get_model_status(model_id), "READY")
        
        # Subsequent checks should remain READY
        self.assertEqual(offline_llm_manager.get_model_status(model_id), "READY")

    def test_offline_models_config(self):
        """Test that OFFLINE_MODELS config is valid."""
        self.assertGreater(len(offline_llm_manager.OFFLINE_MODELS), 0)
        
        for model_id, config in offline_llm_manager.OFFLINE_MODELS.items():
            # Check model ID format
            self.assertTrue(model_id.startswith("offline/"))
            
            # Check required config keys
            self.assertIn("repo_id", config)
            self.assertIn("filename", config)
            self.assertIn("size_gb", config)
            self.assertIn("context_length", config)
            
            # Check types
            self.assertIsInstance(config["repo_id"], str)
            self.assertIsInstance(config["filename"], str)
            self.assertIsInstance(config["size_gb"], (int, float))
            self.assertIsInstance(config["context_length"], int)
            
            # Check reasonable values
            self.assertGreater(config["size_gb"], 0)
            self.assertGreater(config["context_length"], 0)


if __name__ == '__main__':
    unittest.main()
