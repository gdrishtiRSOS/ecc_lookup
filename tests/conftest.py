import urllib.error

import pytest


@pytest.fixture(autouse=True)
def no_network(monkeypatch, tmp_path):
    """Block all real network access for every test by default, and give
    each test its own empty cache directory (instead of the real, shared
    platform-temp cache _resolve_cache_dir(None) would otherwise resolve
    to) so a file this machine already downloaded during manual testing
    can never make a test silently pass on stale real data. Tests that
    need to exercise the registry join logic inject a fixture registry by
    monkeypatching `ecc_lookup._fetch_registry` directly instead of relying
    on the network, per CLAUDE.md's "tests must not hit network" rule.
    """

    def _raise(*args, **kwargs):
        raise urllib.error.URLError("network disabled in tests")

    monkeypatch.setattr("urllib.request.urlopen", _raise)
    monkeypatch.setattr("tempfile.gettempdir", lambda: str(tmp_path))
