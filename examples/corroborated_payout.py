# v0.2.18
# { "Depends": "py-genlayer:1jb45aa8ynh2a9c9xn3b7qqh8sm5q93hwfp7jqmwsfhh8jpz09h6" }

from genlayer import *

from dataclasses import dataclass


# ---------------------------------------------------------------------------
# CorroboratedPayout -- a worked example of consuming SourceQuorum.
#
# A payer commits to releasing funds if a real-world event happened. A payee
# claims it did. Neither can be trusted to decide, and the event has no oracle
# feed: "did the vendor announce a security incident", "did the airline cancel
# the route", "did the protocol ship the audit".
#
# The interesting part of this example is what it does NOT do. It performs no
# fetching, writes no prompts, defines no equivalence principle, and holds no
# opinion about news sources. It reads two fields off a verdict.
#
# It is also deliberately PULL-based. SourceQuorum answers a question at a
# point in time rather than emitting a stream, so a consumer resolves and then
# reads, instead of registering a callback.
#
# Deploy order:
#   1. deploy SourceQuorum
#   2. deploy CorroboratedPayout(quorum_address, payee)
#   3. quorum.open_query(question, urls, min_independent) -> query_id
#   4. payout.arm(query_id)  -- payer only, and only before a verdict exists
#   5. anyone calls quorum.resolve(query_id)
#   6. anyone calls settle()
# ---------------------------------------------------------------------------


ERR_EXPECTED = "EXPECTED"

# Only a fully corroborated answer may move money. Everything else -- a
# contradiction, too few independent clusters, an unreachable web -- leaves the
# escrow exactly where it was.
STATUS_RESOLVED = 1

# A payout is a decision worth being sure about, so a bare quorum is not
# enough: the verdict must also carry more than the lowest confidence band.
MIN_CONFIDENCE = 1


@gl.contract_interface
class ISourceQuorum:
    class View:
        def get_verdict(self, query_id: u256) -> dict: ...
        def get_query(self, query_id: u256) -> dict: ...

    class Write:
        def open_query(
            self,
            question: str,
            urls: list,
            min_independent: int,
            freshness_days: int,
        ) -> u256: ...


@gl.evm.contract_interface
class _Payee:
    """Recipient of the escrow.

    An EOA lives on the chain layer, so paying one is an *external* message and
    must go through an EVM interface -- `gl.get_contract_at` is for Intelligent
    Contracts only. External messages always execute on finalisation.
    """

    class View:
        pass

    class Write:
        pass


@allow_storage
@dataclass
class Outcome:
    settled: bool
    paid: bool
    status: u8
    clusters: u32
    answer: str


class PayoutArmed(gl.Event):
    def __init__(self, query_id: u256, /, **blob): ...


class PayoutSettled(gl.Event):
    def __init__(self, paid: bool, status: u8, /, **blob): ...


