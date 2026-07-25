"""Direct-mode tests for SourceQuorum.

The web and model layers are mocked, so these exercise the parts that decide
outcomes: ownership grouping, the quorum floor, abstention, and the reputation
ledger.

The adversarial cases carry the weight. A corroboration primitive that only
works when sources agree is worthless -- what matters is what it does when one
publisher is quoted twice, when a source is unreachable, when the model tries
to resolve on a single cluster, and when the evidence genuinely conflicts.
"""

import json

from conftest import as_address

CONTRACT = "contracts/source_quorum.py"

QUESTION = "Did ACME Corp ship version 3.0 before 2026-08-01?"

GATHER_PROMPT = r"You report what each source says"
JUDGE_PROMPT = r"You decide whether independent sources corroborate"

# Four different registrable domains.
U_ALPHA = "https://alpha-news.com/acme-ships-v3"
U_BETA = "https://beta-wire.org/tech/acme"
U_GAMMA = "https://gamma-times.co.uk/business/acme"
U_DELTA = "https://delta-report.net/acme-v3"

# Same owner as U_ALPHA, different subdomain and path.
U_ALPHA_MIRROR = "https://www.alpha-news.com/amp/acme-ships-v3"


def gather(*entries):
    """Build a round-1 mock. Each entry is (url, reachable, stance, value)."""
    return json.dumps(
        {
            "findings": [
                {
                    "url": url,
                    "reachable": reachable,
                    "stance": stance,
                    "claim_value": value,
                    "excerpt": f"...{value}...",
                }
                for url, reachable, stance, value in entries
            ]
        }
    )


def judge(clusters, status, answer="v3.0 shipped 2026-07-14", confidence="HIGH"):
    return json.dumps(
        {
            "clusters": clusters,
            "status": status,
            "answer": answer,
            "confidence": confidence,
            "reasoning": "test verdict",
        }
    )


def mock_all_web(direct_vm, body="page text"):
    direct_vm.mock_web(r".*", {"status": 200, "body": body})


def opened(contract, urls=None, min_independent=2):
    return contract.open_query(
        QUESTION, urls or [U_ALPHA, U_BETA, U_GAMMA], min_independent, 365
    )


# ---------------------------------------------------------------------------
# Deterministic ownership grouping
#
# This runs entirely outside consensus and is the first line of defence
# against "corroboration" that is really one publisher quoted twice.
# ---------------------------------------------------------------------------


def test_domain_grouping_collapses_subdomains_and_multipart_tlds(
    direct_vm, direct_deploy
):
    contract = direct_deploy(CONTRACT)

    assert contract.domain_of("https://news.bbc.co.uk/story/1") == "bbc.co.uk"
    assert contract.domain_of("https://bbc.co.uk/story/1") == "bbc.co.uk"
    assert contract.domain_of("https://www.alpha-news.com/x") == "alpha-news.com"
    assert contract.domain_of("https://a.b.alpha-news.com/x") == "alpha-news.com"
    assert contract.domain_of("http://example.com:8080/path?q=1") == "example.com"
    assert contract.domain_of("https://site.com.au/news") == "site.com.au"


def test_urls_from_one_publisher_cannot_satisfy_a_quorum(direct_vm, direct_deploy):
    """Ten links to one outlet are one source, and the contract says so upfront.

    Refusing at open time saves the caller a resolution that could only ever
    come back INSUFFICIENT.
    """
    contract = direct_deploy(CONTRACT)

    with direct_vm.expect_revert("distinct domains"):
        contract.open_query(QUESTION, [U_ALPHA, U_ALPHA_MIRROR], 2, 365)


