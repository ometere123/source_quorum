"""Integration tests for SourceQuorum against live consensus.

These deploy to a real GenLayer environment and exercise what direct mode
cannot: validators independently fetching several live domains and having to
agree on what each one says.

    gltest tests/integration/ -v -s --network studionet

Every write method is driven and every view is read, with state printed after
each step. Run with -s to see the report.

What these are really for
-------------------------
Direct tests prove the state machine is right given a mocked gathering round.
The open question they cannot answer is whether independent validators, each
fetching several different hosts at slightly different moments and running the
model separately, agree on the *stance* of each source. That is the assumption
the whole primitive rests on, and only a real environment can test it.
"""

import json

from gltest import get_contract_factory
from gltest.accounts import create_accounts
from gltest.assertions import tx_execution_failed, tx_execution_succeeded


# Stable, text-heavy, independently owned, and unlikely to disappear. The
# question is deliberately one these pages genuinely answer.
QUESTION = (
    "Is the example.com domain reserved for use in documentation and examples "
    "without needing permission?"
)
SOURCES = [
    "https://example.com/",
    "https://www.iana.org/help/example-domains",
]


# resolve() runs two consensus rounds and fetches several live hosts, so it
# takes well past the default 150s wait. This is latency, not failure.
RESOLVE_WAIT = {"wait_interval": 5000, "wait_retries": 90}   # up to 7.5 minutes


def show(label, value):
    print(f"\n  [READ] {label}\n{json.dumps(value, indent=2, sort_keys=True, default=str)}")


def step(n, label):
    print(f"\n{'=' * 70}\n  WRITE {n}: {label}\n{'=' * 70}")


def expect_refused(label, fn):
    """A reverting write is mined with a failed result rather than raising."""
    try:
        receipt = fn()
    except Exception as exc:
        print(f"  [REFUSED as designed] {label}\n      {str(exc).strip().splitlines()[0][:150]}")
        return
    assert tx_execution_failed(receipt), f"{label} was allowed but must be refused"
    print(f"  [REFUSED as designed] {label}")


