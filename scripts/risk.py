"""Which parts of the system a change touches, and how much review it therefore needs.

High risk: the code and configuration that decide what the platform vouches for or whom it shows what (the status
policy, check rules, reading values, matching, verdict handling, tenant isolation, accuracy and the breaker, holds
and webhooks, the agent surface, the domain pack) and the machinery that keeps those honest (the gates, the mutation
proof, test discipline, the pipeline itself). A high-risk pull request needs an approving review and is never merged
automatically. Medium: other engine code. Low: tests, documentation, reports.

Usage:
  python scripts/risk.py FILE...                  classify the given paths
  python scripts/risk.py --base REF --head REF    classify a diff
  python scripts/risk.py --base REF --head REF --pr N
        in CI: label the pull request risk:<level>, write the job summary, and fail while a high-risk pull request
        has no approving review. CI runs this script from the base branch, so a pull request cannot change the rules
        it is judged by.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys

HIGH = {
    "testamur/status.py": "the status policy",
    "testamur/checks.py": "check rules",
    "testamur/textnorm.py": "reading values",
    "testamur/understand.py": "reading documents",
    "testamur/match.py": "matching documents to suppliers",
    "testamur/engine.py": "verdicts, decisions and statuses",
    "testamur/store.py": "tenant isolation and storage",
    "testamur/accuracy.py": "accuracy and the breaker",
    "testamur/modeltier.py": "the model tier",
    "testamur/actions.py": "holds, reminders and webhooks",
    "testamur/agents.py": "the agent surface",
    "testamur/mcp_server.py": "the agent surface",
    "testamur/assistant.py": "what the assistant may say",
    "testamur/budget.py": "spend caps",
    "testamur/packs/": "the domain pack",
    "gates.yaml": "the merge gates",
    "scripts/prove_tests_fire.py": "the mutation proof",
    "scripts/risk.py": "this risk classification",
    "tests/conftest.py": "test discipline",
    ".github/": "the pipeline itself",
}
LOW_PREFIXES = ("tests/", "docs/", "reports/", "README", "LICENSE", ".gitignore", ".gitattributes")
LEVELS = ("low", "medium", "high")


def classify(paths: list[str]) -> tuple[str, list[str]]:
    level, areas = "low", []
    for raw in paths:
        path = raw.replace("\\", "/")
        hit = next((area for prefix, area in HIGH.items() if path == prefix or path.startswith(prefix)), None)
        if hit:
            level = "high"
            areas.append(hit)
        elif not path.startswith(LOW_PREFIXES) and LEVELS.index(level) < 1:
            level = "medium"
    return level, sorted(set(areas))


def _run(*cmd: str) -> str:
    return subprocess.run(cmd, check=True, capture_output=True, text=True).stdout


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("paths", nargs="*")
    ap.add_argument("--base")
    ap.add_argument("--head")
    ap.add_argument("--pr", type=int)
    a = ap.parse_args(argv)
    paths = a.paths or _run("git", "diff", "--name-only", f"{a.base}...{a.head}").split()
    level, areas = classify(paths)
    lines = [f"Risk: **{level}** ({len(paths)} files changed)."]
    if areas:
        lines.append("Touches: " + "; ".join(areas) + ".")
    if level == "high":
        lines.append("Needs an approving review; never merged automatically.")
    print("\n".join(lines))
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
    if a.pr is None:
        return 0
    for name, colour in (("risk:high", "b60205"), ("risk:medium", "fbca04"), ("risk:low", "0e8a16")):
        subprocess.run(["gh", "label", "create", name, "--color", colour, "--force"], capture_output=True)
    stale = [f"risk:{x}" for x in LEVELS if x != level]
    _run("gh", "pr", "edit", str(a.pr), "--add-label", f"risk:{level}", "--remove-label", ",".join(stale))
    if level != "high":
        return 0
    view = json.loads(_run("gh", "pr", "view", str(a.pr), "--json", "reviewDecision,autoMergeRequest"))
    if view.get("autoMergeRequest"):
        print("A high-risk pull request may not be merged automatically: turn auto-merge off.")
        return 1
    if view.get("reviewDecision") != "APPROVED":
        print("Waiting for an approving review.")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