def test_open_query_validates_inputs(direct_vm, direct_deploy):
    contract = direct_deploy(CONTRACT)

    with direct_vm.expect_revert("EXPECTED"):
        contract.open_query("", [U_ALPHA, U_BETA], 2, 365)

    with direct_vm.expect_revert("EXPECTED"):
        contract.open_query(QUESTION, [], 2, 365)

    with direct_vm.expect_revert("EXPECTED"):
        contract.open_query(QUESTION, [U_ALPHA, "ftp://beta.org/x"], 2, 365)

    with direct_vm.expect_revert("EXPECTED"):
        contract.open_query(QUESTION, [U_ALPHA, U_ALPHA], 2, 365)

    # A single source is never corroboration, at any setting.
    with direct_vm.expect_revert("never corroboration"):
        contract.open_query(QUESTION, [U_ALPHA, U_BETA], 1, 365)


def test_open_query_stores_the_question_and_sources(direct_vm, direct_deploy):
    contract = direct_deploy(CONTRACT)
    query_id = opened(contract)

    state = contract.get_query(query_id)
    assert state["status_name"] == "PENDING"
    assert state["source_count"] == 3
    assert state["min_independent"] == 2
    assert contract.get_sources(query_id) == [U_ALPHA, U_BETA, U_GAMMA]
    assert contract.query_count() == 1


# ---------------------------------------------------------------------------
# Resolution
# ---------------------------------------------------------------------------


def test_independent_corroboration_resolves(direct_vm, direct_deploy):
    contract = direct_deploy(CONTRACT)
    query_id = opened(contract)

    mock_all_web(direct_vm)
    direct_vm.mock_llm(
        GATHER_PROMPT,
        gather(
            (U_ALPHA, True, "SUPPORTS", "2026-07-14"),
            (U_BETA, True, "SUPPORTS", "2026-07-14"),
            (U_GAMMA, True, "SUPPORTS", "2026-07-14"),
        ),
    )
    direct_vm.mock_llm(JUDGE_PROMPT, judge([0, 1, 2], "RESOLVED"))
    contract.resolve(query_id)

    verdict = contract.get_verdict(query_id)
    assert verdict["conclusive"] is True
    assert verdict["independent_clusters"] == 3
    assert verdict["confidence"] == 2
    assert "2026-07-14" in verdict["answer"]


def test_syndicated_sources_collapse_to_one_cluster(direct_vm, direct_deploy):
    """Three outlets reprinting one wire report are one source, not three.

    The model does the clustering; the contract enforces the floor. Here all
    three land in cluster 0, so a min_independent of 2 is not met and the
    verdict is downgraded even though the model said RESOLVED.
    """
    contract = direct_deploy(CONTRACT)
    query_id = opened(contract)

    mock_all_web(direct_vm)
    direct_vm.mock_llm(
        GATHER_PROMPT,
        gather(
            (U_ALPHA, True, "SUPPORTS", "2026-07-14"),
            (U_BETA, True, "SUPPORTS", "2026-07-14"),
            (U_GAMMA, True, "SUPPORTS", "2026-07-14"),
        ),
    )
    direct_vm.mock_llm(JUDGE_PROMPT, judge([0, 0, 0], "RESOLVED"))
    contract.resolve(query_id)

    verdict = contract.get_verdict(query_id)
    assert verdict["conclusive"] is False
    assert verdict["status"] == 3, "should downgrade to INSUFFICIENT"
    assert verdict["independent_clusters"] == 1


def test_a_model_cannot_resolve_below_the_quorum_floor(direct_vm, direct_deploy):
    """The contract re-checks the floor after clustering.

    The model proposes; the contract disposes. If a model returns RESOLVED off
    a single positioned cluster, the answer is discarded.
    """
    contract = direct_deploy(CONTRACT)
    query_id = opened(contract, min_independent=3)

    mock_all_web(direct_vm)
    direct_vm.mock_llm(
        GATHER_PROMPT,
        gather(
            (U_ALPHA, True, "SUPPORTS", "2026-07-14"),
            (U_BETA, True, "UNRELATED", ""),
            (U_GAMMA, True, "UNCLEAR", ""),
        ),
    )
    direct_vm.mock_llm(JUDGE_PROMPT, judge([0, 1, 2], "RESOLVED"))
    contract.resolve(query_id)

    verdict = contract.get_verdict(query_id)
    assert verdict["conclusive"] is False
    assert verdict["independent_clusters"] == 1
    assert contract.get_query(query_id)["answer"] == ""


