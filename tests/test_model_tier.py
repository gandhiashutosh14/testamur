"""The model tier with a scripted client: evidence must be real, and answers must survive swapping the order."""
import json

import pytest

from testamur.modeltier import Cassette, CassetteMiss, Reply, judge, locate, quote_in

EXCERPT = ("12. LIMITATION OF LIABILITY. The Supplier's total aggregate liability arising out of or in connection with "
           "this Agreement shall not exceed EUR 2,000,000. 13. TERM. This Agreement continues for three years.")
CLAIM, COUNTER = "Liability is capped.", "Liability is not capped."
REAL = "The Supplier's total aggregate liability arising out of or in connection with this Agreement shall not exceed EUR 2,000,000."


class Scripted:
    model_id = "scripted"

    def __init__(self, *answers):
        self.answers = list(answers)
        self.calls = 0

    def chat(self, messages, max_tokens=200):
        self.calls += 1
        statement, quote = self.answers.pop(0)
        return Reply(json.dumps({"statement": statement, "quote": quote}), 100, 20)


def test_pass_needs_both_orderings_and_a_quote_that_is_really_there():
    assert judge(Scripted((1, REAL), (2, REAL)), CLAIM, COUNTER, EXCERPT).verdict == "PASS"
    fabricated = "The Supplier's liability is capped at EUR 10,000,000 in all circumstances whatsoever."
    assert judge(Scripted((1, fabricated), (2, fabricated)), CLAIM, COUNTER, EXCERPT).verdict == "ABSTAIN"


def test_answering_by_position_is_caught():
    j = judge(Scripted((1, REAL), (1, REAL)), CLAIM, COUNTER, EXCERPT)       # always "statement 1"
    assert j.verdict == "ABSTAIN" and "order" in j.reason
    assert judge(Scripted((2, ""), (2, "")), CLAIM, COUNTER, EXCERPT).verdict == "ABSTAIN"


def test_consistent_absence_is_a_fail():
    assert judge(Scripted((2, ""), (1, "")), CLAIM, COUNTER, EXCERPT).verdict == "FAIL"


def test_an_unparseable_reply_is_never_a_verdict():
    class Garbage(Scripted):
        def chat(self, messages, max_tokens=200):
            return Reply("I think the clause is there!")
    assert judge(Garbage(), CLAIM, COUNTER, EXCERPT).verdict == "ABSTAIN"


def test_quote_verification_ignores_spacing_but_needs_substance():
    assert quote_in(REAL.replace(" ", "  "), EXCERPT)
    assert not quote_in("TERM.", EXCERPT)                     # too short to be evidence


def test_locate_finds_the_clause_passage():
    lines = [f"{i}. Boilerplate clause number {i} about payment and notices." for i in range(1, 30)]
    lines[17] = "18. LIMITATION OF LIABILITY. Total aggregate liability shall not exceed the cap."
    picked = " ".join(locate(lines, "limitation of liability total aggregate liability cap"))
    assert "LIMITATION OF LIABILITY" in picked


def test_a_replay_only_cassette_fails_loudly_on_an_unrecorded_call(tmp_path):
    tape = tmp_path / "c.jsonl"
    recorder = Cassette(tape, inner=Scripted((1, REAL)))
    msg = [{"role": "user", "content": "hello"}]
    assert recorder.chat(msg).text == json.dumps({"statement": 1, "quote": REAL})
    replay = Cassette(tape)
    assert replay.chat(msg).cached
    with pytest.raises(CassetteMiss):
        replay.chat([{"role": "user", "content": "something new"}])
