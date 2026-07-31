# Response to Review — SourceQuorum

**Review date:** Jul 25, 2026 (original) / Jul 31, 2026 (this follow-up)
**Requested by:** Joaquin
**Resubmitted:** Jul 31, 2026

## Feedback

> The corroboration mechanism is substantive, but the repository and
> submitted Explorer deployment do not yet match on a consequential quorum
> safeguard. The repository deterministically merges same-publisher findings
> before counting independent clusters; the reviewed deployment lacks that
> check and can treat two URLs from one publisher as a quorum. Please provide
> Explorer evidence whose deployed source contains the same-domain cluster
> merge and matches the submitted contract.

## What was wrong

The same-domain cluster merge (`resolve()`, deterministic post-processing
after Round 2 adjudication) was already committed to the repository —
`2698c09 Fix same-domain quorum clustering`, documented in the prior
`more info.md`. But the address the review evidence pointed at,
`0xc9fCE280384A1B3D2CE03d2CB6f6344d36e205A2`, was a stale deployment that
predates that fix. Source and on-chain deployment had drifted apart: the fix
existed in git, not on the address under review.

## Fix

Redeployed the current `main` source — the one containing the merge —
to fresh StudioNet addresses, and generated new live evidence tying the
Explorer address directly to the merge behavior rather than just asserting
the source matches.

## Live proof the deployed contract runs the merge

Opened and resolved a real query on the new deployment with three URLs
spanning two publishers — two pages on `example.com` plus one page on a
different domain (`iana.org`):

```
open_query(
  "Is the example.com domain reserved for use in documentation and examples
   without needing permission?",
  ["https://example.com/", "https://example.com/?ref=alt",
   "https://www.iana.org/help/example-domains"],
  min_independent=2, freshness_days=3650
)
```

`get_findings()` after `resolve()`:

| url | domain | cluster | stance |
|---|---|---|---|
| `https://example.com/` | `example.com` | **0** | SUPPORTS |
| `https://example.com/?ref=alt` | `example.com` | **0** | SUPPORTS |
| `https://www.iana.org/help/example-domains` | `iana.org` | **1** | SUPPORTS |

Both `example.com` findings collapsed to the same cluster id (`0`) despite
being two separate URLs, and `get_verdict()` reports
`independent_clusters: 2` — not 3 — even though three sources were probed.
This is the merge running live on the deployed bytecode, not just present in
the repository.

## Evidence

| | |
|---|---|
| Source | [github.com/ometere123/source_quorum](https://github.com/ometere123/source_quorum) |
| Merge fix commit | `2698c09` Fix same-domain quorum clustering |
| **New SourceQuorum (StudioNet)** | `0x02749e67A31c943f2700C2823E3500bcd2312599` |
| Explorer | [explorer-studio.genlayer.com/address/0x02749e67A31c943f2700C2823E3500bcd2312599](https://explorer-studio.genlayer.com/address/0x02749e67A31c943f2700C2823E3500bcd2312599) |
| Studio import | [studio.genlayer.com/?import-contract=0x02749e67A31c943f2700C2823E3500bcd2312599](https://studio.genlayer.com/?import-contract=0x02749e67A31c943f2700C2823E3500bcd2312599) |
| **New CorroboratedPayout consumer (StudioNet)** | `0xCD39236439Dc6e4d32fec682846E8DB198a5665C` |
| Superseded (mismatched) deployment | `0xc9fCE280384A1B3D2CE03d2CB6f6344d36e205A2` — do not review this address |
| Query demonstrating the merge | query id `1` on the new SourceQuorum address, above |
| Direct tests | 33/33 passing, including `test_same_domain_clusters_are_merged_before_quorum_count` |

## What didn't change

`open_query`'s owner-based quorum floor, `resolve()`'s two-round consensus
flow, source reputation tracking, and `get_verdict()`'s status semantics
(only `RESOLVED` is conclusive) are all unchanged from the version already
reviewed. Only the deployment address is new — the source is identical to
what was previously submitted with the merge fix.
