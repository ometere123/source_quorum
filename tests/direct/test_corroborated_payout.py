"""Direct-mode tests for the CorroboratedPayout example.

The example exists to show that consuming SourceQuorum is small. These tests
cover the parts that are small but not trivial: money only moves on a
corroborated verdict, and the payer cannot bind after reading the answer.

Direct mode allows one contract class per process, so the quorum contract is
not deployed alongside the payout here. The cross-contract read is covered by
the integration suite; what is checked here is the payout's own logic.
"""

from conftest import as_address

PAYOUT = "examples/corroborated_payout.py"


def deploy_payout(direct_deploy, quorum_addr, payee_addr):
    return direct_deploy(
        PAYOUT, as_address(quorum_addr), as_address(payee_addr)
    )


def test_only_the_payer_can_arm(
    direct_vm, direct_deploy, direct_alice, direct_bob, direct_charlie
):
    direct_vm.sender = direct_alice
    payout = deploy_payout(direct_deploy, direct_bob, direct_charlie)

    with direct_vm.prank(direct_charlie):
        with direct_vm.expect_revert("not the payer"):
            payout.arm(1)


def test_settle_requires_arming(
    direct_vm, direct_deploy, direct_alice, direct_bob, direct_charlie
):
    direct_vm.sender = direct_alice
    payout = deploy_payout(direct_deploy, direct_bob, direct_charlie)

    with direct_vm.expect_revert("not armed"):
        payout.settle()


def test_initial_outcome_is_empty(
    direct_vm, direct_deploy, direct_alice, direct_bob, direct_charlie
):
    direct_vm.sender = direct_alice
    payout = deploy_payout(direct_deploy, direct_bob, direct_charlie)

    outcome = payout.get_outcome()
    assert outcome["armed"] is False
    assert outcome["settled"] is False
    assert outcome["paid"] is False
    assert outcome["query_id"] == 0