def test_full_public_surface():
    other = create_accounts(1)[0]

    factory = get_contract_factory("SourceQuorum")
    quorum = factory.deploy(args=[])
    print(f"\nDeployed SourceQuorum at {quorum.address}")

    # -- deterministic views before anything exists -------------------------
    show("query_count()", quorum.query_count().call())
    show("domain_of() -- ownership grouping is deterministic", {
        "https://news.bbc.co.uk/x": quorum.domain_of(args=["https://news.bbc.co.uk/x"]).call(),
        "https://www.iana.org/help": quorum.domain_of(args=["https://www.iana.org/help"]).call(),
    })
    show("get_source() on an unseen domain -- neutral, not an error",
         quorum.get_source(args=["never-seen-before.example"]).call())

    # -- WRITE 1 ------------------------------------------------------------
    step(1, "open_query  (fully deterministic -- no consensus round)")
    assert tx_execution_succeeded(
        quorum.open_query(args=[QUESTION, SOURCES, 2, 3650]).transact()
    )
    query_id = quorum.query_count().call()

    show("query_count()", query_id)
    show("get_query(id)", quorum.get_query(args=[query_id]).call())
    show("get_sources(id)", quorum.get_sources(args=[query_id]).call())
    show("get_verdict(id) -- pending, conclusive must be False",
         quorum.get_verdict(args=[query_id]).call())
    show("get_findings(id) -- empty before resolution",
         quorum.get_findings(args=[query_id]).call())

    assert quorum.get_query(args=[query_id]).call()["status_name"] == "PENDING"
    assert quorum.get_verdict(args=[query_id]).call()["conclusive"] is False

    expect_refused(
        "opening a query whose urls span one domain",
        lambda: quorum.open_query(
            args=[QUESTION, ["https://example.com/a", "https://www.example.com/b"], 2, 365]
        ).transact(),
    )
    expect_refused(
        "opening a query with min_independent=1",
        lambda: quorum.open_query(args=[QUESTION, SOURCES, 1, 365]).transact(),
    )

    # -- WRITE 2 ------------------------------------------------------------
    step(2, "resolve  (round 1 fetch+stance, then round 2 cluster+rule)")
    assert tx_execution_succeeded(quorum.resolve(args=[query_id]).transact(**RESOLVE_WAIT))

    state = quorum.get_query(args=[query_id]).call()
    verdict = quorum.get_verdict(args=[query_id]).call()
    findings = quorum.get_findings(args=[query_id]).call()

    show("get_query(id)  -- after resolution", state)
    show("get_verdict(id)", verdict)
    show("get_findings(id)  -- per-source stance and cluster", findings)

    print("\n  --- adjudication ------------------------------------------")
    print(f"  status              : {state['status_name']}")
    print(f"  independent clusters: {verdict['independent_clusters']}")
    print(f"  confidence band     : {verdict['confidence']}")
    print(f"  answer              : {verdict['answer'][:80]}")

    # The contract must land on a real terminal state, never stay pending.
    assert state["status_name"] in (
        "RESOLVED", "CONTRADICTED", "INSUFFICIENT", "UNAVAILABLE"
    )
    # Whatever the outcome, the invariant holds: conclusive implies a quorum.
    if verdict["conclusive"]:
        assert verdict["independent_clusters"] >= state["min_independent"], (
            "resolved below the quorum floor -- the deterministic re-check failed"
        )
        assert verdict["answer"] != ""
    else:
        assert verdict["answer"] == "", "a non-conclusive verdict must carry no answer"

    # Findings are keyed to the requested sources, in order.
    assert [f["url"] for f in findings] == SOURCES

    show("get_source() for each consulted domain", {
        f["domain"]: quorum.get_source(args=[f["domain"]]).call() for f in findings
    })

    expect_refused(
        "resolving an already-settled query",
        lambda: quorum.resolve(args=[query_id]).transact(**RESOLVE_WAIT),
    )

    # -- WRITE 3 ------------------------------------------------------------
    step(3, "cancel_query  (asker only, and only while pending)")
    assert tx_execution_succeeded(
        quorum.open_query(args=[QUESTION, SOURCES, 2, 3650]).transact()
    )
    second_id = quorum.query_count().call()

    expect_refused(
        "a non-asker cancelling",
        lambda: quorum.connect(other).cancel_query(args=[second_id]).transact(),
    )

    assert tx_execution_succeeded(quorum.cancel_query(args=[second_id]).transact())
    show("get_query(second)  -- cancelled",
         quorum.get_query(args=[second_id]).call()["status_name"])

    expect_refused(
        "resolving a cancelled query",
        lambda: quorum.resolve(args=[second_id]).transact(),
    )

    print(f"\n{'=' * 70}")
    print("  3/3 writes exercised, 7/7 views read.")
    print(f"{'=' * 70}\n")


def test_stance_agreement_is_reproducible(quorum=None):
    """Resolve the same question twice and compare the per-source stances.

    This is the convergence question for this primitive. Snapshot stability was
    the equivalent property for a change watcher; here it is whether validators
    independently reach the same *reading* of each source. If two resolutions
    of the same question disagree about what a source says, corroboration
    counts are noise and nothing downstream can rely on them.
    """
    factory = get_contract_factory("SourceQuorum")
    contract = factory.deploy(args=[])

    stances = []
    for _ in range(2):
        contract.open_query(args=[QUESTION, SOURCES, 2, 3650]).transact()
        query_id = contract.query_count().call()
        contract.resolve(args=[query_id]).transact(**RESOLVE_WAIT)
        findings = contract.get_findings(args=[query_id]).call()
        stances.append([(f["url"], f["stance_name"]) for f in findings])

    print(f"\n  run 1 stances: {stances[0]}")
    print(f"  run 2 stances: {stances[1]}")

    assert stances[0] == stances[1], (
        "the same sources were read differently across two resolutions; "
        "per-source stance is not reproducible"
    )
