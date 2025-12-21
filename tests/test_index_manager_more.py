from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

import aye.model.index_manager.index_manager as index_manager


def _patch_coordinator_properties(monkeypatch, coord, *, state: dict):
    """Patch read-only InitializationCoordinator properties to read from `state`."""

    # These are properties on InitializationCoordinator (no setters).
    # We patch them at the class level for the duration of each test.
    monkeypatch.setattr(
        type(coord),
        "is_initialized",
        property(lambda self: state.get("is_initialized", False)),
        raising=False,
    )
    monkeypatch.setattr(
        type(coord),
        "in_progress",
        property(lambda self: state.get("in_progress", False)),
        raising=False,
    )
    monkeypatch.setattr(
        type(coord),
        "collection",
        property(lambda self: state.get("collection", None)),
        raising=False,
    )
    monkeypatch.setattr(
        type(coord),
        "is_ready",
        property(lambda self: state.get("is_ready", False)),
        raising=False,
    )


@pytest.fixture()
def manager(tmp_path, monkeypatch):
    # Avoid registering global cleanup handlers during tests
    monkeypatch.setattr(index_manager, "register_manager", lambda *_args, **_kwargs: None, raising=True)
    m = index_manager.IndexManager(tmp_path, "*.py", verbose=False, debug=False)
    return m


def test_shutdown_is_idempotent(manager, monkeypatch):
    save = MagicMock()
    wait = MagicMock()
    monkeypatch.setattr(manager, "_save_progress", save, raising=True)
    monkeypatch.setattr(manager, "_wait_for_background_work", wait, raising=True)

    manager.shutdown()
    manager.shutdown()  # should no-op second time

    assert manager._state.shutdown_requested is True
    assert save.call_count == 1
    assert wait.call_count == 1


def test_wait_for_background_work_loops_until_inactive_or_timeout(manager, monkeypatch):
    # Simulate: active twice, then inactive.
    active = {"n": 0}

    def fake_is_active():
        active["n"] += 1
        return active["n"] < 3

    monkeypatch.setattr(manager._state, "is_active", fake_is_active, raising=True)

    slept = {"n": 0}

    def fake_sleep(_):
        slept["n"] += 1

    monkeypatch.setattr(index_manager.time, "sleep", fake_sleep, raising=True)

    # Make time progress so the loop can evaluate deadline without real waiting.
    t = {"now": 1000.0}

    def fake_time():
        # advance a bit each call
        t["now"] += 0.1
        return t["now"]

    monkeypatch.setattr(index_manager.time, "time", fake_time, raising=True)

    manager._wait_for_background_work(timeout=1.0)

    assert slept["n"] >= 1


def test_prepare_sync_skips_home_directory_verbose_prints(tmp_path, monkeypatch):
    monkeypatch.setattr(index_manager, "register_manager", lambda *_args, **_kwargs: None, raising=True)
    m = index_manager.IndexManager(tmp_path, "*.py", verbose=False, debug=False)

    # Force "home directory" to be the manager root.
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path), raising=True)

    rprint = MagicMock()
    monkeypatch.setattr(index_manager, "rprint", rprint, raising=True)

    m.prepare_sync(verbose=True)

    rprint.assert_called_once_with("[yellow]Skipping indexing in home directory.[/]")


def test_try_initialize_prints_downloading_message_when_verbose_and_not_initialized(manager, monkeypatch):
    coord_state = {"is_initialized": False}
    _patch_coordinator_properties(monkeypatch, manager._init_coordinator, state=coord_state)

    # initialize(blocking=False) called, but does not finish
    init = MagicMock(return_value=False)
    monkeypatch.setattr(manager._init_coordinator, "initialize", init, raising=True)

    monkeypatch.setattr(index_manager.onnx_manager, "get_model_status", lambda: "DOWNLOADING", raising=True)

    rprint = MagicMock()
    monkeypatch.setattr(index_manager, "rprint", rprint, raising=True)

    manager._try_initialize(verbose=True)

    init.assert_called_once_with(blocking=False)
    rprint.assert_called_once()
    assert "downloading models" in str(rprint.call_args[0][0])


def test_handle_deleted_files_no_collection_noop(manager, monkeypatch):
    coord_state = {"collection": None}
    _patch_coordinator_properties(monkeypatch, manager._init_coordinator, state=coord_state)

    delete = MagicMock()
    monkeypatch.setattr(index_manager.vector_db, "delete_from_index", delete, raising=True)

    manager._handle_deleted_files({"a.py"}, {"b.py": {}})

    delete.assert_not_called()


def test_handle_deleted_files_with_collection_deletes(manager, monkeypatch):
    coord_state = {"collection": object(), "is_ready": True}
    _patch_coordinator_properties(monkeypatch, manager._init_coordinator, state=coord_state)

    monkeypatch.setattr(index_manager, "get_deleted_files", lambda current, old: ["gone.py"], raising=True)
    delete = MagicMock()
    monkeypatch.setattr(index_manager.vector_db, "delete_from_index", delete, raising=True)

    info = MagicMock()
    manager._error_handler.info = info

    manager._handle_deleted_files({"keep.py"}, {"keep.py": {}, "gone.py": {}})

    info.assert_called_once()
    delete.assert_called_once_with(coord_state["collection"], ["gone.py"])


def test_update_state_after_categorization_no_work_logs_up_to_date(manager):
    info = MagicMock()
    manager._error_handler.info = info

    manager._update_state_after_categorization(
        files_to_coarse=[],
        files_to_refine=[],
        new_index={"a.py": {"hash": "h"}},
        old_index={"b.py": {"hash": "x"}},
    )

    assert manager._state.target_index == {"a.py": {"hash": "h"}}
    assert manager._state.current_index_on_disk == {"b.py": {"hash": "x"}}
    info.assert_called_once_with("Project index is up-to-date.")


