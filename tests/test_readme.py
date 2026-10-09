"""The README's results are written from the committed reports by scripts/results.py; they may not drift apart."""
import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("results", ROOT / "scripts" / "results.py")
results = importlib.util.module_from_spec(spec)
spec.loader.exec_module(results)


def test_the_readme_results_are_the_reports_results():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert results.START in readme and results.END in readme, "run: python scripts/results.py"
    assert results.render() in readme, "the README disagrees with the reports: run python scripts/results.py"
