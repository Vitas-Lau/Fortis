# Stage 3 — Enhancement 1: Hong Kong Local Compliance Corpus (DPO S17 & G3)

**Implemented:** 2026-11-27 · Fortis · Python 3.13 · Windows 11
**Baseline this addresses:** Stage 2 weakness `HK_COMPLIANCE_GAP` — 0 / 12 shipped
frameworks covered Hong Kong, and probe P01 refused and fell back to PCI/ISO/HIPAA.

---

## What was built

### 1. Two new framework corpora

| file | entries | source | version |
|---|---|---|---|
| `core/frameworks/hk_dpo_s17.json` | **47** | Baseline IT Security Policy (S17) | v8.2, April 2025 |
| `core/frameworks/hk_dpo_g3.json` | **58** | IT Security Guidelines (G3) | v10.2, April 2025 |

Both exceed the 25-entry minimum. Schema matches what
`seed_framework_chunks()` consumes — mandatory `id` + `text`, plus only the
whitelisted metadata keys `title`, `category`, `function`, `version`,
`source_url`, and `framework`.

**Every clause ID was validated against the real documents, not invented.**
Both PDFs were downloaded from `govcert.gov.hk` and parsed, and each ID was
checked against numbering harvested from them:

- S17 uses three-level clause notation (`11.2.6`, `14.6.2`) — 140 clauses
  harvested from the PDF; all 47 IDs used resolve.
- G3 uses lettered sub-items under a section (`11.4(b)`, `18.1(c)`) — the
  sub-item titles come from G3's own table of contents; all 58 IDs resolve.

The build script fails loudly if an ID is not found in the source document, so
an invented clause number cannot enter the corpus.

Text is a paraphrase of each control requirement rather than a verbatim copy,
but keeps the operative specifics (the eight-character minimum, the two-year
assessment cycle, the RESTRICTED-and-above public-cloud prohibition) because
those are what make a control auditable.

Domain coverage: governance, asset/classification, access control, passwords,
network/gateway, endpoint/remote, cryptography, logging, vulnerability/patch,
incident handling, outsourcing/cloud, and business continuity. All six NIST CSF
functions are represented (GOVERN/IDENTIFY/PROTECT/RESPOND/RECOVER).

Both files are pure ASCII on disk (UTF-8 safe, cp950 safe).

### 2. Registration and the encoding bug

- `HK_DPO_S17` and `HK_DPO_G3` registered in `_FILES`; `FRAMEWORK_NAMES` now
  reports 14.
- `open(path)` → `open(path, encoding="utf-8")` in `seed_all_frameworks()`.
  Without this, a zh-HK Windows default codec (cp950) raises
  `UnicodeDecodeError` on the corpora.
- `loader.py` docstring corrected: `python -m app.frameworks.loader` →
  `python -m core.frameworks.loader`.

### 3. Deterministic intent routing (`core/rag.py`)

`detect_requested_frameworks(query) -> List[str]` is pure, no I/O, no globals.
It recognises S17, G3, OGCIO, "Digital Policy Office", or Hong Kong together
with a compliance term, and separately NIST CSF / 800-53, OWASP, CIS Controls /
Benchmarks, ISO 27001, MITRE ATT&CK, PCI DSS, SOC 2, GDPR, HIPAA.

`build_context_block()` runs a **second retrieval pass per named framework**
whenever routing fires, then merges:

- Targeted hits lead, **interleaved per framework** — so naming both S17 and G3
  surfaces both rather than letting one corpus fill every slot.
- General hits still fill the remaining slots, so a mixed question
  ("map S17 to NIST CSF") keeps both sides.
- `top_k_framework=4` is preserved as the cap.
- Routing does not fire on generic queries, so existing behaviour is unchanged.

`SYSTEM_PERSONA` now names S17 and G3 explicitly.

### 4. Benchmark dataset

`evaluation/datasets/hk_soc_benchmark.json` — **20 questions**, HK01–HK20,
across IAM, firewall/gateway, password/MFA, logging, incident reporting,
cloud/third-party, endpoint/remote, backup, vulnerability/patching, governance,
and two negative/edge cases (no framework named; a non-existent clause).

`expected_control_ids` are **verified at build time**: all 38 references resolve
to real corpus entries, and each question's `must_cover_concepts` was checked
against the text of its cited controls. Three concept lists were corrected
during that check because the concepts I first wrote were not actually present
in the cited clauses — the validation caught my own error rather than letting it
silently weaken the ground truth.

### 5. Tests and the ablation harness

`desktop/tests/test_hk_frameworks.py` — **30 tests**, fully offline, covering:
schema validity, unique IDs, non-empty text, whitelist-only keys, ASCII-on-disk,
clause-notation shape per document, registration, seeding into Chroma with
metadata, routing (positive and neutral cases), `build_context_block` surfacing
HK chunks, and zero broken benchmark ground truth.

One pre-existing test needed updating: `test_integration.py::test_health`
asserted an exact 12-framework set. Updated to the correct 14.

