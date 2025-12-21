from __future__ import annotations

from dataclasses import dataclass
from types import SimpleNamespace
from pathlib import Path
from unittest.mock import MagicMock

import pytest

import aye.model.index_manager.index_manager_executor as exec_mod


class FakeFuture:
    def __init__(self, *, value=None, exc: Exception | None = None):
        self._value = value
        self._exc = exc
        self.cancel_called = 0

    def result(self, timeout=None):
        if self._exc is not None:
            raise self._exc
        return self._value

    def cancel(self):
        self.cancel_called += 1
        return True


class FakeExecutor:
    def __init__(self, *args, **kwargs):
        self.submitted = []

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def submit(self, fn, *args, **kwargs):
        try:
            val = fn(*args, **kwargs)
            fut = FakeFuture(value=val)
        except Exception as e:
            fut = FakeFuture(exc=e)
        self.submitted.append((fn, args, kwargs, fut))
        return fut


def _make_executor(tmp_path: Path, *, save_interval=2, generation=1):
    config = SimpleNamespace(
        root_path=tmp_path,
        max_workers=2,
        save_interval=save_interval,
    )
    state = SimpleNamespace(
        generation=generation,
        current_index_on_disk={},
        target_index={},
    )
    progress = MagicMock()
    error_handler = MagicMock()
    save_callback = MagicMock()
    should_stop = MagicMock(return_value=False)
    collection = object()

    pe = exec_mod.PhaseExecutor(
        config=config,
        state=state,
        progress=progress,
        error_handler=error_handler,
        collection=collection,
        should_stop=should_stop,
        save_callback=save_callback,
    )
    return pe, config, state, progress, error_handler, save_callback, should_stop


def test_execute_coarse_phase_early_returns_on_empty_list(tmp_path, monkeypatch):
    pe, *_ = _make_executor(tmp_path)
    pe._run_phase = MagicMock()

    pe.execute_coarse_phase([], generation=1)

    pe._run_phase.assert_not_called()


def test_execute_refine_phase_early_returns_on_should_stop(tmp_path):
    pe, _config, _state, progress, _eh, _save, should_stop = _make_executor(tmp_path)
    should_stop.return_value = True

    pe.execute_refine_phase(["a.py"], generation=1)

    progress.set_active.assert_not_called()


def test_filter_files_for_processing_skips_already_indexed_in_coarse(tmp_path):
    pe, _config, state, progress, _eh, _save, _stop = _make_executor(tmp_path)
    state.current_index_on_disk = {"a.py": {"hash": "x"}}

    out = pe._filter_files_for_processing(["a.py", "b.py"], is_refinement=False)

    assert out == ["b.py"]
    progress.increment.assert_called_once_with("coarse")


def test_filter_files_for_processing_skips_refined_in_refine(tmp_path):
    pe, _config, state, progress, _eh, _save, _stop = _make_executor(tmp_path)
    state.current_index_on_disk = {
        "a.py": {"hash": "x", "refined": True},
        "b.py": {"hash": "y", "refined": False},
    }

    out = pe._filter_files_for_processing(["a.py", "b.py", "c.py"], is_refinement=True)

    assert out == ["b.py", "c.py"]
    progress.increment.assert_called_once_with("refine")


def test_update_index_after_processing_coarse_sets_current_index(tmp_path):
    pe, _config, state, _progress, _eh, _save, _stop = _make_executor(tmp_path)
    state.target_index = {"a.py": {"hash": "h1"}}

    pe._update_index_after_processing("a.py", is_refinement=False)

    assert state.current_index_on_disk == {"a.py": {"hash": "h1"}}


def test_update_index_after_processing_refine_updates_existing_meta(tmp_path):
    pe, _config, state, _progress, _eh, _save, _stop = _make_executor(tmp_path)
    state.current_index_on_disk = {"a.py": {"hash": "h1", "refined": False}}

    pe._update_index_after_processing("a.py", is_refinement=True)

    assert state.current_index_on_disk["a.py"]["refined"] is True


