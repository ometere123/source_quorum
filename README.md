# SourceQuorum

**A reusable GenLayer primitive that answers factual questions only when *independent* sources corroborate — and refuses to answer when they don't.**

`SourceQuorum` is infrastructure, not an application. It is what a prediction market, insurance trigger, bounty payout or grant verifier calls instead of hand-rolling its own oracle logic.

---

## The problem

Every contract that settles on a real-world fact has to answer the same question: *who says so, and why should we believe them?*

The usual answer is one source. Ask a model to read a page, take what it says, settle. That fails in three ways that matter:

**One source is not evidence.** A single page can be wrong, stale, or written by the party who benefits from the outcome.

**Counting sources is not corroboration.** Ask five news sites about a company announcement and you will often get five copies of one press release. They agree perfectly and prove nothing. Worse, `bbc.co.uk` and `news.bbc.co.uk` look like two publishers to any naive counter.

**Being forced to answer is the real bug.** Most oracles must produce a value. When the evidence is thin or conflicting, they guess — and downstream logic cannot tell a corroborated fact from a coin flip.

## What this does instead

```
open_query("Did ACME ship v3.0 before 2026-08-01?", [4 urls], min_independent=2)
resolve(query_id)
   → RESOLVED       3 independent clusters agree     ← the only status that settles
   → CONTRADICTED   independent clusters disagree    ← evidence attached
   → INSUFFICIENT   fewer than 2 clusters took a position
   → UNAVAILABLE    too little of the web could be read
```

**Three of the four terminal states are ways of saying "we do not know."** That is deliberate. A contract that pays out on `RESOLVED` and does nothing otherwise is safe by construction.

---

## Why this needs GenLayer

### The trust problem, stated precisely

Two parties are about to settle money on a fact that is public but unstructured. Neither can be the one who reads the web and reports back — whoever holds that job decides the outcome.

Delete GenLayer and see what survives:

| Approach | What breaks |
|---|---|
| **Off-chain resolver** | The operator picks the sources, reads them, and decides what counts as agreement. Every party must trust them. That is the trust assumption the escrow existed to remove. |
| **Price oracle / Chainlink** | There is no numeric feed for "did this company announce a breach". The input is prose. |
| **Optimistic oracle + human dispute** | Works, but costs days and a bond per question. |
| **One LLM call, on-chain or off** | Someone still has to be trusted to have run it honestly. And a single reading has no notion of corroboration at all. |
| **Multisig of reporters** | You have re-created a trusted committee, with the same collusion surface. |

The property only GenLayer provides: **N independent validators each fetch every source themselves and each form their own reading, and the transaction only lands if their readings agree in substance.** No node is privileged, no party authors the answer, and disagreement is visible rather than silently resolved by whoever was asked.

Note what that buys beyond "the fetch happened". A single reader — human or model — has no way to distinguish *"this page says X"* from *"I read this page as saying X"*. Corroboration across independent readers is what collapses that gap, and the equivalence principle is what forces the readers to be independent rather than one reader repeated.

### Why the reputation ledger must be on-chain

This is the part of the design that is hardest to build anywhere else, and it is worth separating from the resolution logic.

`SourceQuorum` accumulates a track record for every registrable domain it has ever consulted. That ledger has four properties at once:

1. **Nobody curates it.** There is no admin method, no allowlist, no owner. Scores move only as a mechanical consequence of past query outcomes.
2. **Nobody can edit it.** Not the contract deployer, not a query asker, not a source.
3. **Everybody shares it.** A query opened by one contract improves the ledger for every other contract using the same deployment.
4. **It is auditable back to its inputs.** Every score movement traces to a specific resolved query with its findings still stored.

Off-chain, you can have at most three of those. The moment a ledger has an operator, "nobody can edit it" becomes "we promise not to", and a source-reputation list with an owner is a censorship surface — whoever holds the pen decides which publishers count.

This is also what makes the contract *infrastructure* rather than a library. A library is equally useful to its first and thousandth user. **This gets more useful the more the ecosystem uses it**, because the ledger deepens. That network effect only exists if the state is shared and trustless, which is a blockchain-shaped requirement, not an application one.

### The test that matters

The honest check on whether consensus is decorative: **does the output move money?**

