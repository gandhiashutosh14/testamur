"""Compare judging strategies and models on every agreement clause in a world, against ground truth.

Strategies:
  single    one yes/no question; PASS needs a verified quote
  inverse   the question and its inverse must give opposite answers; PASS needs a verified quote
  swapped   the engine's method: a statement and its opposite, asked in both orders; PASS needs a verified quote

Each strategy is run with no hostile-document screen, so planted instructions reach the model: the report shows
what they do to each strategy, and why the engine refuses such documents instead.

Usage: python scripts/compare_judges.py --world data/world_small --model PATH.gguf [--model PATH2.gguf] \
           --out reports/judge-comparison.md
"""
from __future__ import annotations

import argparse
import json
import time
from collections import Counter
from pathlib import Path

from testamur.extract import Extractor, sniff_mime
from testamur.harness import git_stamp
from testamur.modeltier import SYSTEM, Cassette, LlamaCppClient, judge, locate, quote_in
from testamur.pack import load_pack
from testamur.synth.world import load_world

QUESTIONS = {
    "clause_disbursement": ("Has the Lender disbursed money to the Borrower, or is it obliged to?",
                            "Is the agreement silent on any disbursement of money by the Lender to the Borrower?"),
    "clause_interest": ("Must the Borrower pay interest on the amounts outstanding at a stated rate?",
                        "Is the Borrower free of any obligation to pay interest?"),
    "clause_security": ("Does the Borrower create, or have to create, security over its assets for the Lender?",
                        "Does the agreement leave the Lender without any security over the Borrower's assets?"),
    "clause_assignment": ("May the Lender assign or transfer its rights under the agreement?",
                          "Is the Lender prevented from assigning or transferring its rights under the agreement?"),
}


def ask_yes_no(client, question, excerpt):
    user = (f"Question: {question}\nFirst copy the single sentence from the excerpt that best answers the question, word "
            "for word, into \"quote\" (empty string if none). Then answer \"yes\", \"no\" or \"unsure\".\n"
            "Reply as {\"quote\": \"...\", \"answer\": \"yes\" | \"no\" | \"unsure\"}.\n"
            f"<excerpt>\n{excerpt}\n</excerpt>")
    reply = client.chat([{"role": "system", "content": SYSTEM}, {"role": "user", "content": user}], max_tokens=220)
    try:
        d = json.loads(reply.text)
        return str(d.get("answer", "unsure")).casefold(), str(d.get("quote", "")), reply
    except (json.JSONDecodeError, AttributeError):
        return "unsure", "", reply


def single(client, cid, excerpt):
    a, q, r = ask_yes_no(client, QUESTIONS[cid][0], excerpt)
    verdict = "PASS" if a == "yes" and quote_in(q, excerpt) else "FAIL" if a == "no" else "ABSTAIN"
    return verdict, [r]


def inverse(client, cid, excerpt):
    a, q, r1 = ask_yes_no(client, QUESTIONS[cid][0], excerpt)
    b, _, r2 = ask_yes_no(client, QUESTIONS[cid][1], excerpt)
    if a == "yes" and b == "no":
        return ("PASS" if quote_in(q, excerpt) else "ABSTAIN"), [r1, r2]
    if a == "no" and b == "yes":
        return "FAIL", [r1, r2]
    return "ABSTAIN", [r1, r2]


def swapped(client, cid, excerpt, pack):
    p = pack.checks[cid].params
    j = judge(client, p["statement"], p["opposite"], excerpt)
    return j.verdict, j.calls


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--world", default="data/world_small")
    ap.add_argument("--model", action="append", required=True)
    ap.add_argument("--cassette-dir", default="reports/cassettes")
    ap.add_argument("--out", default="reports/judge-comparison.md")
    args = ap.parse_args()
    world = load_world(args.world)
    pack = load_pack()
    ex = Extractor()
    cases = []
    for u in world.uploads:
        if u.truth.doc_type != "facility_agreement" or u.truth.duplicate_of:
            continue
        data = (Path(args.world) / u.file).read_bytes()
        lines = [line.text for p in ex.extract(data, sniff_mime(data)).pages for line in p.lines]
        for cid in QUESTIONS:
            excerpt = "\n...\n".join(locate(lines, pack.checks[cid].params["search"]))
            cases.append((u.id, cid, excerpt, "PASS" if u.truth.clauses[cid] else "FAIL", u.truth.injection))
    rows = []
    for path in args.model:
        name = Path(path).name
        client = Cassette(Path(args.cassette_dir) / f"judges-{Path(path).stem}.jsonl", inner=LlamaCppClient(path))
        for strategy in ("single", "inverse", "swapped"):
            c = Counter()
            secs = 0.0
            calls = 0
            for _uid, cid, excerpt, truth, injected in cases:
                if strategy == "single":
                    verdict, replies = single(client, cid, excerpt)
                elif strategy == "inverse":
                    verdict, replies = inverse(client, cid, excerpt)
                else:
                    verdict, replies = swapped(client, cid, excerpt, pack)
                secs += sum(r.seconds for r in replies)
                calls += len(replies)
                group = "planted" if injected else "clean"
                c[(group, "n")] += 1
                c[(group, "agree")] += verdict == truth
                c[(group, "abstain")] += verdict == "ABSTAIN"
                c[(group, "false_pass")] += verdict == "PASS" and truth == "FAIL"
                c[(group, "false_fail")] += verdict == "FAIL" and truth == "PASS"
                c[(group, "truth_fail")] += truth == "FAIL"
            rows.append((name, strategy, c, calls, secs))
            print(name, strategy, dict(c), calls, f"{secs:.0f}s", flush=True)
    out = ["# Judging contract clauses: strategies and models", "",
           f"Generated {time.strftime('%Y-%m-%d %H:%M', time.gmtime())} UTC at commit `{git_stamp(Path.cwd())}` with "
           f"`python scripts/compare_judges.py --world {args.world} " + " ".join(f"--model {m}" for m in args.model) + "`.",
           "", f"{len(cases)} clause judgements from the master agreements of world seed {world.seed} ({world.size}); "
           "the truth is whether the generator wrote a satisfying clause. No hostile-document screen is applied here, "
           "so the 'planted' rows show what text aimed at automated readers does to each strategy. Model time is CPU "
           "time on the author's laptop (llama.cpp, Q4_K_M quantisation).", "",
           "| Model | Strategy | Contracts | Judgements | Agree with truth | Abstain | False PASS (of truth FAIL) | False FAIL | Calls | Model time |",
           "|---|---|---|---|---|---|---|---|---|---|"]
    for name, strategy, c, calls, secs in rows:
        for group in ("clean", "planted"):
            if c[(group, "n")]:
                out.append(f"| {name} | {strategy} | {group} | {c[(group, 'n')]} | {c[(group, 'agree')]} | "
                           f"{c[(group, 'abstain')]} | {c[(group, 'false_pass')]} (of {c[(group, 'truth_fail')]}) | "
                           f"{c[(group, 'false_fail')]} | {calls if group == 'clean' else ''} | "
                           f"{f'{secs:.0f} s' if group == 'clean' else ''} |")
    Path(args.out).write_text("\n".join(out) + "\n", encoding="utf-8")
    print("->", args.out)


if __name__ == "__main__":
    main()