def test_update_index_after_processing_refine_adds_from_target_when_missing(tmp_path):
    pe, _config, state, _progress, _eh, _save, _stop = _make_executor(tmp_path)
    state.target_index = {"a.py": {"hash": "h1"}}

    pe._update_index_after_processing("a.py", is_refinement=True)

    assert state.current_index_on_disk == {"a.py": {"hash": "h1", "refined": True}}


def test_run_phase_saves_every_save_interval_and_updates_index(tmp_path, monkeypatch):
    pe, _config, state, _progress, error_handler, save_callback, _stop = _make_executor(
        tmp_path, save_interval=2, generation=1
    )

    # Avoid real threads and sleeps.
    monkeypatch.setattr(exec_mod, "DaemonThreadPoolExecutor", FakeExecutor, raising=True)
    monkeypatch.setattr(exec_mod.time, "sleep", lambda *_: None, raising=True)
    monkeypatch.setattr(exec_mod.concurrent.futures, "as_completed", lambda d: list(d.keys()), raising=True)

    files = [f"f{i}.py" for i in range(6)]
    state.target_index = {f: {"hash": f"h-{f}"} for f in files}

    def worker(path):
        return path

    pe._run_phase(worker_func=worker, file_list=files, is_refinement=False, generation=1)

    assert set(state.current_index_on_disk.keys()) == set(files)
    assert save_callback.call_count == 3  # 6 files, save_interval=2
    error_handler.handle_silent.assert_not_called()


def test_run_phase_handles_worker_exception_silently(tmp_path, monkeypatch):
    pe, _config, state, _progress, error_handler, save_callback, _stop = _make_executor(
        tmp_path, save_interval=2, generation=1
    )

    monkeypatch.setattr(exec_mod, "DaemonThreadPoolExecutor", FakeExecutor, raising=True)
    monkeypatch.setattr(exec_mod.time, "sleep", lambda *_: None, raising=True)
    monkeypatch.setattr(exec_mod.concurrent.futures, "as_completed", lambda d: list(d.keys()), raising=True)

    files = ["ok1.py", "bad.py", "ok2.py"]
    state.target_index = {f: {"hash": f"h-{f}"} for f in files}

    def worker(path):
        if path == "bad.py":
            raise RuntimeError("boom")
        return path

    pe._run_phase(worker_func=worker, file_list=files, is_refinement=False, generation=1)

    # bad.py failed, should not be added
    assert set(state.current_index_on_disk.keys()) == {"ok1.py", "ok2.py"}
    error_handler.handle_silent.assert_called_once()

    # only 2 successes -> save_interval=2 -> one save
    assert save_callback.call_count == 1


def test_run_phase_aborts_when_generation_changes_and_cancels_remaining(tmp_path, monkeypatch):
    pe, _config, state, _progress, _eh, save_callback, _stop = _make_executor(
        tmp_path, save_interval=10, generation=1
    )

    monkeypatch.setattr(exec_mod, "DaemonThreadPoolExecutor", FakeExecutor, raising=True)
    monkeypatch.setattr(exec_mod.time, "sleep", lambda *_: None, raising=True)
    monkeypatch.setattr(exec_mod.concurrent.futures, "as_completed", lambda d: list(d.keys()), raising=True)

    files = ["a.py", "b.py", "c.py", "d.py"]
    state.target_index = {f: {"hash": f"h-{f}"} for f in files}

    def worker(path):
        return path

    # IMPORTANT: our FakeExecutor executes worker() during submit().
    # If we changed generation inside worker(), _run_phase() would abort before
    # processing any completed futures. Instead, flip generation after the FIRST
    # successful update, so we get at least one indexed file and then abort.
    original_update = pe._update_index_after_processing
    update_calls = {"n": 0}

    def update_then_flip_generation(path: str, is_refinement: bool):
        original_update(path, is_refinement)
        update_calls["n"] += 1
        if update_calls["n"] == 1:
            state.generation = 999

    pe._update_index_after_processing = update_then_flip_generation

    # Capture futures created so we can assert cancel called.
    created_futures = []

    class CapturingExecutor(FakeExecutor):
        def submit(self, fn, *args, **kwargs):
            fut = super().submit(fn, *args, **kwargs)
            created_futures.append(fut)
            return fut

    monkeypatch.setattr(exec_mod, "DaemonThreadPoolExecutor", CapturingExecutor, raising=True)

    pe._run_phase(worker_func=worker, file_list=files, is_refinement=False, generation=1)

    # Should have processed at least one file and then aborted.
    assert len(state.current_index_on_disk) >= 1
    assert len(state.current_index_on_disk) < len(files)

    # When aborting mid-batch, it cancels all futures in the current batch.
    assert sum(f.cancel_called for f in created_futures) >= 1

    # save_interval high -> likely no save unless abort occurs after some successes
    assert save_callback.call_count in (0, 1)