In [`examples/corroborated_payout.py`](examples/corroborated_payout.py) an escrow releases on `conclusive == true`. If a single party could author that verdict — pick the sources, read them, decide what counts as agreement — the escrow is worth nothing. The consensus is not garnish on an LLM call; it is the only reason the escrow is safe to enter.

### Why it is not the patterns that get rejected

| Anti-pattern | Why this is not that |
|---|---|
| *"An AI app with GenLayer attached"* | The output is not advice or a summary. It is a typed verdict — a status enum, an independent-cluster count, a banded confidence — consumed programmatically. In `examples/corroborated_payout.py` it releases an escrow. |
| *"A validator that only checks output format"* | Round 1's principle requires validators to agree on the **stance of every source** and on the substance of each claimed fact. Round 2's requires the same status, the same cluster **structure**, and the same confidence band. Valid JSON with a different verdict fails consensus. |
| *"Judging facts from user-submitted text"* | No fact is ever accepted from a caller. Callers supply a question and URLs; every stance recorded was read by the contract from the live page inside a consensus block. |
| *"A thin LLM wrapper"* | The model reports what sources say and which are syndicated. It never decides the outcome — the quorum floor is re-checked in deterministic code after clustering, and a model returning `RESOLVED` off one cluster is overruled. |

---

## Why each non-deterministic call is non-deterministic

Only **2 of the 3** write methods enter consensus, and `open_query` — all validation, ownership grouping and the quorum pre-check — is entirely deterministic. There are exactly **three** non-deterministic operations:

| Call | Where | Why it cannot be deterministic |
|---|---|---|
| `gl.nondet.web.render(url)` | round 1, once per source | Network I/O against several independent hosts. There is no deterministic way for a contract to learn what a page says; the alternative is being told and trusting the teller. |
| `exec_prompt` (gather) | round 1 | Deciding whether a page *asserts*, *denies* or *merely mentions* a claim is language understanding. A parser can find a `<div>`; it cannot tell "shipped on the 14th" from "will ship by the 14th". |
| `exec_prompt` (adjudicate) | round 2 | Recognising that three outlets are reprinting one wire report requires reading them. There is no total function from a set of documents to an independence graph. |

### What is deliberately **not** non-deterministic

Every operation that decides an outcome:

- **Ownership grouping.** `registrable_domain()` reduces a URL to its owner using an explicit public-suffix table, so `news.bbc.co.uk` and `bbc.co.uk` are one publisher. Pure string handling, identical on every node.
- **The quorum floor, checked twice.** Once at `open_query` (refusing a source list that cannot possibly satisfy the minimum) and again *after* clustering. **The model proposes the clustering; the contract decides whether it clears the bar.**
- **Reputation.** Weights, score movement and the trusted/suspect thresholds are integer arithmetic.
- **The reachability floor.** Whether enough distinct domains answered at all.
- **All sanitisation.** Stance normalisation, confidence clamping, JSON recovery, keying findings back onto the requested URL list.

The shape to notice: **the model is asked what sources say and which are independent — never what the contract should do.**

### Why the two rounds are separate

Fetching-and-reading and clustering-and-ruling could have been one prompt. Splitting them is deliberate:

- **Independent readings must not contaminate each other.** Round 1's prompt explicitly forbids letting one source's content influence how another is read. If the same call also decided the verdict, the model would be reading each source already knowing what answer it was building toward — which is exactly the bias corroboration exists to remove.
- **The two rounds need different equivalence principles.** Round 1's asks whether validators agree on *what each source says*. Round 2's asks whether they agree on *the verdict and the independence structure*. Collapsing them would force one principle to do both jobs badly.
- **A deterministic gate sits between them.** The reachability floor runs on round 1's output. When too few distinct domains answered, round 2 never executes — a doomed query costs one consensus round instead of two. This is measurable: the Premier League query below short-circuits exactly here.

### Ordering discipline

Every deterministic guard runs before the expensive work, and the expensive work is bracketed by deterministic checks on both sides:

```
open_query   all validation, ownership grouping, quorum pre-check   ← no consensus at all
   ▼
resolve      guard: exists, still pending                          ← deterministic
             ROUND 1                                               ← consensus
             gate: reachable distinct domains >= min_independent   ← deterministic
             ROUND 2                                               ← consensus
             re-check: independent clusters >= min_independent      ← deterministic
             reputation ledger update                              ← deterministic
```

