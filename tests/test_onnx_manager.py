import os
import threading
import types
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import MagicMock, patch

import aye.model.onnx_manager as onnx_manager


class TestOnnxManager(unittest.TestCase):
    def setUp(self):
        # Isolate filesystem side-effects: never use the real cache/home flag file.
        self._tmp = TemporaryDirectory()
        self.tmpdir = Path(self._tmp.name)
        self.flag_file = self.tmpdir / "onnx_model.downloaded"

        # Patch the module-global flag file path
        self._flag_patcher = patch.object(onnx_manager, "_model_flag_file", self.flag_file)
        self._flag_patcher.start()

        # Reset global state
        onnx_manager._status = "NOT_CHECKED"

        # Ensure flag file doesn't exist
        if self.flag_file.exists():
            self.flag_file.unlink()

    def tearDown(self):
        self._flag_patcher.stop()
        self._tmp.cleanup()

    def test_get_model_status_initial(self):
        self.assertEqual(onnx_manager.get_model_status(), "NOT_DOWNLOADED")

    def test_get_model_status_ready_when_flag_exists(self):
        self.flag_file.parent.mkdir(parents=True, exist_ok=True)
        self.flag_file.touch()
        onnx_manager._status = "NOT_CHECKED"

        self.assertEqual(onnx_manager.get_model_status(), "READY")

    def test_get_model_status_failed(self):
        onnx_manager._status = "FAILED"
        self.assertEqual(onnx_manager.get_model_status(), "FAILED")

    def test_get_model_status_downloading_cached(self):
        onnx_manager._status = "DOWNLOADING"
        self.assertEqual(onnx_manager.get_model_status(), "DOWNLOADING")
        self.assertEqual(onnx_manager.get_model_status(), "DOWNLOADING")

    def test_get_model_status_is_cached_even_if_flag_appears_later(self):
        # First call caches NOT_DOWNLOADED
        self.assertEqual(onnx_manager.get_model_status(), "NOT_DOWNLOADED")

        # Create flag file after status has been cached
        self.flag_file.parent.mkdir(parents=True, exist_ok=True)
        self.flag_file.touch()

        # Still cached
        self.assertEqual(onnx_manager.get_model_status(), "NOT_DOWNLOADED")

        # Reset cache -> should re-check and become READY
        onnx_manager._status = "NOT_CHECKED"
        self.assertEqual(onnx_manager.get_model_status(), "READY")

    def test_get_model_status_thread_safety(self):
        # Concurrent calls should not race to different values.
        results = []

        def call_get_status():
            results.append(onnx_manager.get_model_status())

        threads = [threading.Thread(target=call_get_status) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertTrue(results)
        self.assertTrue(all(r == results[0] for r in results))

    def test_get_model_flag_file_uses_chroma_cache_dir_env(self):
        with TemporaryDirectory() as td:
            with patch.dict(os.environ, {"CHROMA_CACHE_DIR": td}):
                p = onnx_manager._get_model_flag_file()

        self.assertEqual(p, Path(td) / "onnx_model.downloaded")

    def test_get_model_flag_file_fallback_when_getenv_raises(self):
        # Forces exception path in _get_model_flag_file() try-block.
        with patch("aye.model.onnx_manager.os.getenv", side_effect=Exception("boom")):
            p = onnx_manager._get_model_flag_file()

        self.assertTrue(str(p).endswith(str(Path(".aye") / "onnx_model.downloaded")))

    def test_download_model_sync_success_sets_ready_and_creates_flag(self):
        # Ensure imports inside _download_model_sync are satisfied even if environment is odd.
        dummy_vector_db = types.ModuleType("aye.model.vector_db")
        dummy_vector_db.suppress_stdout_stderr = MagicMock()

        def fake_download():
            # Should have transitioned to DOWNLOADING before starting the work.
            self.assertEqual(onnx_manager.get_model_status(), "DOWNLOADING")

        with patch.dict("sys.modules", {"aye.model.vector_db": dummy_vector_db}), patch(
            "chromadb.utils.embedding_functions.ONNXMiniLM_L6_V2", new=object()
        ), patch("aye.model.onnx_manager.download_onnx", side_effect=fake_download):
            onnx_manager._download_model_sync()

        self.assertTrue(self.flag_file.exists())
        self.assertEqual(onnx_manager.get_model_status(), "READY")

    def test_download_model_sync_failure_sets_failed_and_no_flag(self):
        dummy_vector_db = types.ModuleType("aye.model.vector_db")
        dummy_vector_db.suppress_stdout_stderr = MagicMock()

        with patch.dict("sys.modules", {"aye.model.vector_db": dummy_vector_db}), patch(
            "chromadb.utils.embedding_functions.ONNXMiniLM_L6_V2", new=object()
        ), patch("aye.model.onnx_manager.download_onnx", side_effect=RuntimeError("fail")):
            onnx_manager._download_model_sync()

        self.assertFalse(self.flag_file.exists())
        self.assertEqual(onnx_manager.get_model_status(), "FAILED")

    def test_download_model_if_needed_background_starts_daemon_thread(self):
        # Ensure model is considered missing
        onnx_manager._status = "NOT_CHECKED"
        if self.flag_file.exists():
            self.flag_file.unlink()

        created = {}

        class FakeThread:
            def __init__(self, target=None, daemon=None):
                created["target"] = target
                created["daemon"] = daemon
                self.started = False

            def start(self):
                self.started = True
                created["started"] = True

        with patch("aye.model.onnx_manager.threading.Thread", FakeThread):
            onnx_manager.download_model_if_needed(background=True)

        self.assertIs(created.get("target"), onnx_manager._download_model_sync)
        self.assertTrue(created.get("daemon"))
        self.assertTrue(created.get("started"))

    def test_download_model_if_needed_foreground_calls_sync(self):
        onnx_manager._status = "NOT_CHECKED"
        if self.flag_file.exists():
            self.flag_file.unlink()

        with patch("aye.model.onnx_manager._download_model_sync") as mock_sync:
            onnx_manager.download_model_if_needed(background=False)

        mock_sync.assert_called_once()

    def test_download_model_if_needed_noop_when_ready(self):
        onnx_manager._status = "READY"

        with patch("aye.model.onnx_manager._download_model_sync") as mock_sync, patch(
            "aye.model.onnx_manager.threading.Thread"
        ) as mock_thread:
            onnx_manager.download_model_if_needed(background=True)

        mock_thread.assert_not_called()
        mock_sync.assert_not_called()


if __name__ == "__main__":
    unittest.main()
