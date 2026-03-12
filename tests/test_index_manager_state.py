import threading
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

import aye.model.index_manager.index_manager_state as state


def test_index_config_properties_and_from_params(monkeypatch, tmp_path):
    # Patch MAX_WORKERS import used inside from_params
    monkeypatch.setattr(
        "aye.model.index_manager.index_manager_utils.MAX_WORKERS",
        3,
        raising=True,
    )

    cfg = state.IndexConfig.from_params(
        root_path=tmp_path,
        file_mask="*.py",
        verbose=True,
        debug=False,
    )

    assert cfg.root_path == tmp_path
    assert cfg.file_mask == "*.py"
    assert cfg.verbose is True
    assert cfg.debug is False
    assert cfg.max_workers == 3

    assert cfg.index_dir == tmp_path / ".aye"
    assert cfg.hash_index_path == tmp_path / ".aye" / "file_index.json"


def test_index_config_chroma_db_path(tmp_path):
    cfg = state.IndexConfig(root_path=tmp_path, file_mask="*.py")
    assert cfg.chroma_db_path == tmp_path / ".aye" / "chroma_db"


def test_safe_state_update_get_update_many_get_many_increment():
    ss = state.SafeState()

    assert ss.get("missing") is None
    assert ss.get("missing", 123) == 123

    ss.update("a", 1)
    assert ss.get("a") == 1

    ss.update_many({"b": 2, "c": 3})
    assert ss.get_many(["a", "b", "c", "d"]) == {"a": 1, "b": 2, "c": 3, "d": None}

    assert ss.increment("counter") == 1
    assert ss.increment("counter", amount=5) == 6
    assert ss.get("counter") == 6


def test_indexing_state_progress_generation_and_work_flags():
    st = state.IndexingState()

    assert st.has_work() is False
    assert st.is_active() is False

    st.reset_coarse_progress(total=10)
    assert (st.coarse_total, st.coarse_processed) == (10, 0)

    st.reset_refine_progress(total=7)
    assert (st.refine_total, st.refine_processed) == (7, 0)

    st.discovery_total = 5
    st.discovery_processed = 2
    st.reset_discovery_progress()
    assert (st.discovery_total, st.discovery_processed) == (0, 0)

    assert st.increment_generation() == 1
    assert st.increment_generation() == 2

    st.files_to_coarse_index.append("a.py")
    assert st.has_work() is True

    st.is_discovering = True
    assert st.is_active() is True

    st.files_to_refine.append("b.py")
    st.target_index["b.py"] = {"hash": "x"}
    st.clear_work_queues()
    assert st.files_to_coarse_index == []
    assert st.files_to_refine == []
    assert st.target_index == {}


def test_progress_tracker_display_for_each_phase_and_inactive():
    pt = state.ProgressTracker()

    assert pt.is_active() is False
    assert pt.get_display() == ""

    pt.set_active("discovery")
    assert pt.is_active() is True
    assert pt.get_display() == "discovering files..."

    pt.set_total("discovery", 3)
    pt.increment("discovery")
    assert pt.get_progress("discovery") == (1, 3)
    assert pt.get_display() == "discovering files 1/3"

    pt.set_active("coarse")
    pt.set_total("coarse", 2)
    pt.increment("coarse")
    assert pt.get_display() == "indexing 1/2"

    pt.set_active("refine")
    pt.set_total("refine", 4)
    pt.increment("refine")
    pt.increment("refine")
    assert pt.get_display() == "refining 2/4"

    pt.set_active(None)
    assert pt.get_display() == ""


def test_progress_tracker_get_display_returns_indexing_when_lock_unavailable():
    pt = state.ProgressTracker()
    pt.set_active("coarse")
    pt.set_total("coarse", 1)

    # Force lock acquisition failure: hold the lock from this test.
    pt._lock.acquire()
    try:
        assert pt.get_display() == "indexing..."
    finally:
        pt._lock.release()


# =============================================================================
# Tests for _is_corruption_error
# =============================================================================