A caller who supplies an impossible source list never reaches a consensus round at all — `open_query` refuses it. A query whose sources are unreachable pays for one round, not two. And a model that returns `RESOLVED` off too few clusters is overruled *after* it has spoken.

---

## How it works

```
open_query(question, urls, min_independent)        ← deterministic, no consensus
   │  validate urls, group by registrable domain,
   │  refuse if distinct owners < min_independent
   ▼
resolve(query_id)
   │
   ├─ ROUND 1 (nondet: web × N + LLM)
   │     fetch every source, report each one's stance independently
   │     EP: same reachability and same stance for every source
   │
   ├─ DETERMINISTIC
   │     map each url → registrable domain → reputation weight
   │     if reachable distinct domains < min_independent → UNAVAILABLE
   │
   ├─ ROUND 2 (nondet: LLM)
   │     cluster syndicated/derivative sources, then rule
   │     EP: same status, same cluster structure, same confidence band
   │
   └─ DETERMINISTIC
         re-check the quorum floor against the clusters
         downgrade RESOLVED → INSUFFICIENT if it does not hold
         update the source reputation ledger
```

### Independence, in two layers

**Layer 1 — deterministic ownership.** Two URLs under one registrable domain are one publisher, whatever they look like. This is why `open_query` refuses a list of ten links to one outlet with a `min_independent` of two: it could only ever come back `INSUFFICIENT`.

**Layer 2 — syndication clustering.** The hard case is four genuinely different domains all reprinting one agency report. Round 2 groups sources that are not independent evidence: the same wire story, one source citing another in the list, near-identical distinctive phrasing. **Only clusters are counted, never raw sources.**

### The source reputation ledger

Every registrable domain accrues a track record derived purely from past outcomes. Nobody curates it and no privileged party can edit it.

| | |
|---|---|
| Start | 5000 bps, neutral |
| Aligned with a corroborated majority | +250 |
| Stood alone against one | **−500** |
| Could not be fetched | −100 |
| Inconclusive query | **no movement at all** |

Two design choices worth stating:

**Being wrong costs more than being right gains.** A source that is usually right and occasionally badly wrong should not accumulate standing on volume.

**A contradicted query moves nothing.** It proves nothing about who was right, and penalising everyone present would punish a good source merely for appearing alongside a bad one.

**Weight is compressed on purpose** — 0.6× / 1.0× / 1.2×. Reputation tilts a close call; it can never let one well-regarded domain outvote several independent ones. That would rebuild the single-trusted-source problem this contract exists to remove.

### Equivalence principles

Neither round could use `strict_eq` — validators fetching the same pages moments apart legitimately receive different bytes.

**Round 1** — validators must agree, for every source, on whether it was reachable and what stance it takes. Wording of excerpts and claim values is irrelevant; a different date, number or name is not.

**Round 2** — validators must agree on the status, on the cluster *structure* (which sources are grouped together, and how many distinct clusters), and on the confidence band. Cluster numbering and reasoning prose are ignored.

---

## Safety properties

**A model cannot talk its way past the quorum.** The floor is re-checked in deterministic code after clustering. A `RESOLVED` verdict with fewer independent clusters than required is downgraded to `INSUFFICIENT` and its answer discarded.

**An unreadable page has no stance.** If a source failed to fetch, its stance is forced to `UNCLEAR` no matter what the model reports.

**Findings are keyed to the requested sources.** A model that drops, reorders or invents a source cannot change which sources were consulted. An invented URL is discarded; a missing one becomes unreachable.

**An unreachable web is not a verdict.** `UNAVAILABLE` is deliberately distinct from `INSUFFICIENT`: one says the sources could not be read, the other says they were read and did not settle it.

**Nothing defaults upward.** An unreadable stance is `UNCLEAR`, an unreadable status is `INSUFFICIENT`, an unreadable confidence is `LOW`.

**A query resolves once.** Re-resolution is refused rather than allowed to overwrite a verdict others may already have acted on.

**Source text is evidence, never instruction.** Both prompts explicitly refuse to act on instructions found inside fetched content.

---

## Why this is reusable

The falsifiable version: **a consumer reads two fields.**

