"""Suite-wide fixtures.

The one thing that belongs here: keeping every test off the operator's live
config. The runtime resolves its config from ``GDSTT_HOME`` (falling back to
``./data/config.yml``), and several code paths read it without being asked --
``is_run_enabled`` on every polling-loop iteration, most visibly. A suite that
reads the developer's real file gives different results on different machines,
and in one case turned a 12-second run into a hang: with ``run.enabled: false``
set by ``gdstt stop``, the loop takes its paused branch, whose only exit is the
``time.sleep`` these tests mock away.
"""

from __future__ import annotations

import pytest

from src import delegation


@pytest.fixture(autouse=True)
def clean_env(monkeypatch, tmp_path):
    """Point the config-home resolver at a throwaway per-test directory.

    The directory is left empty on purpose: every reader treats a missing config
    as "not configured" and falls back to its documented default, which is what a
    test that never mentions config should see. Resolver and ``config init`` tests
    override or delete ``GDSTT_HOME`` themselves. The runtime reads no other
    environment variables.
    """
    monkeypatch.setenv("GDSTT_HOME", str(tmp_path))
    yield


@pytest.fixture(autouse=True)
def forget_delegated_clients():
    """Keep one test's impersonated clients out of the next one's cache.

    ``delegation`` keeps a client per employee for the life of the process, which is
    right for a daemon and wrong for a suite: without this, a test that patches the
    builder would be served the previous test's MagicMock and pass for the wrong
    reason.
    """
    delegation.forget_clients()
    yield
    delegation.forget_clients()