def test_start_indexing_if_needed_spawns_thread(manager, monkeypatch):
    manager._state.files_to_coarse_index = ["a.py"]
    monkeypatch.setattr(manager, "_should_stop", lambda: False, raising=True)

    th = MagicMock()
    thread_ctor = MagicMock(return_value=th)
    monkeypatch.setattr(index_manager.threading, "Thread", thread_ctor, raising=True)

    manager._start_indexing_if_needed()

    thread_ctor.assert_called_once()
    assert th.start.called


def test_wait_for_initialization_success(manager, monkeypatch):
    coord_state = {"is_initialized": False, "collection": None}
    _patch_coordinator_properties(monkeypatch, manager._init_coordinator, state=coord_state)

    calls = {"n": 0}

    def init(blocking=True):
        calls["n"] += 1
        if calls["n"] >= 2:
            coord_state["is_initialized"] = True
            coord_state["collection"] = object()
            return True
        return False

    monkeypatch.setattr(manager._init_coordinator, "initialize", init, raising=True)
    monkeypatch.setattr(index_manager.onnx_manager, "get_model_status", lambda: "READY", raising=True)
    monkeypatch.setattr(index_manager.time, "sleep", lambda *_: None, raising=True)

    assert manager._wait_for_initialization() is True
    assert calls["n"] >= 2


def test_wait_for_initialization_returns_false_when_model_failed(manager, monkeypatch):
    coord_state = {"is_initialized": False, "collection": None}
    _patch_coordinator_properties(monkeypatch, manager._init_coordinator, state=coord_state)

    monkeypatch.setattr(manager._init_coordinator, "initialize", lambda blocking=True: False, raising=True)
    monkeypatch.setattr(index_manager.onnx_manager, "get_model_status", lambda: "FAILED", raising=True)
    monkeypatch.setattr(index_manager.time, "sleep", lambda *_: None, raising=True)

    assert manager._wait_for_initialization() is False


def test_wait_for_discovery_returns_false_when_stop_requested(manager, monkeypatch):
    manager._state.is_discovering = True
    manager._state.files_to_coarse_index = ["a.py"]

    monkeypatch.setattr(manager, "_should_stop", lambda: True, raising=True)
    monkeypatch.setattr(index_manager.time, "sleep", lambda *_: None, raising=True)

    assert manager._wait_for_discovery() is False


def test_execute_coarse_phase_sets_and_unsets_flag(manager, monkeypatch):
    manager._state.files_to_coarse_index = ["a.py"]
    monkeypatch.setattr(manager, "_should_stop", lambda: False, raising=True)

    executor = MagicMock()
    monkeypatch.setattr(manager, "_create_phase_executor", lambda: executor, raising=True)

    assert manager._state.is_indexing is False
    manager._execute_coarse_phase(generation=123)
    assert manager._state.is_indexing is False
    executor.execute_coarse_phase.assert_called_once_with(["a.py"], 123)


def test_execute_refinement_phase_dedupes_sorts_and_sets_progress(manager, monkeypatch):
    manager._state.files_to_refine = ["b.py", "a.py"]
    manager._state.files_to_coarse_index = ["a.py", "c.py"]

    monkeypatch.setattr(manager, "_should_stop", lambda: False, raising=True)

    reset_refine = MagicMock()
    manager._state.reset_refine_progress = reset_refine

    executor = MagicMock()
    monkeypatch.setattr(manager, "_create_phase_executor", lambda: executor, raising=True)

    manager._execute_refinement_phase(generation=9)

    reset_refine.assert_called_once_with(3)
    executor.execute_refine_phase.assert_called_once_with(["a.py", "b.py", "c.py"], 9)
    assert manager._state.is_refining is False


def test_should_continue_to_refinement_generation_mismatch(manager, monkeypatch):
    manager._state.generation = 2
    monkeypatch.setattr(manager, "_should_stop", lambda: False, raising=True)

    assert manager._should_continue_to_refinement(generation=1) is False


def test_finalize_indexing_clears_queues_only_when_generation_matches(manager, monkeypatch):
    save = MagicMock()
    monkeypatch.setattr(manager, "_save_progress", save, raising=True)

    manager._state.files_to_coarse_index = ["a.py"]
    manager._state.files_to_refine = ["b.py"]
    manager._state.target_index = {"a.py": {"hash": "h"}}

    manager._state.generation = 10
    manager._finalize_indexing(generation=10)
    assert manager._state.files_to_coarse_index == []
    assert manager._state.files_to_refine == []
    assert manager._state.target_index == {}

    # If generation mismatches, do not clear work queues
    manager._state.files_to_coarse_index = ["a.py"]
    manager._state.files_to_refine = ["b.py"]
    manager._state.target_index = {"a.py": {"hash": "h"}}

    manager._state.generation = 11
    manager._finalize_indexing(generation=10)
    assert manager._state.files_to_coarse_index == ["a.py"]
    assert manager._state.files_to_refine == ["b.py"]
    assert manager._state.target_index == {"a.py": {"hash": "h"}}


def test_query_returns_empty_when_shutdown_requested(manager):
    manager._state.shutdown_requested = True
    assert manager.query("x") == []


def test_query_returns_empty_when_init_in_progress(manager, monkeypatch):
    coord_state = {"in_progress": True}
    _patch_coordinator_properties(monkeypatch, manager._init_coordinator, state=coord_state)

    info = MagicMock()
    manager._error_handler.info = info

    assert manager.query("x") == []
    info.assert_called_once()