```python
verdict = ISourceQuorum(self.quorum).view().get_verdict(self.query_id)
if verdict["conclusive"] and verdict["confidence"] >= 1:
    ...   # settle
# anything else: not proven. Funds stay put.
```

[`examples/corroborated_payout.py`](examples/corroborated_payout.py) is a complete worked consumer — an escrow that releases only on a corroborated verdict. It performs **no fetching, writes no prompts, defines no equivalence principle, and holds no opinion about news sources.**

What a consumer never has to learn: how to write an equivalence principle for a multi-source fetch, why counting sources is not counting evidence, how to tell a syndicated reprint from independent reporting, or what to do when the web is down.

| Property | Why it matters for reuse |
|---|---|
| **Zero domain assumptions** | The question is a string parameter. Nothing about companies, outages, shipping or elections appears in the source. |
| **One deployment, shared reputation** | Every query improves the source ledger for everyone. The primitive gets *more* valuable the more the ecosystem uses it — the property that makes something infrastructure rather than a library. |
| **Pull-based** | A quorum answer is a point-in-time fact, so consumers resolve and then read. No callback registration, no delivery assumptions. |
| **Binding before answering** | The question and its sources are fixed at `open_query`, before anyone knows the answer. A payer who could pick sources afterwards would pick the ones that suit them. |
| **Safe default** | `conclusive` is the single flag to branch on, and it is false for every non-resolution. |

### The integration surface, in full

There is no SDK to learn. A consuming contract needs exactly this:

```python
@gl.contract_interface
class ISourceQuorum:
    class View:
        def get_verdict(self, query_id: u256) -> dict: ...
    class Write:
        def open_query(self, question: str, urls: list,
                       min_independent: int, freshness_days: int) -> u256: ...
        def resolve(self, query_id: u256) -> None: ...
```

And the verdict is five fields, of which most consumers use two:

```json
{"status": 1, "conclusive": true, "answer": "Spain",
 "confidence": 2, "independent_clusters": 3}
```

`conclusive` is deliberately redundant with `status == 1`. It exists so the correct integration is the *shortest* one to write — a consumer that branches on `conclusive` cannot accidentally treat `CONTRADICTED` as a negative answer, which is the mistake a raw status enum invites.

### Who would use it

| Use case | The question |
|---|---|
| Prediction market resolution | "Did X happen before date D?" |
| Parametric insurance | "Did provider P declare an outage on date D?" |
| Bounty / grant milestones | "Was deliverable D published by team T?" |
| Delisting & depeg detection | "Did exchange E announce a delisting of token T?" |
| KYB / entity status | "Is company C currently registered and in good standing?" |
| Conditional DAO proposals | "Did partner P ship the integration they committed to?" |

They differ only in the question string and the source list. **No contract change, no redeploy, no new equivalence principle.** That is the operational meaning of "reusable" — and it is why the same deployment serving all six is more valuable than six deployments serving one each.

### What reuse would look like if this were *not* a primitive

Worth stating the counterfactual, because "reusable" is easy to assert. Without this contract, every one of the six rows above independently implements:

- a multi-source fetch inside a consensus block, with per-source failure handling
- an equivalence principle that tolerates byte differences but not substantive ones
- some notion of "enough sources", almost always by counting URLs rather than owners
- a decision about what to do when sources conflict — usually "take the majority", which is wrong when the majority is one press release
- defensive parsing of model output

Five of those five are hard to get right and invisible when got wrong. The sixth thing they would each *not* build is a shared source track record, because it is worthless to a single application.

### The honest limits

- **Not for high-frequency data.** Two consensus rounds plus N live fetches is the wrong tool for anything that moves per-block. Use a price feed.
- **Quality of sources is the caller's job.** The contract enforces *independence*, not *competence*. Four independent conspiracy blogs are four independent clusters and will resolve. Reputation mitigates this over many queries; it does nothing on query one. **This is the sharpest limitation and it is structural** — a contract cannot know which publishers are serious without someone telling it, and "someone telling it" is the trusted party the design removes everywhere else.
- **Syndication detection is a judgement.** It works well on obvious reprints and is weaker on a story rewritten from the same press release with no shared phrasing.
- **Permalinks age better than index pages.** The World Cup run below used a BBC section front; section fronts change, so a rerun months later may read differently. Callers should prefer stable URLs.
- **`resolve` is retryable, not guaranteed first-attempt.** An observation round can return `UNDETERMINED`, writing nothing.
- **Max 8 sources per query**, capped for cost.