def test_conflicting_sources_return_contradicted_not_a_guess(
    direct_vm, direct_deploy
):
    """Disagreement is reported as disagreement, with the evidence attached."""
    contract = direct_deploy(CONTRACT)
    query_id = opened(contract)

    mock_all_web(direct_vm)
    direct_vm.mock_llm(
        GATHER_PROMPT,
        gather(
            (U_ALPHA, True, "SUPPORTS", "2026-07-14"),
            (U_BETA, True, "REFUTES", "delayed to 2026-09"),
            (U_GAMMA, True, "SUPPORTS", "2026-07-14"),
        ),
    )
    direct_vm.mock_llm(JUDGE_PROMPT, judge([0, 1, 2], "CONTRADICTED", answer=""))
    contract.resolve(query_id)

    verdict = contract.get_verdict(query_id)
    assert verdict["status"] == 2
    assert verdict["conclusive"] is False
    assert verdict["answer"] == ""

    findings = contract.get_findings(query_id)
    assert [f["stance_name"] for f in findings] == ["SUPPORTS", "REFUTES", "SUPPORTS"]


def test_absence_of_evidence_is_not_a_verdict(direct_vm, direct_deploy):
    contract = direct_deploy(CONTRACT)
    query_id = opened(contract)

    mock_all_web(direct_vm)
    direct_vm.mock_llm(
        GATHER_PROMPT,
        gather(
            (U_ALPHA, True, "UNRELATED", ""),
            (U_BETA, True, "UNRELATED", ""),
            (U_GAMMA, True, "UNCLEAR", ""),
        ),
    )
    direct_vm.mock_llm(JUDGE_PROMPT, judge([0, 1, 2], "INSUFFICIENT", answer=""))
    contract.resolve(query_id)

    assert contract.get_verdict(query_id)["status"] == 3


def test_a_query_resolves_only_once(direct_vm, direct_deploy):
    """Others may already have acted on the verdict; it cannot be overwritten."""
    contract = direct_deploy(CONTRACT)
    query_id = opened(contract)

    mock_all_web(direct_vm)
    direct_vm.mock_llm(
        GATHER_PROMPT,
        gather(
            (U_ALPHA, True, "SUPPORTS", "2026-07-14"),
            (U_BETA, True, "SUPPORTS", "2026-07-14"),
            (U_GAMMA, True, "SUPPORTS", "2026-07-14"),
        ),
    )
    direct_vm.mock_llm(JUDGE_PROMPT, judge([0, 1, 2], "RESOLVED"))
    contract.resolve(query_id)

    with direct_vm.expect_revert("already settled"):
        contract.resolve(query_id)


# ---------------------------------------------------------------------------
# Adversarial: sources and models misbehaving
# ---------------------------------------------------------------------------


def test_too_few_reachable_domains_is_unavailable_not_a_verdict(
    direct_vm, direct_deploy
):
    """A dead web is not evidence about the question.

    UNAVAILABLE is deliberately distinct from INSUFFICIENT: one says the
    sources could not be read, the other says they were read and did not
    settle it.
    """
    contract = direct_deploy(CONTRACT)
    query_id = opened(contract)

    mock_all_web(direct_vm)
    direct_vm.mock_llm(
        GATHER_PROMPT,
        gather(
            (U_ALPHA, True, "SUPPORTS", "2026-07-14"),
            (U_BETA, False, "UNCLEAR", ""),
            (U_GAMMA, False, "UNCLEAR", ""),
        ),
    )
    contract.resolve(query_id)

    verdict = contract.get_verdict(query_id)
    assert verdict["status"] == 4
    assert verdict["conclusive"] is False
    assert "reachable" in contract.get_query(query_id)["reasoning"]