def test_process_one_file_coarse_reads_and_calls_vector_db(tmp_path, monkeypatch):
    root = tmp_path
    p = root / "a.py"
    p.write_text("print('hi')", encoding="utf-8")

    pe, config, _state, progress, error_handler, _save, should_stop = _make_executor(root)
    should_stop.return_value = False

    vector_db = MagicMock()
    monkeypatch.setattr(exec_mod, "vector_db", vector_db, raising=True)
    monkeypatch.setattr(exec_mod.time, "sleep", lambda *_: None, raising=True)

    out = pe._process_one_file_coarse("a.py")

    assert out == "a.py"
    vector_db.update_index_coarse.assert_called_once()
    progress.increment.assert_called_once_with("coarse")
    error_handler.handle_silent.assert_not_called()


def test_process_one_file_refine_reads_and_calls_vector_db(tmp_path, monkeypatch):
    root = tmp_path
    p = root / "a.py"
    p.write_text("print('hi')", encoding="utf-8")

    pe, _config, _state, progress, error_handler, _save, should_stop = _make_executor(root)
    should_stop.return_value = False

    vector_db = MagicMock()
    monkeypatch.setattr(exec_mod, "vector_db", vector_db, raising=True)
    monkeypatch.setattr(exec_mod.time, "sleep", lambda *_: None, raising=True)

    out = pe._process_one_file_refine("a.py")

    assert out == "a.py"
    vector_db.refine_file_in_index.assert_called_once()
    progress.increment.assert_called_once_with("refine")
    error_handler.handle_silent.assert_not_called()


def test_process_one_file_skips_large_file_and_increments_progress(tmp_path, monkeypatch):
    root = tmp_path
    p = root / "big.bin"
    p.write_text("x", encoding="utf-8")

    pe, _config, _state, progress, error_handler, _save, should_stop = _make_executor(root)
    should_stop.return_value = False

    # Force stat().st_size to be > 1MB
    class Stat:
        st_size = 1024 * 1024 + 1

    monkeypatch.setattr(Path, "stat", lambda self: Stat(), raising=True)
    monkeypatch.setattr(exec_mod.time, "sleep", lambda *_: None, raising=True)

    out = pe._process_one_file_coarse("big.bin")

    assert out is None
    error_handler.info.assert_called_once()
    progress.increment.assert_called_once_with("coarse")


def test_process_one_file_handles_exception_and_increments_progress(tmp_path, monkeypatch):
    root = tmp_path
    p = root / "a.py"
    p.write_text("x", encoding="utf-8")

    pe, _config, _state, progress, error_handler, _save, should_stop = _make_executor(root)
    should_stop.return_value = False

    # Force read_text to fail
    monkeypatch.setattr(
        Path,
        "read_text",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("read fail")),
        raising=True,
    )
    monkeypatch.setattr(exec_mod.time, "sleep", lambda *_: None, raising=True)

    out = pe._process_one_file_refine("a.py")

    assert out is None
    error_handler.handle_silent.assert_called_once()
    progress.increment.assert_called_once_with("refine")