`evaluation/eval_hk_retrieval.py` — Recall@k ablation on a temporary
`FORTIS_DATA_DIR` (the developer's real engagements are never touched), no LLM
inference.

---

## Verification results

### 1. Test suite — PASS

```
EMBEDDING_BACKEND=hash-stub ./.venv/Scripts/python.exe -m pytest desktop/tests/ -q --basetemp=.pytest-tmp
=> 269 passed, 2 warnings in 38.91s
```

239 pre-existing + 30 new. Zero failures.

### 2. Retrieval ablation — real `BAAI/bge-small-en-v1.5` embeddings

18 scored questions (HK19/HK20 are negative/edge cases and are excluded from
recall scoring by design).

| Routing | Recall@4 | Recall@8 | Δ (8−4) |
|---|---|---|---|
| OFF | 0.611 | 0.685 | +0.074 |
| ON | **0.639** | **0.694** | +0.056 |

**Routing gain: +0.028 at k=4, +0.009 at k=8.**

Honest reading: routing helps, but **modestly**. Two questions carry most of the
gain (HK04 0.50→1.00, HK06 0.00→0.33 at k=8). It is not a dramatic improvement
and should not be reported as one.

### 3. Probe P01 re-run — `HK_COMPLIANCE_GAP` closed

Fresh engagement, `iam-policy.json` uploaded, `role=grc`, original Stage 2
prompt, measured through the same HTTP path:

| | Stage 2 (before) | Stage 3 (after) |
|---|---|---|
| Behaviour | refused | answered |
| S17/G3 citations | **0** | `S17 8.1.1`, `G3 7.1(a)` |
| Invented clauses | 0 | **0** |
| Fallback to PCI/ISO/HIPAA | yes | no |
| Latency | 30.53 s | 54.38 s |

The response cites real clause numbers that exist in the shipped corpora, and
invents none. The gap is closed.

---

## An honest limitation worth stating in the report

Closing the gap is not the same as citing well. In the P01 re-run the response
attached **`S17 8.1.1` and `G3 7.1(a)` to all four findings** — both are generic
governance clauses ("promulgate and enforce an IT security policy"; "define
security roles and responsibilities"), not the specific controls that actually
govern each finding.

The correct controls for these findings exist in the corpus and were not used:

| finding | cited | should cite |
|---|---|---|
| `"Principal": "*"` | S17 8.1.1 | **S17 11.1.1** (least privilege), **G3 11.1(a)** |
| `DenyMFASetup` | S17 8.1.1 | **G3 11.4(b)** (password/authentication) |
| `Resource: "*"` | S17 8.1.1 | **S17 11.2.3** (restriction of special privileges) |
| CloudTrail stop/delete | S17 8.1.1 | **S17 18.1.2** (incident evidence retention) |

So the failure mode moved from *"cannot cite at all"* to *"cites a real but
generic clause for everything"*. That is progress — the citations are now
verifiable and the instrument is right — but it is not correct control mapping.

The measured cause is retrieval precision, not corpus content. In the ablation,
**HK01 scores 0.00 under every configuration**: for that prompt the top-8 S17
hits are `8.1.1, 9.1.2, 20.2.2, 11.5.1, 11.6.1, 7.1.1, 20.1.1, 14.7.1` — the
correct `11.2.4`/`11.2.6` never appear. Long prompts that repeat the instrument
name dominate the embedding, so `8.1.1` (whose text contains "policy") ranks
first for almost any S17 query. HK09 fails for the same reason (`14.4.1` and
`18.1.1` are retrieved, but not `18.1.2`).

This is the natural next enhancement: clause-level retrieval needs the kind of
query decomposition or metadata-routed pre-filtering that a 4B model with 4
retrieved slots cannot do on its own. Worth stating plainly rather than tuning
the benchmark until the number looks better.

---

## Files changed

| file | change |
|---|---|
| `core/frameworks/hk_dpo_s17.json` | **new** — 47 S17 controls |
| `core/frameworks/hk_dpo_g3.json` | **new** — 58 G3 controls |
| `core/frameworks/__init__.py` | register 2 frameworks; `encoding="utf-8"` |
| `core/frameworks/loader.py` | docstring `app.` → `core.` |
| `core/rag.py` | persona; `detect_requested_frameworks`; targeted retrieval + merge |
| `desktop/tests/test_hk_frameworks.py` | **new** — 30 offline tests |
| `desktop/tests/test_integration.py` | health assertion 12 → 14 frameworks |
| `evaluation/datasets/hk_soc_benchmark.json` | **new** — 20 questions, 38 verified refs |
| `evaluation/eval_hk_retrieval.py` | **new** — Recall@k ablation |
| `evaluation/run_p01_hk_verify.py` | **new** — P01 re-run harness |
| `evaluation/hk_retrieval_ablation.json` | ablation results |
| `evaluation/step3_p01_verification.json` | P01 verdict |
| `evaluation/raw_outputs/P01_stage3_hk_verification.md` | verbatim P01 response |

Ready for a focused commit. Nothing outside the files above was modified;
`.env` untouched; port 8757 released; temporary pytest dirs removed.