def test_an_unreachable_source_gets_no_stance(direct_vm, direct_deploy):
    """A model claiming a stance for a page it could not read is overridden."""
    contract = direct_deploy(CONTRACT)
    query_id = opened(contract, urls=[U_ALPHA, U_BETA, U_GAMMA, U_DELTA])

    mock_all_web(direct_vm)
    direct_vm.mock_llm(
        GATHER_PROMPT,
        gather(
            (U_ALPHA, True, "SUPPORTS", "2026-07-14"),
            (U_BETA, True, "SUPPORTS", "2026-07-14"),
            (U_GAMMA, False, "SUPPORTS", "invented"),
            (U_DELTA, True, "SUPPORTS", "2026-07-14"),
        ),
    )
    direct_vm.mock_llm(JUDGE_PROMPT, judge([0, 1, 2, 3], "RESOLVED"))
    contract.resolve(query_id)

    findings = contract.get_findings(query_id)
    gamma = next(f for f in findings if f["domain"] == "gamma-times.co.uk")
    assert gamma["reachable"] is False
    assert gamma["stance_name"] == "UNCLEAR", "an unreadable page has no stance"


def test_a_finding_for_an_unrequested_url_is_ignored(direct_vm, direct_deploy):
    """Findings are keyed onto the requested list, never trusted from the model.

    Otherwise a model could introduce a source nobody asked for and have it
    counted as corroboration.
    """
    contract = direct_deploy(CONTRACT)
    query_id = opened(contract)

    mock_all_web(direct_vm)
    direct_vm.mock_llm(
        GATHER_PROMPT,
        gather(
            (U_ALPHA, True, "SUPPORTS", "2026-07-14"),
            ("https://attacker.example/fake", True, "SUPPORTS", "fabricated"),
            (U_BETA, True, "SUPPORTS", "2026-07-14"),
            (U_GAMMA, True, "SUPPORTS", "2026-07-14"),
        ),
    )
    direct_vm.mock_llm(JUDGE_PROMPT, judge([0, 1, 2], "RESOLVED"))
    contract.resolve(query_id)

    urls = [f["url"] for f in contract.get_findings(query_id)]
    assert urls == [U_ALPHA, U_BETA, U_GAMMA]
    assert "https://attacker.example/fake" not in urls


def test_a_missing_finding_becomes_unreachable_not_absent(
    direct_vm, direct_deploy
):
    contract = direct_deploy(CONTRACT)
    query_id = opened(contract)

    mock_all_web(direct_vm)
    direct_vm.mock_llm(
        GATHER_PROMPT,
        gather(
            (U_ALPHA, True, "SUPPORTS", "2026-07-14"),
            (U_BETA, True, "SUPPORTS", "2026-07-14"),
        ),
    )
    direct_vm.mock_llm(JUDGE_PROMPT, judge([0, 1, 2], "RESOLVED"))
    contract.resolve(query_id)

    findings = contract.get_findings(query_id)
    assert len(findings) == 3
    assert findings[2]["reachable"] is False


def test_unparseable_gathering_does_not_produce_a_verdict(
    direct_vm, direct_deploy
):
    contract = direct_deploy(CONTRACT)
    query_id = opened(contract)

    mock_all_web(direct_vm)
    direct_vm.mock_llm(GATHER_PROMPT, "I'm sorry, I can't help with that.")
    contract.resolve(query_id)

    assert contract.get_verdict(query_id)["status"] == 4


def test_unparseable_adjudication_falls_back_to_insufficient(
    direct_vm, direct_deploy
):
    contract = direct_deploy(CONTRACT)
    query_id = opened(contract)

    mock_all_web(direct_vm)
    direct_vm.mock_llm(
        GATHER_PROMPT,
        gather(
            (U_ALPHA, True, "SUPPORTS", "2026-07-14"),
            (U_BETA, True, "SUPPORTS", "2026-07-14"),
            (U_GAMMA, True, "SUPPORTS", "2026-07-14"),
        ),
    )
    direct_vm.mock_llm(JUDGE_PROMPT, "not json at all")
    contract.resolve(query_id)

    verdict = contract.get_verdict(query_id)
    assert verdict["conclusive"] is False
    assert verdict["status"] == 3


