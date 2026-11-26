# Step 2.2 — RAG_LEVEL comparison (basic / standard / full)

Generated 2026-11-26 13:28:21 · model `Qwen3.5-4B-Q4_K_M.gguf` · hardware `gpu_full(cuda)`


## Aggregate

| metric | basic | standard | full |
|---|---|---|---|
| prompts measured | 4 | 4 | 4 |
| mean latency (s) | 45.79 | 57.4 | 47.88 |
| ground-truth coverage | 18.5/29 (64%) | 21.5/29 (74%) | 18.5/29 (64%) |
| citation hit yes/partial/no | 2/2/0 | 3/1/0 | 2/2/0 |
| tests with citation error | 3 | 4 | 4 |

## Per-prompt latency (s)

| slot | prompt | basic | standard | full |
|---|---|---|---|---|
| 1 | T02 / B01 / F01 — firewall, general | 31.02 | 43.06 | 29.98 |
| 2 | T04 / B02 / F02 — IAM, general | 49.06 | 50.53 | 48.6 |
| 3 | T06 / B03 / F03 — large policy, grc | 49.14 | 70.9 | 59.27 |
| 4 | T09 / B04 / F04 — vuln app, code | 53.92 | 65.1 | 53.68 |

## Per-prompt detail

### Slot 1

| field | basic | standard | full |
|---|---|---|---|
| test id | B01 | T02 | F01 |
| latency (s) | 31.02 | 43.06 | 29.98 |
| ground truth | 5/7 | 7/7 | 6/7 |
| citation hit | yes | yes | yes |
| citation error | Yes | Yes | Yes |
| weakness tag | hallucinated_control_id | withdrawn_framework_id | withdrawn_framework_id |

### Slot 2

| field | basic | standard | full |
|---|---|---|---|
| test id | B02 | T04 | F02 |
| latency (s) | 49.06 | 50.53 | 48.6 |
| ground truth | 4/4 | 4/4 | 4/4 |
| citation hit | partial | partial | partial |
| citation error | Yes | Yes | Yes |
| weakness tag | withdrawn_framework_id | domain_logic_error | withdrawn_framework_id |

### Slot 3

| field | basic | standard | full |
|---|---|---|---|
| test id | B03 | T06 | F03 |
| latency (s) | 49.14 | 70.9 | 59.27 |
| ground truth | 1/7 | 3/7 | 1/7 |
| citation hit | partial | yes | partial |
| citation error | No | Yes | Yes |
| weakness tag | incomplete_gt_coverage | hallucinated_control_id | hallucinated_control_id |

### Slot 4

| field | basic | standard | full |
|---|---|---|---|
| test id | B04 | T09 | F04 |
| latency (s) | 53.92 | 65.1 | 53.68 |
| ground truth | 8/11 (+1 partial) | 7/11 (+1 partial) | 7/11 (+1 partial) |
| citation hit | yes | yes | yes |
| citation error | Yes | Yes | Yes |
| weakness tag | mislabelled_framework_category | wrong_framework_edition | mislabelled_framework_category |


## Citation-error breakdown

A test can contribute more than one error type.

| error type | basic | standard | full |
|---|---|---|---|
| domain_logic_error | 0 | 1 | 0 |
| false_absence | 0 | 1 | 1 |
| invented_id | 1 | 1 | 1 |
| mislabelled_title | 1 | 1 | 2 |
| withdrawn_id | 1 | 3 | 3 |
| wrong_edition | 1 | 1 | 0 |

## What each tier actually changes

- `basic`: dense vector retrieval only.
- `standard`: adds hybrid BM25/RRF fusion plus cross-encoder reranking; heuristic query condensation (no extra LLM call).
- `full`: adds LLM query condensation on top. On a single-turn prompt there is no history to condense, so `full` and `standard` share the same retrieval path and the same reranker.

## Retrieval-path verification (measured, not assumed)

`build_context_block` was called directly with the same query and engagement at
each tier, and the resulting context strings compared:

| engagement | basic vs standard | standard vs full |
|---|---|---|
| Lab1-Firewall (2 chunks, top_k=6) | differ (3856 vs 3864 chars) | **byte-identical** |
| Lab3-LargePolicy (9 chunks, top_k=6) | differ (13239 vs 13704 chars) | identical path |

Two consequences for reading the table above:

1. **`full` cannot beat `standard` on these prompts.** With no chat history the
   LLM-condensation step is skipped by design (`condense_query` returns early
   when `history` has no user turn), so both tiers run hybrid retrieval plus the
   same reranker. Any latency or quality gap between the `standard` and `full`
   columns is run-to-run noise, not a tier effect. Confirmed identical context,
   yet slot 3 measured 70.9 s (standard) vs 59.3 s (full) — ~20% apart purely
   from sampling.
2. **Reranking barely engages on this corpus.** `TOP_K_USER_DOC=6` with only 2
   and 3 chunks indexed means the candidate set never exceeds top_k for three of
   the four samples, so the cross-encoder has nothing to reorder. Only
   `large-policy-document.txt` (9 chunks) gives it work to do.

## Caveat: latency for `standard` is not directly comparable

The `standard` column comes from the Step 2.1 run, which executed against the
**Fortis Desktop** process. The `basic`/`full` columns come from a **headless
`python -m server.app`** subprocess. Desktop carries a pywebview UI and its
polling/timers on the same machine, so the `standard` latencies are not measured
under identical conditions to the other two columns.

Despite that, `standard` is the *slowest* column in all four slots — the
opposite of what extra desktop overhead would predict. The likely explanation is
that two separate `is_available()`/`_resolve_device()` code paths exist, one for
embeddings and one for the reranker, and the CUDA demotion they trigger was
logged **4 times** in the headless server log (once per process start) but the
desktop run predates this logging. Treat the ~10 s spread between `standard`
and the other two columns as unverified; a clean three-level comparison needs a
single launcher for all three tiers.

## Reranker note

This venv's torch build has no CUDA support (`AssertionError: Torch not compiled
with CUDA enabled`), so the cross-encoder demotes to CPU. Reranking therefore
costs CPU time on every retrieval and competes with the GPU-resident LLM. The
demotion is logged once per process at first reranker load, and
`rerank.is_available()` still returns `True` — the tier is enabled, it just runs
on CPU.

