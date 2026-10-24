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
        agents.grant(store, 1, tool, "admin@firm")
    return key


def test_an_agent_sees_and_calls_only_the_tools_its_tenant_granted(env):
    world, store, engine = env
    server = build_server(engine, _agent(store, "list_claims"))
    assert _tools(server) == ["list_claims"]
    assert _call(server, "get_requirement", {"claim": 1, "requirement": "authority"}).is_error
    agents.revoke(store, 1, "list_claims", "admin@firm")
    assert _call(server, "list_claims", {}).is_error              # refused at once, without a restart
    with pytest.raises(PermissionError):
        build_server(engine, "tsk_not_a_key")


def test_a_persons_key_does_not_open_the_agent_surface(env):
    """Keys carry a role: what an agent does is never recorded under a person's name, nor the reverse."""
    world, store, engine = env
    for role in ("reviewer", "uploader"):
        with pytest.raises(PermissionError):
            build_server(engine, agents.create_key(store, 1, f"person-{role}", role))
    with pytest.raises(ValueError):
        agents.create_key(store, 1, "someone", "admin")


def test_a_revoked_key_stops_working_on_a_server_already_running(env):
    """Authentication is checked on every call, not once when the server was built."""
    world, store, engine = env
    server = build_server(engine, _agent(store, "list_claims"))
    assert not _call(server, "list_claims", {}).is_error
    assert agents.revoke_key(store, 1, "agent-a", "admin@firm") == 1
    assert _call(server, "list_claims", {}).is_error
    with pytest.raises(PermissionError):
        build_server(engine, agents.create_key(store, 2, "agent-a") + "x")


def test_a_tool_granted_to_one_agent_is_not_another_agents(env):
    """Grants per agent: an investigator and a verifier of the same tenant hold different tools."""
    world, store, engine = env
    investigator = agents.create_key(store, 1, "investigator")
    verifier = agents.create_key(store, 1, "verifier")
    agents.grant(store, 1, "review_queue", "admin@firm", principal="investigator")
    agents.grant(store, 1, "list_claims", "admin@firm")                      # every agent of the tenant
    inv, ver = build_server(engine, investigator), build_server(engine, verifier)
    assert _tools(inv) == ["list_claims", "review_queue"] and _tools(ver) == ["list_claims"]
    assert _call(ver, "review_queue", {}).is_error and not _call(inv, "review_queue", {}).is_error
    agents.revoke(store, 1, "review_queue", "admin@firm", principal="investigator")
    assert _call(inv, "review_queue", {}).is_error


def test_reads_stay_inside_the_tenant_and_page_with_a_fixed_number_of_queries(env):
    world, store, engine = env
    server = build_server(engine, _agent(store, "list_claims", "get_requirement"))
    seen, cursor = [], 0
    while cursor is not None:
        page = _data(_call(server, "list_claims", {"cursor": cursor, "limit": 4}))
        seen += [x["claim"] for x in page["items"]]
        cursor = page["next_cursor"]
    assert sorted(seen) == sorted(e.id for e in world.claims if e.tenant_id == 1)
    with agents.count_queries(store) as small:
        agents.list_claims(store, 1, 0, 2)
    with agents.count_queries(store) as large:
        agents.list_claims(store, 1, 0, 50)
    assert small[0] == large[0] <= 6                                    # not one query per row
    other = next(e.id for e in world.claims if e.tenant_id == 2)
    assert _call(server, "get_requirement", {"claim": other, "requirement": "authority"}).is_error


def test_an_agent_proposes_and_only_a_person_changes_anything(env):
    world, store, engine = env
    server = build_server(engine, _agent(store, "propose_check_verdict", "proposal_status"))
    with store.tenant(1) as s:
        eids = [st.claim_id for st in s.scalars(select(RequirementState).where(
            RequirementState.requirement_id == "authority", RequirementState.status == Status.SATISFIED))][:2]

    def status(eid):
        with store.tenant(1) as s:
            return s.get(RequirementState, {"claim_id": eid, "requirement_id": "authority", "tenant_id": 1}).status

    def propose(eid):
        return _data(_call(server, "propose_check_verdict", {"claim": eid, "requirement": "authority",
                                                             "check": "auth_creditor_matches", "verdict": "FAIL",
                                                             "reason": "the limit shown is the aggregate"}))["proposal"]
    first, second = propose(eids[0]), propose(eids[1])
    assert status(eids[0]) == status(eids[1]) == Status.SATISFIED            # proposing changes nothing
    assert _data(_call(server, "proposal_status", {"proposal": first}))["status"] == "proposed"
    assert agents.decide(engine, 1, second, "reviewer@firm", confirm=False) == "declined"
    assert status(eids[1]) == Status.SATISFIED
    agents.decide(engine, 1, first, "reviewer@firm", confirm=True)
    assert status(eids[0]) == Status.NOT_SATISFIED
    with store.tenant(1) as s:
        assert s.get(Proposal, first).status == "applied"
        review = s.scalars(select(CheckReview).where(CheckReview.claim_id == eids[0])).one()
        assert review.reviewer == "reviewer@firm (proposed by agent agent-a)"
        assert s.scalar(select(AuditRow).where(AuditRow.action == "proposal_applied")).detail["agent"] == "agent-a"
    with pytest.raises(ValueError):
        agents.decide(engine, 1, first, "reviewer@firm", confirm=True)      # once only
    assert _call(server, "propose_check_verdict", {"claim": eids[0], "requirement": "authority",
                                                   "check": "auth_creditor_matches", "verdict": "ABSTAIN",
                                                   "reason": "unsure"}).is_error