class TestIsCorruptionError:
    """Tests for the _is_corruption_error helper function."""

    def test_returns_false_for_generic_error(self):
        err = RuntimeError("something went wrong")
        assert state._is_corruption_error(err) is False

    def test_returns_false_for_value_error(self):
        err = ValueError("invalid value")
        assert state._is_corruption_error(err) is False

    @pytest.mark.parametrize("message", [
        "database disk image is malformed",
        "file is not a database",
        "no such table: embeddings",
        "database is locked",
        "unable to open database file",
        "corrupt header",
        "The database file is corrupt",
    ])
    def test_returns_true_for_corruption_message_indicators(self, message):
        err = Exception(message)
        assert state._is_corruption_error(err) is True

    def test_returns_true_for_corruption_message_case_insensitive(self):
        err = Exception("DATABASE DISK IMAGE IS MALFORMED")
        assert state._is_corruption_error(err) is True

    def test_returns_true_for_operational_error_type(self):
        # Create a custom exception class with the name OperationalError
        class OperationalError(Exception):
            pass
        err = OperationalError("some error")
        assert state._is_corruption_error(err) is True

    def test_returns_true_for_database_error_type(self):
        class DatabaseError(Exception):
            pass
        err = DatabaseError("connection failed")
        assert state._is_corruption_error(err) is True

    def test_returns_true_for_integrity_error_type(self):
        class IntegrityError(Exception):
            pass
        err = IntegrityError("constraint violation")
        assert state._is_corruption_error(err) is True

    def test_returns_true_for_sqlite_in_message(self):
        err = Exception("sqlite3.OperationalError: disk I/O error")
        assert state._is_corruption_error(err) is True

    def test_returns_true_for_sqlite_in_type_name(self):
        class Sqlite3Error(Exception):
            pass
        err = Sqlite3Error("something")
        assert state._is_corruption_error(err) is True

    def test_returns_true_for_operationalerror_in_message(self):
        err = RuntimeError("got OperationalError from database")
        assert state._is_corruption_error(err) is True


# =============================================================================
# Tests for InitializationCoordinator recovery logic
# =============================================================================

def test_initialization_coordinator_ready_path_success(monkeypatch, tmp_path):
    cfg = state.IndexConfig(root_path=tmp_path, file_mask="*.py", debug=True)
    coord = state.InitializationCoordinator(cfg)

    # Patch aye.model attributes that the coordinator imports.
    import aye.model as aye_model

    fake_onnx = SimpleNamespace(get_model_status=lambda: "READY")
    fake_vector = SimpleNamespace(initialize_index=lambda root: "COLLECTION")

    monkeypatch.setattr(aye_model, "onnx_manager", fake_onnx, raising=False)
    monkeypatch.setattr(aye_model, "vector_db", fake_vector, raising=False)

    rprint = MagicMock()
    monkeypatch.setattr(state, "rprint", rprint, raising=True)

    ok = coord.initialize(blocking=True)

    assert ok is True
    assert coord.is_initialized is True
    assert coord.in_progress is False
    assert coord.collection == "COLLECTION"
    assert coord.is_ready is True

    # Debug message printed when debug=True
    rprint.assert_any_call("[bold cyan]Code lookup is now active.[/]")


def test_initialization_coordinator_ready_path_failure_sets_initialized(monkeypatch, tmp_path):
    cfg = state.IndexConfig(root_path=tmp_path, file_mask="*.py", debug=False)
    coord = state.InitializationCoordinator(cfg)

    import aye.model as aye_model

    fake_onnx = SimpleNamespace(get_model_status=lambda: "READY")

    def boom(_root):
        raise RuntimeError("db down")

    fake_vector = SimpleNamespace(initialize_index=boom)

    monkeypatch.setattr(aye_model, "onnx_manager", fake_onnx, raising=False)
    monkeypatch.setattr(aye_model, "vector_db", fake_vector, raising=False)

    rprint = MagicMock()
    monkeypatch.setattr(state, "rprint", rprint, raising=True)

    ok = coord.initialize(blocking=True)

    assert ok is False
    assert coord.is_initialized is True
    assert coord.collection is None
    assert coord.is_ready is False

    assert any(
        "Failed to initialize local code search" in str(c.args[0])
        for c in rprint.call_args_list
    )


def test_initialization_coordinator_failed_model_status(monkeypatch, tmp_path):
    cfg = state.IndexConfig(root_path=tmp_path, file_mask="*.py")
    coord = state.InitializationCoordinator(cfg)

    import aye.model as aye_model

    fake_onnx = SimpleNamespace(get_model_status=lambda: "FAILED")
    fake_vector = SimpleNamespace(initialize_index=lambda root: "COLLECTION")

    monkeypatch.setattr(aye_model, "onnx_manager", fake_onnx, raising=False)
    monkeypatch.setattr(aye_model, "vector_db", fake_vector, raising=False)

    ok = coord.initialize(blocking=True)

    assert ok is False
    assert coord.is_initialized is True
    assert coord.collection is None
    assert coord.is_ready is False


