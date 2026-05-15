import concurrent.futures.thread as _cf_thread
import hashlib
import threading
from unittest.mock import MagicMock

import aye.model.index_manager.index_manager_utils as utils


def _clear_active_managers():
    # Keep global module state isolated between tests.
    with utils._cleanup_lock:
        utils._active_managers.clear()


def test_constants_cpu_count_and_max_workers_invariants():
    assert isinstance(utils.CPU_COUNT, int)
    assert utils.CPU_COUNT >= 1

    expected = min(4, max(1, utils.CPU_COUNT // 2))
    assert utils.MAX_WORKERS == expected
    assert 1 <= utils.MAX_WORKERS <= 4


def test_daemon_thread_pool_executor_creates_daemon_threads():
    # Submitting work forces thread creation.
    with utils.DaemonThreadPoolExecutor(max_workers=1, thread_name_prefix="test") as ex:
        fut = ex.submit(lambda: 123)
        assert fut.result(timeout=2) == 123

        # Ensure at least one worker thread exists and it is daemonized.
        assert len(ex._threads) >= 1
        t = next(iter(ex._threads))
        assert t.daemon is True


def test_daemon_thread_pool_executor_all_workers_are_daemon():
    # With max_workers>1 and enough concurrent work to force every slot
    # to spin up its own thread, every created thread must be daemon.
    barrier = threading.Barrier(4)

    def hold_until_all_in():
        barrier.wait(timeout=5)
        return True

    with utils.DaemonThreadPoolExecutor(max_workers=4, thread_name_prefix="test") as ex:
        futures = [ex.submit(hold_until_all_in) for _ in range(4)]
        for f in futures:
            assert f.result(timeout=5) is True

        assert len(ex._threads) == 4
        assert all(t.daemon is True for t in ex._threads)


def test_daemon_thread_pool_executor_restores_threading_thread_reference():
    # The implementation swaps concurrent.futures.thread.threading.Thread for
    # the duration of _adjust_thread_count. Ensure the original reference is
    # restored after submit() completes — even after many calls.
    original = _cf_thread.threading.Thread
    with utils.DaemonThreadPoolExecutor(max_workers=2, thread_name_prefix="test") as ex:
        for _ in range(5):
            ex.submit(lambda: None).result(timeout=2)
            assert _cf_thread.threading.Thread is original

    assert _cf_thread.threading.Thread is original


def test_daemon_thread_pool_executor_restores_thread_reference_on_exception(monkeypatch):
    # Even if the standard _adjust_thread_count raises, the original
    # threading.Thread reference must be restored (try/finally invariant).
    original = _cf_thread.threading.Thread

    def boom(self):
        raise RuntimeError("kaboom")

    monkeypatch.setattr(
        "concurrent.futures.ThreadPoolExecutor._adjust_thread_count", boom
    )

    ex = utils.DaemonThreadPoolExecutor(max_workers=1, thread_name_prefix="test")
    try:
        try:
            ex.submit(lambda: None)
        except RuntimeError:
            pass
        assert _cf_thread.threading.Thread is original
    finally:
        ex.shutdown(wait=False)


def test_daemon_thread_pool_executor_concurrent_submits_all_produce_daemon_threads():
    # Concurrent submits from many threads exercise the
    # _adjust_thread_count_lock. All resulting workers must be daemon and
    # the threading.Thread reference must be restored at the end.
    original = _cf_thread.threading.Thread
    submitter_count = 8

    with utils.DaemonThreadPoolExecutor(max_workers=4, thread_name_prefix="test") as ex:
        results: list = []
        results_lock = threading.Lock()

        def submitter():
            fut = ex.submit(lambda: threading.current_thread().daemon)
            with results_lock:
                results.append(fut.result(timeout=5))

        submitters = [threading.Thread(target=submitter) for _ in range(submitter_count)]
        for s in submitters:
            s.start()
        for s in submitters:
            s.join(timeout=5)

        assert len(results) == submitter_count
        assert all(results), "every worker thread should report daemon=True"
        assert all(t.daemon is True for t in ex._threads)

    assert _cf_thread.threading.Thread is original


def test_daemon_thread_pool_executor_reuses_idle_thread():
    # Sequential submits within max_workers should reuse the same daemon
    # worker thread instead of spinning up new ones each time.
    with utils.DaemonThreadPoolExecutor(max_workers=2, thread_name_prefix="test") as ex:
        seen_thread_ids = set()
        for _ in range(5):
            tid = ex.submit(threading.get_ident).result(timeout=2)
            seen_thread_ids.add(tid)

        assert len(ex._threads) <= 2
        assert len(seen_thread_ids) <= 2
        assert all(t.daemon is True for t in ex._threads)


def test_set_low_priority_calls_os_nice_when_available(monkeypatch):
    nice = MagicMock()
    # On Windows, os.nice does not exist. Use raising=False so we can
    # simulate the attribute being available on this platform.
    monkeypatch.setattr(utils.os, "nice", nice, raising=False)

    utils.set_low_priority()

    nice.assert_called_once_with(5)


def test_set_low_priority_swallows_oserror(monkeypatch):
    def raising_nice(_):
        raise OSError("no permission")

    # On Windows, os.nice does not exist. Use raising=False so we can
    # simulate the attribute being available on this platform.
    monkeypatch.setattr(utils.os, "nice", raising_nice, raising=False)

    # Should not raise.
    utils.set_low_priority()


def test_set_discovery_thread_low_priority_calls_os_nice_when_available(monkeypatch):
    nice = MagicMock()
    # On Windows, os.nice does not exist. Use raising=False so we can
    # simulate the attribute being available on this platform.
    monkeypatch.setattr(utils.os, "nice", nice, raising=False)

    utils.set_discovery_thread_low_priority()

    nice.assert_called_once_with(5)


def test_set_discovery_thread_low_priority_swallows_oserror(monkeypatch):
    def raising_nice(_):
        raise OSError("no permission")

    # On Windows, os.nice does not exist. Use raising=False so we can
    # simulate the attribute being available on this platform.
    monkeypatch.setattr(utils.os, "nice", raising_nice, raising=False)

    # Should not raise.
    utils.set_discovery_thread_low_priority()


def test_register_and_unregister_manager_updates_registry():
    _clear_active_managers()

    m1 = MagicMock()
    m2 = MagicMock()

    utils.register_manager(m1)
    utils.register_manager(m2)

    with utils._cleanup_lock:
        assert utils._active_managers == [m1, m2]

    utils.unregister_manager(m1)

    with utils._cleanup_lock:
        assert utils._active_managers == [m2]

    # Unregistering a non-existent manager should be a no-op.
    utils.unregister_manager(m1)
    with utils._cleanup_lock:
        assert utils._active_managers == [m2]


def test_cleanup_all_managers_calls_shutdown_and_swallows_exceptions():
    _clear_active_managers()

    ok = MagicMock()

    bad = MagicMock()
    bad.shutdown.side_effect = RuntimeError("boom")

    utils.register_manager(ok)
    utils.register_manager(bad)

    # Should not raise despite one manager failing.
    utils._cleanup_all_managers()

    ok.shutdown.assert_called_once()
    bad.shutdown.assert_called_once()


def test_calculate_hash_matches_sha256_hexdigest():
    content = "hello world"
    expected = hashlib.sha256(content.encode("utf-8")).hexdigest()
    assert utils.calculate_hash(content) == expected
