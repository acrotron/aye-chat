import hashlib
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