def test_initialization_coordinator_lock_not_acquired_nonblocking(monkeypatch, tmp_path):
    cfg = state.IndexConfig(root_path=tmp_path, file_mask="*.py")
    coord = state.InitializationCoordinator(cfg)

    # Hold the coordinator lock, then call initialize(blocking=False)
    coord._lock.acquire()
    try:
        ok = coord.initialize(blocking=False)
        assert ok is False
        assert coord.in_progress is False
        assert coord.is_initialized is False
        assert coord.collection is None
    finally:
        coord._lock.release()


class TestInitializationCoordinatorRecovery:
    """Tests for corruption detection and recovery in InitializationCoordinator."""

    def test_corruption_triggers_recovery_and_succeeds(self, monkeypatch, tmp_path):
        """When corruption is detected, recovery is attempted and succeeds."""
        cfg = state.IndexConfig(root_path=tmp_path, file_mask="*.py")
        coord = state.InitializationCoordinator(cfg)

        # Create the .aye directory structure
        aye_dir = tmp_path / ".aye"
        aye_dir.mkdir()
        chroma_dir = aye_dir / "chroma_db"
        chroma_dir.mkdir()
        (chroma_dir / "data.db").write_text("corrupt")
        hash_index = aye_dir / "file_index.json"
        hash_index.write_text('{"files": {}}')

        import aye.model as aye_model

        fake_onnx = SimpleNamespace(get_model_status=lambda: "READY")

        call_count = [0]

        def init_index_with_corruption(_root):
            call_count[0] += 1
            if call_count[0] == 1:
                raise Exception("database disk image is malformed")
            return "RECOVERED_COLLECTION"

        fake_vector = SimpleNamespace(initialize_index=init_index_with_corruption)

        monkeypatch.setattr(aye_model, "onnx_manager", fake_onnx, raising=False)
        monkeypatch.setattr(aye_model, "vector_db", fake_vector, raising=False)

        rprint = MagicMock()
        monkeypatch.setattr(state, "rprint", rprint, raising=True)

        ok = coord.initialize(blocking=True)

        assert ok is True
        assert coord.is_initialized is True
        assert coord.collection == "RECOVERED_COLLECTION"
        assert coord._recovery_attempted is True

        # Original chroma_db should be quarantined
        assert not chroma_dir.exists()
        quarantined = [p for p in aye_dir.iterdir() if "chroma_db.corrupt" in p.name]
        assert len(quarantined) == 1

        # Hash index should be quarantined
        assert not hash_index.exists()
        quarantined_index = [p for p in aye_dir.iterdir() if "file_index.json.corrupt" in p.name]
        assert len(quarantined_index) == 1

        # Check recovery messages
        messages = [str(c.args[0]) for c in rprint.call_args_list]
        assert any("Detected possible index corruption" in m for m in messages)
        assert any("Attempting automatic recovery" in m for m in messages)
        assert any("Recovery successful" in m for m in messages)

    def test_corruption_triggers_recovery_and_fails(self, monkeypatch, tmp_path):
        """When corruption is detected and recovery also fails."""
        cfg = state.IndexConfig(root_path=tmp_path, file_mask="*.py")
        coord = state.InitializationCoordinator(cfg)

        # Create the .aye directory structure
        aye_dir = tmp_path / ".aye"
        aye_dir.mkdir()
        chroma_dir = aye_dir / "chroma_db"
        chroma_dir.mkdir()

        import aye.model as aye_model

        fake_onnx = SimpleNamespace(get_model_status=lambda: "READY")

        def always_corrupt(_root):
            raise Exception("database disk image is malformed")

        fake_vector = SimpleNamespace(initialize_index=always_corrupt)

        monkeypatch.setattr(aye_model, "onnx_manager", fake_onnx, raising=False)
        monkeypatch.setattr(aye_model, "vector_db", fake_vector, raising=False)

        rprint = MagicMock()
        monkeypatch.setattr(state, "rprint", rprint, raising=True)

        ok = coord.initialize(blocking=True)

        assert ok is False
        assert coord.is_initialized is True
        assert coord.collection is None
        assert coord._recovery_attempted is True

        # Check failure messages
        messages = [str(c.args[0]) for c in rprint.call_args_list]
        assert any("Recovery failed" in m for m in messages)
        assert any("Code search will be disabled" in m for m in messages)

    def test_recovery_not_attempted_twice(self, monkeypatch, tmp_path):
        """If recovery was already attempted, don't try again."""
        cfg = state.IndexConfig(root_path=tmp_path, file_mask="*.py")
        coord = state.InitializationCoordinator(cfg)
        coord._recovery_attempted = True  # Simulate previous recovery attempt

        import aye.model as aye_model

        fake_onnx = SimpleNamespace(get_model_status=lambda: "READY")

        def raise_corruption(_root):
            raise Exception("database disk image is malformed")

        fake_vector = SimpleNamespace(initialize_index=raise_corruption)

        monkeypatch.setattr(aye_model, "onnx_manager", fake_onnx, raising=False)
        monkeypatch.setattr(aye_model, "vector_db", fake_vector, raising=False)

        rprint = MagicMock()
        monkeypatch.setattr(state, "rprint", rprint, raising=True)

        ok = coord.initialize(blocking=True)

        assert ok is False
        assert coord.is_initialized is True
        assert coord.collection is None

        # Should not see recovery attempt messages
        messages = [str(c.args[0]) for c in rprint.call_args_list]
        assert not any("Attempting automatic recovery" in m for m in messages)
        assert any("Failed to initialize local code search" in m for m in messages)

    def test_non_corruption_error_does_not_trigger_recovery(self, monkeypatch, tmp_path):
        """Generic errors should not trigger recovery."""
        cfg = state.IndexConfig(root_path=tmp_path, file_mask="*.py")
        coord = state.InitializationCoordinator(cfg)

        import aye.model as aye_model

        fake_onnx = SimpleNamespace(get_model_status=lambda: "READY")

        def raise_generic(_root):
            raise RuntimeError("network timeout")

        fake_vector = SimpleNamespace(initialize_index=raise_generic)

        monkeypatch.setattr(aye_model, "onnx_manager", fake_onnx, raising=False)
        monkeypatch.setattr(aye_model, "vector_db", fake_vector, raising=False)

        rprint = MagicMock()
        monkeypatch.setattr(state, "rprint", rprint, raising=True)

        ok = coord.initialize(blocking=True)

        assert ok is False
        assert coord._recovery_attempted is False

        messages = [str(c.args[0]) for c in rprint.call_args_list]
        assert not any("Attempting automatic recovery" in m for m in messages)

    def test_recovery_handles_move_failure_with_delete(self, monkeypatch, tmp_path):
        """If quarantine move fails, fallback to delete."""
        cfg = state.IndexConfig(root_path=tmp_path, file_mask="*.py")
        coord = state.InitializationCoordinator(cfg)

        # Create the .aye directory structure
        aye_dir = tmp_path / ".aye"
        aye_dir.mkdir()
        chroma_dir = aye_dir / "chroma_db"
        chroma_dir.mkdir()
        (chroma_dir / "data.db").write_text("corrupt")

        import aye.model as aye_model
        import shutil as real_shutil

        fake_onnx = SimpleNamespace(get_model_status=lambda: "READY")

        call_count = [0]

        def init_index_with_corruption(_root):
            call_count[0] += 1
            if call_count[0] == 1:
                raise Exception("database disk image is malformed")
            return "RECOVERED_COLLECTION"

        fake_vector = SimpleNamespace(initialize_index=init_index_with_corruption)

        monkeypatch.setattr(aye_model, "onnx_manager", fake_onnx, raising=False)
        monkeypatch.setattr(aye_model, "vector_db", fake_vector, raising=False)

        # Make shutil.move fail
        original_move = real_shutil.move
        original_rmtree = real_shutil.rmtree

        def failing_move(src, dst):
            raise OSError("Permission denied")

        monkeypatch.setattr(state.shutil, "move", failing_move)
        monkeypatch.setattr(state.shutil, "rmtree", original_rmtree)

        rprint = MagicMock()
        monkeypatch.setattr(state, "rprint", rprint, raising=True)

        ok = coord.initialize(blocking=True)

        assert ok is True
        assert coord.collection == "RECOVERED_COLLECTION"

        # Chroma dir should be deleted (rmtree fallback)
        assert not chroma_dir.exists()

        messages = [str(c.args[0]) for c in rprint.call_args_list]
        assert any("Could not quarantine DB" in m for m in messages)

    def test_recovery_with_no_existing_chroma_dir(self, monkeypatch, tmp_path):
        """Recovery handles case where chroma_db doesn't exist."""
        cfg = state.IndexConfig(root_path=tmp_path, file_mask="*.py")
        coord = state.InitializationCoordinator(cfg)

        # Create .aye but NOT chroma_db
        aye_dir = tmp_path / ".aye"
        aye_dir.mkdir()

        import aye.model as aye_model

        fake_onnx = SimpleNamespace(get_model_status=lambda: "READY")

        call_count = [0]

        def init_index_with_corruption(_root):
            call_count[0] += 1
            if call_count[0] == 1:
                raise Exception("no such table: embeddings")
            return "RECOVERED_COLLECTION"

        fake_vector = SimpleNamespace(initialize_index=init_index_with_corruption)

        monkeypatch.setattr(aye_model, "onnx_manager", fake_onnx, raising=False)
        monkeypatch.setattr(aye_model, "vector_db", fake_vector, raising=False)

        rprint = MagicMock()
        monkeypatch.setattr(state, "rprint", rprint, raising=True)

        ok = coord.initialize(blocking=True)

        assert ok is True
        assert coord.collection == "RECOVERED_COLLECTION"

    def test_recovery_with_no_existing_hash_index(self, monkeypatch, tmp_path):
        """Recovery handles case where hash index doesn't exist."""
        cfg = state.IndexConfig(root_path=tmp_path, file_mask="*.py")
        coord = state.InitializationCoordinator(cfg)

        # Create .aye and chroma_db but NOT file_index.json
        aye_dir = tmp_path / ".aye"
        aye_dir.mkdir()
        chroma_dir = aye_dir / "chroma_db"
        chroma_dir.mkdir()

        import aye.model as aye_model

        fake_onnx = SimpleNamespace(get_model_status=lambda: "READY")

        call_count = [0]

        def init_index_with_corruption(_root):
            call_count[0] += 1
            if call_count[0] == 1:
                raise Exception("database is locked")
            return "RECOVERED_COLLECTION"

        fake_vector = SimpleNamespace(initialize_index=init_index_with_corruption)

        monkeypatch.setattr(aye_model, "onnx_manager", fake_onnx, raising=False)
        monkeypatch.setattr(aye_model, "vector_db", fake_vector, raising=False)

        rprint = MagicMock()
        monkeypatch.setattr(state, "rprint", rprint, raising=True)

        ok = coord.initialize(blocking=True)

        assert ok is True
        assert coord.collection == "RECOVERED_COLLECTION"


