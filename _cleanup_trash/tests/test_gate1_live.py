import os

import pytest


@pytest.mark.live
@pytest.mark.skipif(
    os.environ.get("BANK_AGENT_LIVE_GATE") != "1",
    reason="Run explicitly with BANK_AGENT_LIVE_GATE=1",
)
def test_gate1_live_retrieval_and_extraction():
    from scripts.run_gate1 import run_live_gate

    report = run_live_gate()
    assert report["passed"], report["failures"]