---

## API

### Writes

| Method | |
|---|---|
| `open_query(question, urls, min_independent=2, freshness_days=365) -> u256` | Register a question and its sources. **Deterministic** — no consensus round. Refuses a source list that cannot satisfy the minimum. |
| `resolve(query_id)` | Consult the sources and rule. **Permissionless** — anyone may pay. Resolves exactly once. |
| `cancel_query(query_id)` | Withdraw an unresolved query. Asker only, before a verdict. |

### Views

| Method | |
|---|---|
| `get_verdict(query_id)` | The minimal consumer shape: `status`, `conclusive`, `answer`, `confidence`, `independent_clusters`. |
| `get_query(query_id)` | Full state including `status_name` and `reasoning`. |
| `get_findings(query_id)` | Per-source stance, cluster, excerpt and applied weight. |
| `get_sources(query_id)` | The consulted URLs. |
| `get_source(domain)` | Track record for a registrable domain. Unknown domains report neutral rather than erroring. |
| `domain_of(url)` | The ownership grouping, exposed so callers can pick sources sensibly. |
| `query_count()` | |

### Events

`QueryOpened` · `QueryResolved` · `SourceReputationChanged`

---

## Development

```bash
pip install genvm-linter genlayer-test
```

```bash
genvm-lint check contracts/source_quorum.py --json
```

```bash
pytest tests/direct/ -v
```

```bash
gltest tests/integration/ -v -s --network studionet
```

### Test coverage

30 direct tests. The adversarial cases are the point.

| Area | Cases |
|---|---|
| Ownership grouping | subdomains, multi-part TLDs, ports, one publisher cannot satisfy a quorum |
| Validation | empty question, empty/duplicate/non-http urls, `min_independent=1` refused |
| Resolution | independent corroboration resolves; syndicated sources collapse to one cluster; a model cannot resolve below the floor |
| Abstention | contradiction returns `CONTRADICTED` with evidence; absence of evidence is not a verdict |
| Sources misbehaving | too few reachable domains → `UNAVAILABLE`; an unreachable source gets no stance |
| Model misbehaving | invented source discarded, missing finding becomes unreachable, fenced JSON recovered, unparseable rounds fail safe, unknown status → `INSUFFICIENT` |
| Reputation | neutral start, aligned gain, minority penalty larger than gain, inconclusive queries move nothing, unreachable penalty, weight stays bounded |
| Access control | asker-only cancel, settled queries immutable |

### Notes on the environment

Two host-level workarounds live in `tests/conftest.py`; neither affects contract behaviour. On Windows, gltest's direct-mode loader unlinks a temp file still bound to fd 0 — tolerated and swept at exit. The SDK also permits one `gl.Contract` subclass per process, so the registry is reset between tests; without it a multi-contract suite passes or fails on file ordering.

`resolve()` runs two consensus rounds plus N live fetches and comfortably exceeds gltest's default 150s wait. The integration tests pass `wait_retries` explicitly. This is latency, not failure.

## Layout

```
contracts/source_quorum.py        the primitive
examples/corroborated_payout.py   worked consumer
tests/direct/                     in-memory tests, mocked web and model
tests/integration/                consensus tests against a real node
tests/conftest.py                 host workarounds only
```

## Status

Lint clean. **30 direct tests pass. 2 integration tests pass against real StudioNet consensus**, including a full-surface run driving all 3 writes and reading all 7 views.

### Deployed

| | |
|---|---|
| Network | StudioNet (chain id 61999) |
| Address | `0xc9fCE280384A1B3D2CE03d2CB6f6344d36e205A2` |
| Studio | https://studio.genlayer.com/?import-contract=0xc9fCE280384A1B3D2CE03d2CB6f6344d36e205A2 |
| Explorer | https://explorer-studio.genlayer.com/address/0xc9fCE280384A1B3D2CE03d2CB6f6344d36e205A2 |

All 3 write methods have been executed against this deployment, so the explorer shows the complete surface: `open_query` ×3, `resolve` ×2, `cancel_query`.

### Measured on live consensus

**A real-world fact, three independently-owned sources.**

