"""The risk classification that decides how much review a change needs."""
import importlib.util
from pathlib import Path

spec = importlib.util.spec_from_file_location("risk", Path(__file__).resolve().parents[1] / "scripts" / "risk.py")
risk = importlib.util.module_from_spec(spec)
spec.loader.exec_module(risk)


def test_what_decides_vouching_or_isolation_is_high_risk():
    level, areas = risk.classify(["testamur/status.py", "docs/architecture.md"])
    assert level == "high" and areas == ["the status policy"]
    assert risk.classify(["testamur/packs/insolvency_claims/pack.yaml"])[0] == "high"
    assert risk.classify([".github/workflows/ci.yml"])[0] == "high"           # the pipeline judges the rest
    assert risk.classify(["scripts/risk.py"])[0] == "high"                    # and this file judges the pipeline
    assert risk.classify(["tests\\conftest.py"])[0] == "high"                 # Windows separators too


def test_other_code_is_medium_and_words_are_low():
    assert risk.classify(["testamur/cli.py"]) == ("medium", [])
    assert risk.classify(["docs/walkthrough.md", "reports/x.md", "README.md", "tests/test_jobs.py"]) == ("low", [])