def test_error_handler_debug_and_verbose_output(monkeypatch):
    rprint = MagicMock()
    monkeypatch.setattr(state, "rprint", rprint, raising=True)

    eh = state.ErrorHandler(verbose=False, debug=False)
    eh.handle(RuntimeError("x"), context="ctx")
    eh.warn("warn")
    eh.info("info")
    assert rprint.call_count == 0

    eh = state.ErrorHandler(verbose=False, debug=True)
    eh.handle(RuntimeError("x"), context="ctx")
    eh.handle(RuntimeError("y"))
    eh.warn("warn")
    eh.info("info")

    # In debug mode, handle/warn/info should all print.
    assert any("Error in ctx" in str(c.args[0]) for c in rprint.call_args_list)
    assert any("[red]Error: y" in str(c.args[0]) for c in rprint.call_args_list)
    assert any("[yellow]warn" in str(c.args[0]) for c in rprint.call_args_list)
    assert any("[cyan]info" in str(c.args[0]) for c in rprint.call_args_list)

    rprint.reset_mock()

    eh = state.ErrorHandler(verbose=True, debug=False)
    eh.warn("warn")
    eh.info("info")

    # Verbose prints warn but not info.
    rprint.assert_called_once_with("[yellow]warn[/yellow]")


def test_error_handler_handle_silent(monkeypatch):
    rprint = MagicMock()
    monkeypatch.setattr(state, "rprint", rprint, raising=True)

    # Silent handler should not print in non-debug mode
    eh = state.ErrorHandler(verbose=True, debug=False)
    eh.handle_silent(RuntimeError("silent error"), context="ctx")
    assert rprint.call_count == 0

    # But should print in debug mode
    eh = state.ErrorHandler(verbose=False, debug=True)
    eh.handle_silent(RuntimeError("silent error"), context="ctx")
    assert rprint.call_count == 1
    assert "Error in ctx" in str(rprint.call_args_list[0].args[0])