Question: *"Which national team won the 2026 FIFA World Cup?"*
Sources: `en.wikipedia.org`, `bbc.com`, `apnews.com`.

```json
{"status_name": "RESOLVED", "answer": "Spain",
 "independent_clusters": 3, "confidence": 2}
```

Per-source findings, with excerpts the validators pulled from the live pages:

| domain | stance | claim | cluster | excerpt |
|---|---|---|---|---|
| `wikipedia.org` | SUPPORTS | Spain | 0 | "…concluded on July 19 with Spain winning the championship for the second time." |
| `bbc.com` | SUPPORTS | Spain | 1 | "'A date with history and we got there first' - Spain react to World Cup win." |
| `apnews.com` | SUPPORTS | Spain | 2 | "Spain wins the World Cup by beating Argentina 1-0 on Ferran Torres' goal in extra time" |

Note what the clustering got right: three *materially different* reports of the
same event — an encyclopedia summary, a reaction piece and a match report —
were correctly held to be three independent clusters rather than one
syndicated story. The adjudicator's own reasoning:

> "Wikipedia, BBC, and AP News are separate publishers and there is no
> indication here that the BBC and AP items are reprints of the same wire copy
> or that either merely reproduces the other."

All three domains moved 5000 → 5250 bps in the reputation ledger.

**Refusing to answer when it has the answer.**

The property worth testing hardest is abstention. Question: *"Which club won the 2025-26 English Premier League title?"* over three sources, two of which are dead domains.

```json
{"status_name": "UNAVAILABLE", "answer": "", "confidence": 0,
 "independent_clusters": 0,
 "reasoning": "only 1 distinct domains were reachable, below min_independent=2"}
```

| domain | reachable | stance | claim |
|---|---|---|---|
| `this-domain-does-not-exist-91af.example` | false | UNCLEAR | — |
| `wikipedia.org` | **true** | **SUPPORTS** | **Arsenal** |
| `nonexistent-sports-site-7731.test` | false | UNCLEAR | — |

**Wikipedia answered the question — and the contract discarded it.** One reachable domain is not corroboration, so the verdict is `UNAVAILABLE` with an empty answer rather than a confident "Arsenal" backed by a single page. This is the whole design in one transaction: an oracle that must produce a value would have returned Arsenal here.

Note also what did *not* happen. Round 2 never ran: the deterministic reachability floor short-circuited before adjudication, so a doomed query costs one consensus round rather than two. The two dead domains each took the −100 bps unreachable penalty (5000 → 4900), while `wikipedia.org` was neither rewarded nor punished — an inconclusive query moves nothing.

**A second question, on stable reference pages.**

Question: *"Is the example.com domain reserved for use in documentation and examples without needing permission?"*
Sources: `example.com` and `iana.org` — two distinct owners.

```json
{"status_name": "RESOLVED", "independent_clusters": 2, "confidence": 2,
 "answer": "Yes, the example.com domain is reserved for use in documentation
            and examples without needing permission."}
```

Per-source findings, with excerpts the validators actually pulled from the live pages:

| domain | stance | cluster | excerpt |
|---|---|---|---|
| `example.com` | SUPPORTS | 0 | "This domain is for use in documentation examples without needing permission." |
| `iana.org` | SUPPORTS | 1 | "These domains may be used as illustrative examples in documents without prior coordination with us." |

Both domains moved 5000 → 5250 bps in the reputation ledger, `times_aligned = 1`.

**Stance reproducibility.** The same question resolved twice, independently, produced identical per-source stances. This is the convergence property the primitive rests on: if two resolutions disagreed about what a source says, corroboration counts would be noise.

```
run 1: [(example.com, SUPPORTS), (iana.org, SUPPORTS)]
run 2: [(example.com, SUPPORTS), (iana.org, SUPPORTS)]
```

### Observed consensus behaviour

Individual validator votes routinely include `DISAGREE` and `IDLE`; transactions still reach `ACCEPTED` on quorum. An observation round can also return `UNDETERMINED`, in which case **nothing is written** and the call must be retried. Treat `resolve` as retryable. `open_query` and `cancel_query` are deterministic and do not have this behaviour.

### Roadmap

- Staleness enforced from published dates rather than described in the prompt
- Reputation decay so an old track record does not outweigh recent behaviour
- Optional per-query source allowlists for domains a caller pre-trusts
