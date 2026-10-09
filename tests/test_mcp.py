"""The agent tool server: one tenant per key, only the tools that tenant granted, pages with a fixed number of
queries, and no change without a person."""
import asyncio
import json

import pytest
from mcp.client import Client
from sqlalchemy import select

from testamur import agents
from testamur.mcp_server import build_server
from testamur.model import Status
from testamur.store import AuditRow, CheckReview, Proposal, RequirementState


def _call(server, tool, args):
    async def go():
        async with Client(server) as c:
            return await c.call_tool(tool, args)
    return asyncio.run(go())


def _tools(server):
    async def go():
        async with Client(server) as c:
            return sorted(t.name for t in (await c.list_tools()).tools)
    return asyncio.run(go())


def _data(result):
    assert not result.is_error, result.content
    return json.loads(result.content[0].text)


def _agent(store, *tools):
    key = agents.create_key(store, 1, "agent-a")
    for tool in tools:
        agents.grant(store, 1, tool, "admin@buyer")
    return key


def test_an_agent_sees_and_calls_only_the_tools_its_tenant_granted(env):
    world, store, engine = env
    server = build_server(engine, _agent(store, "list_engagements"))
    assert _tools(server) == ["list_engagements"]
    assert _call(server, "get_requirement", {"engagement": 1, "requirement": "insurance"}).is_error
    agents.revoke(store, 1, "list_engagements", "admin@buyer")
    assert _call(server, "list_engagements", {}).is_error              # refused at once, without a restart
    with pytest.raises(PermissionError):
        build_server(engine, "tsk_not_a_key")


def test_reads_stay_inside_the_tenant_and_page_with_a_fixed_number_of_queries(env):
    world, store, engine = env
    server = build_server(engine, _agent(store, "list_engagements", "get_requirement"))
    seen, cursor = [], 0
    while cursor is not None:
        page = _data(_call(server, "list_engagements", {"cursor": cursor, "limit": 4}))
        seen += [x["engagement"] for x in page["items"]]
        cursor = page["next_cursor"]
    assert sorted(seen) == sorted(e.id for e in world.engagements if e.tenant_id == 1)
    with agents.count_queries(store) as small:
        agents.list_engagements(store, 1, 0, 2)
    with agents.count_queries(store) as large:
        agents.list_engagements(store, 1, 0, 50)
    assert small[0] == large[0] <= 6                                    # not one query per row
    other = next(e.id for e in world.engagements if e.tenant_id == 2)
    assert _call(server, "get_requirement", {"engagement": other, "requirement": "insurance"}).is_error


def test_an_agent_proposes_and_only_a_person_changes_anything(env):
    world, store, engine = env
    server = build_server(engine, _agent(store, "propose_check_verdict", "proposal_status"))
    with store.tenant(1) as s:
        eids = [st.engagement_id for st in s.scalars(select(RequirementState).where(
            RequirementState.requirement_id == "insurance", RequirementState.status == Status.SATISFIED))][:2]

    def status(eid):
        with store.tenant(1) as s:
            return s.get(RequirementState, {"engagement_id": eid, "requirement_id": "insurance", "tenant_id": 1}).status

    def propose(eid):
        return _data(_call(server, "propose_check_verdict", {"engagement": eid, "requirement": "insurance",
                                                             "check": "ins_limit_meets_tier", "verdict": "FAIL",
                                                             "reason": "the limit shown is the aggregate"}))["proposal"]
    first, second = propose(eids[0]), propose(eids[1])
    assert status(eids[0]) == status(eids[1]) == Status.SATISFIED            # proposing changes nothing
    assert _data(_call(server, "proposal_status", {"proposal": first}))["status"] == "proposed"
    assert agents.decide(engine, 1, second, "reviewer@buyer", confirm=False) == "declined"
    assert status(eids[1]) == Status.SATISFIED
    agents.decide(engine, 1, first, "reviewer@buyer", confirm=True)
    assert status(eids[0]) == Status.NOT_SATISFIED
    with store.tenant(1) as s:
        assert s.get(Proposal, first).status == "applied"
        review = s.scalars(select(CheckReview).where(CheckReview.engagement_id == eids[0])).one()
        assert review.reviewer == "reviewer@buyer (proposed by agent agent-a)"
        assert s.scalar(select(AuditRow).where(AuditRow.action == "proposal_applied")).detail["agent"] == "agent-a"
    with pytest.raises(ValueError):
        agents.decide(engine, 1, first, "reviewer@buyer", confirm=True)      # once only
    assert _call(server, "propose_check_verdict", {"engagement": eids[0], "requirement": "insurance",
                                                   "check": "ins_limit_meets_tier", "verdict": "ABSTAIN",
                                                   "reason": "unsure"}).is_error