def test_fenced_json_is_recovered(direct_vm, direct_deploy):
    contract = direct_deploy(CONTRACT)
    query_id = opened(contract)

    mock_all_web(direct_vm)
    direct_vm.mock_llm(
        GATHER_PROMPT,
        "Here you go:\n```json\n"
        + gather(
            (U_ALPHA, True, "SUPPORTS", "2026-07-14"),
            (U_BETA, True, "SUPPORTS", "2026-07-14"),
            (U_GAMMA, True, "SUPPORTS", "2026-07-14"),
        )
        + "\n```",
    )
    direct_vm.mock_llm(JUDGE_PROMPT, judge([0, 1, 2], "RESOLVED"))
    contract.resolve(query_id)

    assert contract.get_verdict(query_id)["conclusive"] is True


def test_an_unknown_status_defaults_to_insufficient(direct_vm, direct_deploy):
    """An unreadable status must never be read as a resolution."""
    contract = direct_deploy(CONTRACT)
    query_id = opened(contract)

    mock_all_web(direct_vm)
    direct_vm.mock_llm(
        GATHER_PROMPT,
        gather(
            (U_ALPHA, True, "SUPPORTS", "2026-07-14"),
            (U_BETA, True, "SUPPORTS", "2026-07-14"),
            (U_GAMMA, True, "SUPPORTS", "2026-07-14"),
        ),
    )
    direct_vm.mock_llm(JUDGE_PROMPT, judge([0, 1, 2], "PROBABLY_TRUE"))
    contract.resolve(query_id)

    assert contract.get_verdict(query_id)["status"] == 3


# ---------------------------------------------------------------------------
# Source reputation
# ---------------------------------------------------------------------------


def test_unknown_domains_start_neutral(direct_vm, direct_deploy):
    contract = direct_deploy(CONTRACT)

    record = contract.get_source("never-seen.com")
    assert record["known"] is False
    assert record["score_bps"] == 5000
    assert record["weight_bps"] == 10000


def test_aligning_with_a_resolved_majority_raises_standing(
    direct_vm, direct_deploy
):
    contract = direct_deploy(CONTRACT)
    query_id = opened(contract)

    mock_all_web(direct_vm)
    direct_vm.mock_llm(
        GATHER_PROMPT,
        gather(
            (U_ALPHA, True, "SUPPORTS", "2026-07-14"),
            (U_BETA, True, "SUPPORTS", "2026-07-14"),
            (U_GAMMA, True, "SUPPORTS", "2026-07-14"),
        ),
    )
    direct_vm.mock_llm(JUDGE_PROMPT, judge([0, 1, 2], "RESOLVED"))
    contract.resolve(query_id)

    record = contract.get_source("alpha-news.com")
    assert record["known"] is True
    assert record["score_bps"] == 5250
    assert record["times_aligned"] == 1
    assert record["times_used"] == 1


def test_standing_alone_against_a_resolved_majority_costs_more_than_agreeing_gains(
    direct_vm, direct_deploy
):
    """Being wrong is penalised harder than being right is rewarded.

    A source that is usually right and occasionally badly wrong should not
    accumulate standing on volume alone.
    """
    contract = direct_deploy(CONTRACT)
    query_id = opened(contract)

    mock_all_web(direct_vm)
    direct_vm.mock_llm(
        GATHER_PROMPT,
        gather(
            (U_ALPHA, True, "SUPPORTS", "2026-07-14"),
            (U_BETA, True, "SUPPORTS", "2026-07-14"),
            (U_GAMMA, True, "REFUTES", "never shipped"),
        ),
    )
    direct_vm.mock_llm(JUDGE_PROMPT, judge([0, 1, 2], "RESOLVED"))
    contract.resolve(query_id)

    aligned = contract.get_source("alpha-news.com")
    minority = contract.get_source("gamma-times.co.uk")

    assert aligned["score_bps"] == 5250
    assert minority["score_bps"] == 4500
    assert minority["times_minority"] == 1
    assert (5000 - minority["score_bps"]) > (aligned["score_bps"] - 5000)