class CorroboratedPayout(gl.Contract):
    quorum: Address
    payer: Address
    payee: Address
    query_id: u256
    armed: bool
    outcome: Outcome

    def __init__(self, quorum: Address, payee: Address):
        self.quorum = quorum if isinstance(quorum, Address) else Address(quorum)
        self.payee = payee if isinstance(payee, Address) else Address(payee)
        self.payer = gl.message.sender_address
        self.query_id = u256(0)
        self.armed = False

    @gl.public.write.payable
    def fund(self) -> None:
        """Deposit the escrowed amount. Payer only, before arming.

        `gl.message.value` is only readable inside a payable method, and a
        contract with no payable entry point can never hold a balance -- so an
        escrow example without this is a story, not an escrow.
        """
        if gl.message.sender_address != self.payer:
            raise gl.vm.UserError(f"{ERR_EXPECTED}: caller is not the payer")
        if gl.message.value == u256(0):
            raise gl.vm.UserError(f"{ERR_EXPECTED}: send a non-zero amount")
        if bool(self.outcome.settled):
            raise gl.vm.UserError(f"{ERR_EXPECTED}: already settled")

    # There is deliberately no __receive__ here. The SDK documents
    # `@gl.public.write.payable def __receive__(self)` for accepting a bare
    # value transfer with no method named, but genvm-linter 0.2.18 rejects it
    # outright -- "public method names should not start with `__`" -- and lint
    # is a hard gate. The consequence is that a plain transfer to this contract
    # errors: funding must go through fund(). That is arguably the safer
    # behaviour for an escrow anyway, since an accidental transfer bounces
    # instead of silently joining the pot.

    @gl.public.write
    def arm(self, query_id: u256) -> None:
        """Bind this escrow to a query already opened on the quorum contract.

        Opening the query is deliberately *not* done here. Because it happens
        on SourceQuorum first, anyone can inspect the exact question and the
        exact source list before agreeing to be the payee -- the escrow never
        has to be trusted to have registered sensible sources.

        Binding is also refused once the query has a verdict, so a payer
        cannot read the answer and only then decide to commit.
        """

        if gl.message.sender_address != self.payer:
            raise gl.vm.UserError(f"{ERR_EXPECTED}: caller is not the payer")
        if self.armed:
            raise gl.vm.UserError(f"{ERR_EXPECTED}: already armed")

        # Confirm the query exists and has not already been settled, so the
        # payer cannot bind to a verdict they have already read.
        state = ISourceQuorum(self.quorum).view().get_query(query_id)
        if int(state["status"]) != 0:
            raise gl.vm.UserError(
                f"{ERR_EXPECTED}: query {query_id} is already settled; "
                "bind before the answer is known"
            )

        self.query_id = query_id
        self.armed = True
        PayoutArmed(query_id, question=str(state["question"])).emit()

    @gl.public.write
    def settle(self) -> None:
        """Read the verdict and release, or don't. Permissionless.

        The entire integration is the four lines that read `conclusive`,
        `status`, `confidence` and `independent_clusters`.
        """

        if not self.armed:
            raise gl.vm.UserError(f"{ERR_EXPECTED}: not armed")
        if bool(self.outcome.settled):
            raise gl.vm.UserError(f"{ERR_EXPECTED}: already settled")

        verdict = ISourceQuorum(self.quorum).view().get_verdict(self.query_id)

        status = int(verdict["status"])
        conclusive = bool(verdict["conclusive"])
        confidence = int(verdict["confidence"])

        if status == 0:
            raise gl.vm.UserError(
                f"{ERR_EXPECTED}: query is still pending; resolve it first"
            )

        # Silence, contradiction and an unreachable web all mean the same thing
        # here: not proven. Funds stay put. The escrow never guesses, and never
        # reads "we could not tell" as "it did not happen".
        paid = conclusive and confidence >= MIN_CONFIDENCE

        # State is written BEFORE any value leaves, so a re-entrant or
        # duplicated call finds `settled` already true and stops.
        self.outcome.settled = True
        self.outcome.paid = paid
        self.outcome.status = u8(status)
        self.outcome.clusters = u32(int(verdict["independent_clusters"]))
        self.outcome.answer = str(verdict["answer"])

        # Where the funds rest in each terminal state:
        #   corroborated + confident -> payee
        #   anything else            -> back to the payer, never stranded
        amount = self.balance
        if amount > u256(0):
            recipient = self.payee if paid else self.payer
            # Keyword-only, and on='finalized' -- an external message cannot be
            # emitted on acceptance, and value that moves on an appealable
            # result cannot be recalled.
            _Payee(recipient).emit_transfer(value=amount)

        PayoutSettled(paid, u8(status), clusters=int(self.outcome.clusters)).emit()

    @gl.public.view
    def get_outcome(self) -> dict:
        return {
            "armed": bool(self.armed),
            "query_id": int(self.query_id),
            "settled": bool(self.outcome.settled),
            "paid": bool(self.outcome.paid),
            "status": int(self.outcome.status),
            "clusters": int(self.outcome.clusters),
            "answer": str(self.outcome.answer),
        }
