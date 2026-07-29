# More Information: SourceQuorum Review Fix

Yes, the requested issue has been resolved.

Joaquin flagged that the final quorum check could count two URLs from the same publisher as two independent clusters if the adjudication output assigned them different cluster IDs. The patched contract now deterministically merges same-domain cluster assignments before counting independent clusters.

The relevant fix is in `contracts/source_quorum.py` inside `resolve()`, after Round 2 adjudication and before the final quorum count. The model may propose cluster IDs, but the contract now enforces this rule in deterministic code:

- each source is already mapped to its registrable domain, e.g. `news.bbc.co.uk` and `bbc.co.uk` both become `bbc.co.uk`;
- if two findings have the same registrable domain, they are collapsed to the same canonical cluster;
- only the post-collapse clusters are counted toward `independent_clusters`;
- if `status == RESOLVED` but the collapsed cluster count is below `min_independent`, the verdict is downgraded to `INSUFFICIENT` and the answer is discarded.

Regression coverage was added:

`test_same_domain_clusters_are_merged_before_quorum_count`

That test creates a query with two URLs from the same publisher plus one unrelated source. The mocked adjudication tries to assign the same-publisher URLs to different clusters and return `RESOLVED`. The contract deterministically merges those same-domain clusters, counts only one independent positioned cluster, downgrades the verdict to `INSUFFICIENT`, and stores no answer.

Validation:

- `genvm-lint` clean for `contracts/source_quorum.py`
- `genvm-lint` clean for `examples/corroborated_payout.py`
- `33` direct tests pass

Updated StudioNet deployment:

- SourceQuorum: `0x78B847ea74BA67a487abCD07942Ea5fF8DfC6720`
- Deploy tx: `0xe105bd0715dcab2e444f4de26bd11add08f40c5b1e99933681d9d5475f319de9`
- Explorer: `https://explorer-studio.genlayer.com/address/0x78B847ea74BA67a487abCD07942Ea5fF8DfC6720`

Worked consumer deployment:

- CorroboratedPayout: `0x4257362C8C92F3DFf407dE53526BCc513ABFAd0E`
- Deploy tx: `0x567087a444c203bb608c20dd89e2221c74e78c59c8a409449b0365b1a57f3636`

Live StudioNet evidence on the patched deployment includes:

- `RESOLVED`: query 5 resolved `"Python is a programming language."` with `conclusive: true` and `independent_clusters: 2`.
- `UNAVAILABLE`: query 2 finalized not conclusive because too few independent sources were reachable.
- `CANCELLED`: query 3 was cancelled and then read by the example consumer.
- Consumer-to-primitive write: CorroboratedPayout transaction `0xe20325163e4e33ff0dd304f719ee71c0d5e32f7fc2de7f332db8b0998de7ab2c` triggered SourceQuorum transaction `0x1a6d7730643c23eb36dd6c4206b4b9b30b8f17bc5dcb8e6934cd8620f0541e5a`, and SourceQuorum recorded the consumer contract as the query asker.

GitHub has the matching updated source, tests, README, deployment evidence, and consumer example.