def test_an_inconclusive_query_does_not_punish_anyone(direct_vm, direct_deploy):
    """A contradiction proves nothing about who was right.

    Moving standing here would penalise a well-sourced domain merely for
    appearing alongside a bad one.
    """
    contract = direct_deploy(CONTRACT)
    query_id = opened(contract)

    mock_all_web(direct_vm)
    direct_vm.mock_llm(
        GATHER_PROMPT,
        gather(
            (U_ALPHA, True, "SUPPORTS", "2026-07-14"),
            (U_BETA, True, "REFUTES", "delayed"),
            (U_GAMMA, True, "SUPPORTS", "2026-07-14"),
        ),
    )
    direct_vm.mock_llm(JUDGE_PROMPT, judge([0, 1, 2], "CONTRADICTED", answer=""))
    contract.resolve(query_id)

    for domain in ("alpha-news.com", "beta-wire.org", "gamma-times.co.uk"):
        assert contract.get_source(domain)["score_bps"] == 5000, domain


def test_unreachable_sources_lose_a_little_standing(direct_vm, direct_deploy):
    contract = direct_deploy(CONTRACT)
    query_id = opened(contract, urls=[U_ALPHA, U_BETA, U_GAMMA, U_DELTA])

    mock_all_web(direct_vm)
    direct_vm.mock_llm(
        GATHER_PROMPT,
        gather(
            (U_ALPHA, True, "SUPPORTS", "2026-07-14"),
            (U_BETA, True, "SUPPORTS", "2026-07-14"),
            (U_GAMMA, True, "SUPPORTS", "2026-07-14"),
            (U_DELTA, False, "UNCLEAR", ""),
        ),
    )
    direct_vm.mock_llm(JUDGE_PROMPT, judge([0, 1, 2, 3], "RESOLVED"))
    contract.resolve(query_id)

    record = contract.get_source("delta-report.net")
    assert record["score_bps"] == 4900
    assert record["times_unreachable"] == 1


def test_reputation_tilts_weight_but_is_bounded(direct_vm, direct_deploy):
    """Weight is compressed on purpose.

    A trusted domain must never be able to outvote several independent ones --
    that would rebuild the single-trusted-source problem this contract exists
    to remove.
    """
    contract = direct_deploy(CONTRACT)

    mock_all_web(direct_vm)
    direct_vm.mock_llm(
        GATHER_PROMPT,
        gather(
            (U_ALPHA, True, "SUPPORTS", "2026-07-14"),
            (U_BETA, True, "SUPPORTS", "2026-07-14"),
            (U_GAMMA, True, "SUPPORTS", "2026-07-14"),
        ),
    )
    direct_vm.mock_llm(JUDGE_PROMPT, judge([0, 1, 2], "RESOLVED"))

    for _ in range(6):
        contract.resolve(opened(contract))

    record = contract.get_source("alpha-news.com")
    assert record["score_bps"] == 6500
    assert record["weight_bps"] == 12000, "trusted, but only a 1.2x tilt"
    assert record["weight_bps"] < 20000


# ---------------------------------------------------------------------------
# Access control
# ---------------------------------------------------------------------------


def test_only_the_asker_can_cancel(
    direct_vm, direct_deploy, direct_alice, direct_bob
):
    direct_vm.sender = direct_alice
    contract = direct_deploy(CONTRACT)
    query_id = opened(contract)

    with direct_vm.prank(direct_bob):
        with direct_vm.expect_revert("not the asker"):
            contract.cancel_query(query_id)

    contract.cancel_query(query_id)
    assert contract.get_query(query_id)["status_name"] == "CANCELLED"


def test_a_cancelled_query_cannot_be_resolved(direct_vm, direct_deploy):
    contract = direct_deploy(CONTRACT)
    query_id = opened(contract)
    contract.cancel_query(query_id)

    with direct_vm.expect_revert("already settled"):
        contract.resolve(query_id)


def test_unknown_query_is_rejected(direct_vm, direct_deploy):
    contract = direct_deploy(CONTRACT)
    with direct_vm.expect_revert("EXPECTED"):
        contract.get_query(999)
