"""Suite-wide safety net: the three routes out of this suite to a browser."""
import pytest

import safety
import tiers


@pytest.fixture(autouse=True)
def _no_real_browser(monkeypatch):
    def _refuse(port, timeout):
        raise AssertionError(
            "a test tried to open a real DevTools connection on port %s. "
            "Install a fake transport (see tests/test_cdp_transport.py) rather "
            "than reaching a browser: on a machine where the CDP Chrome is "
            "running, this would have created a target in it." % port)

    def _no_port(port: int = 9222, timeout: float = 0.5) -> bool:
        """No DevTools port is reachable from this suite, deterministically."""
        return False

    monkeypatch.setattr(tiers, "_cdp_browser_ws_url", _refuse)
    def _refuse_spawn(args, timeout=None):
        raise AssertionError(
            "a test reached the real tiers._run_pw with args=%r and no fake "
            "installed. That call spawns playwright-cli, which can launch a "
            "browser on this machine. Install a fake (see the `sessions` "
            "fixture in tests/test_reaper_ordering.py) before driving it."
            % (args,))

    monkeypatch.setattr(safety, "cdp_port_reachable", _no_port)
    monkeypatch.setattr(tiers, "_run_pw", _refuse_spawn)
