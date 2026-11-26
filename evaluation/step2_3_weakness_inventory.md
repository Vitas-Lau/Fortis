# Step 2.3 — Baseline Weakness Inventory

**Project:** Fortis — local AI cybersecurity advisor (COMP3010HK CW2)
**Measured:** 2026-11-26 · model `Qwen3.5-4B-Q4_K_M.gguf` · `gpu_full(cuda)`
**Configuration:** `RAG_LEVEL=standard`, `TOP_K_USER_DOC=6`, `TOP_K_FRAMEWORK=4`

Three pain points verified against the running build. Each is measured, not
asserted: the metric comes from disk inspection or from a recorded response, and
every claim below names the artifact that proves it.

---

## Summary

| # | Weakness | Verified metric | Evidence |
|---|---|---|---|
| 1 | Hong Kong compliance coverage absent | **0 / 12 framework files (0%)** | `raw_outputs/P01_hk_s17_g3_probe.md`, `screenshots/05_probe1_hk_s17_g3_gap.png` |
| 2 | No IOC / CISA KEV enrichment | **0 extractor modules, 0 KEV catalogs** | `raw_outputs/P02_ioc_kev_probe.md`, `screenshots/06_probe2_ioc_kev_gap.png` |
| 3 | Test suite never grades answer quality | **239 passed, 0 accuracy tests** | `step2_3_audit_detail.json` → `weakness3_test_suite` |

Baseline reliability across Steps 2.1–2.3: **9 of 10 chat-style tests carry at
least one citation or grounding defect** (8 from Steps 2.1/2.2 + P02/P01 split
below). Only `T01` and `P01` are clean, and `P01` is clean only because the
model **refused** rather than complied.

---

## Weakness 1 — `HK_COMPLIANCE_GAP` (Hong Kong S17 / G3 = 0% corpus coverage)

### Metric

All 12 shipped framework files were scanned for five signals (`OGCIO`, `S17`,
`G3`, `Hong Kong|HK`, `PDPO`). Every file scored zero on every signal:

| framework file | OGCIO | S17 | G3 | HK | PDPO |
|---|---|---|---|---|---|
| cis_benchmarks.json | 0 | 0 | 0 | 0 | 0 |
| cis_controls.json | 0 | 0 | 0 | 0 | 0 |
| cwe_top25.json | 0 | 0 | 0 | 0 | 0 |
| gdpr.json | 0 | 0 | 0 | 0 | 0 |
| hipaa_security_rule.json | 0 | 0 | 0 | 0 | 0 |
| iso_27001.json | 0 | 0 | 0 | 0 | 0 |
| mitre_attack.json | 0 | 0 | 0 | 0 | 0 |
| nist_800_53.json | 0 | 0 | 0 | 0 | 0 |
| nist_csf.json | 0 | 0 | 0 | 0 | 0 |
| owasp_top10.json | 0 | 0 | 0 | 0 | 0 |
| pci_dss.json | 0 | 0 | 0 | 0 | 0 |
| soc2_tsc.json | 0 | 0 | 0 | 0 | 0 |

**Coverage 0 / 12 = 0.0%.** The entire corpus is US/EU-centric: NIST, CIS, ISO,
PCI DSS, SOC 2, GDPR, HIPAA, OWASP, CWE, MITRE. Nothing for Hong Kong.

### Probe result (P01, `role=grc`, 30.53 s)

Fortis **did not fabricate** S17/G3 clause numbers. It refused and said why:

> "I cannot answer this request because the provided documents do not contain
> the text of the Hong Kong Government OGCIO/DPO Baseline IT Security Policy
> (S17) or the IT Security Guidelines (G3)."
>
> "Specific S17/G3 citations cannot be generated."

It then fell back to PCI DSS `Req 12`, ISO 27001 `A.5.1` / `A.5.24`, and HIPAA
`164.308(a)(1)`.

**Fabricated S17/G3 clauses: 0.** Honest-refusal behaviour is the one place the
baseline scores well, and it is worth crediting in the report: the system prompt
("if you cannot answer confidently, say so explicitly instead of guessing") is
demonstrably working.

### Why it is still a weakness

The user asked a Hong Kong compliance question and received a **US/EU answer**.
For an HK-facing consultancy that is a hard blocker: S17 and G3 are the two
instruments a HK government or regulated client will be audited against, and the
product has no representation of either. `citation_hit=no`,
`key_weakness_tag=HK_COMPLIANCE_GAP`.

- One caveat worth stating honestly: the response also contained a *hypothetical*
  example format, `"S17.4.2 - Identity Management"`, offered as illustration of
  what it would produce after upload. It is a placeholder, not a citation, and
  the audit excludes it — but a reader skimming the transcript could mistake it
  for a real clause. Worth flagging as a UX risk.

### Evidence

- `evaluation/raw_outputs/P01_hk_s17_g3_probe.md` — full verbatim response
- `evaluation/screenshots/05_probe1_hk_s17_g3_gap.png` — 1440×900 @2× (2880×1800) UI capture showing the prompt, the refusal, and the PCI/ISO/HIPAA fallback

---

## Weakness 2 — `NO_IOC_KEV_ENRICHMENT` (static framework, no live threat urgency)

### Metric

Scanned all 36 Python modules under `core/`, `engine/`, `server/`:

| check | result |
|---|---|
| deterministic IOC extractor modules | **0** |
| modules referencing CVE ids | **0** |
| modules referencing KEV | **0** |
| offline CISA KEV catalog files | **0** |
| static MITRE ATT&CK corpus | yes (`core/frameworks/mitre_attack.json`, 39 techniques) |

So threat mapping is a **static technique lookup**, not enrichment. Nothing in
the build can answer "is this CVE being exploited right now".

### Probe result (P02, `role=cti`, 53.46 s)

**IOC recall was perfect: 5/5** — the IPv4 address, the C2 domain, the SHA-256,
and both CVE ids were all extracted correctly, in a structured table with
severity and source chunk.

**But it fabricated the enrichment the build cannot do.** With no KEV catalog on
disk, it wrote:

> "I have cross-referenced the identified CVEs with the current CISA Known
> Exploited Vulnerabilities (KEV) Catalog."

> **CVE-2021-44228 (Log4Shell)** — Status: **Active in CISA KEV Catalog.**
> **CVE-2024-3400 (PAN-OS GlobalProtect)** — Status: **Active in CISA KEV Catalog.**

> "Both identified CVEs are confirmed as actively exploited in the CISA KEV
> catalog."

**Ungrounded KEV assertions: 4.** It also invented an ATT&CK id:
**`T1190.001` does not exist** (T1190 "Exploit Public-Facing Application" has no
sub-techniques). The other four cited sub-techniques (`T1059.001`, `T1071.001`,
`T1021.002`, `T1003.001`) are real.

Grounding note — the model happened to be **factually right** about both CVEs
being in KEV. That makes this a *process* failure, not a factual one, and it is
the more dangerous kind: the user cannot distinguish a verified lookup from a
lucky recall, and the same confidence would be applied to a CVE that is *not* in
KEV. `hallucination_detected=Yes`.

### Evidence

- `evaluation/raw_outputs/P02_ioc_kev_probe.md` — full verbatim response with the probe audit block
- `evaluation/screenshots/06_probe2_ioc_kev_gap.png` — 2880×1800 UI capture showing the "cross-referenced" line and both "Active in CISA KEV Catalog" statuses
- `evaluation/simulated_soc_alert.log` — the 10-event fixture (IP `185.220.101.42`, domain `update-cdn-sync.example`, SHA-256 `e3b0c442…b855`, `CVE-2021-44228`, `CVE-2024-3400`)

### A nice detail the model got right

It independently noticed the planted SHA-256 is the hash of an **empty file**
(`e3b0c442…` is SHA-256 of the empty string) and flagged that the `curl … | sh`
payload was therefore likely empty. That is genuine analytical value and worth
citing as a strength.

---

## Weakness 3 — `NO_CITATION_VERIFIER` (the test suite grades behaviour, not answers)

### Metric

Offline run, stub backends, no model required:

```
EMBEDDING_BACKEND=hash-stub FORTIS_LLM_BACKEND=stub \
  ./.venv/Scripts/python.exe -m pytest desktop/tests/ -q --basetemp=.pytest-tmp-eval
```

Result: **239 passed, 2 warnings in 44.00 s.** Fourteen test files define **208**
`def test_*` functions; pytest collects 239 thanks to parametrisation. The
temporary directory was removed afterwards.

### What the suite does check

Wiring, plumbing and contract behaviour — chat history persistence, report
export formats, hardware/offload selection, report progress reporting, RAG tier
switch wiring, rerank retry state, structured output validation, tier/role
presets, UI smoke tests, stress behaviour. That is a solid engineering suite.

### What it does not check

Searching the whole suite for assertions about answer *quality* found **two**
matches, and both are the weak kind:

| signal | match | what it actually asserts |
|---|---|---|
| semantic retrieval accuracy | `test_rag_upgrade.py` | `raise AssertionError("hybrid retrieval must not run at rag_level=basic")` — a branch-selection check, not retrieval quality |
| citation verification | `test_tiers_roles.py` | `assert "control" in preset or "control_id" in preset` — a substring check on a prompt string |

**Tests that measure retrieval accuracy: 0.**
**Tests that verify a cited control id exists or carries the right label: 0.**
**Tests that measure hallucination rate: 0.**

Every test runs against `FORTIS_LLM_BACKEND=stub`, so the model is deterministic
by construction and no answer quality can be observed. **The suite is green
(239/239) while the product emits citation defects on 9 of 10 measured
prompts.** That gap is the weakness: a passing build carries no signal about the
thing the product is actually for.

### Evidence

- `evaluation/step2_3_audit_detail.json` → `weakness3_test_suite` (per-file test counts, the two assertion hits, pytest tail)

---

## Consolidated baseline reliability (Steps 2.1–2.3)

### Step 2.1 — citation audit, 4 lab documents (8 chat tests)

**7 / 8 = 87.5%** carry at least one defect. Only `T01` clean.

| error type | n | tests |
|---|---|---|
| withdrawn_id (CSF 1.1 ids cited as 2.0) | 5 | T02, T03, T04, T05, T06 |
| mislabelled_title (wrong framework/CWE title) | 3 | T05, T06, T08 |
| invented_id | 2 | T05, T06 |
| false_absence (claims a present control is missing) | 2 | T05, T06 |
| domain_logic_error (IAM Deny override) | 1 | T04 |
| wrong_edition (OWASP 2021 for 2025) | 1 | T09 |

A test can contribute more than one type. `corpus_gap` (a real id merely absent
from the local seed corpus) is deliberately **excluded** from the count.

### Step 2.2 — RAG tier comparison (4 prompts × 3 tiers)

| metric | basic | standard | full |
|---|---|---|---|
| mean latency (s) | 45.79 | 57.40 | 47.88 |
| ground-truth coverage | 64% | 74% | 64% |
| tests with citation error | 3/4 | 4/4 | 4/4 |

Two measured facts limit what can be claimed here:

1. **`full` cannot outperform `standard` on single-turn prompts.** With no chat
   history, `condense_query` returns early, so both tiers run identical retrieval
   plus the same reranker. Verified directly: `build_context_block` produces
   **byte-identical** context for `standard` and `full`.
2. **Reranking barely engages.** `TOP_K_USER_DOC=6` with 2–3 chunks indexed
   means three of four samples never exceed top_k, so the cross-encoder has
   nothing to reorder. Only the 9-chunk policy document exercises it.

Also: this venv's torch has **no CUDA build** (`AssertionError: Torch not
compiled with CUDA enabled`), so the cross-encoder silently demotes to CPU while
`rerank.is_available()` still returns `True`.

### Step 2.3 — the two probes

| test | latency | halluc | cite | tag |
|---|---|---|---|---|
| P01 | 30.53 s | No | no | `HK_COMPLIANCE_GAP` |
| P02 | 53.46 s | **Yes** | yes | `NO_IOC_KEV_ENRICHMENT` |

### The headline number

**9 / 10** chat-style tests across all three steps carry at least one citation,
grounding, or enrichment defect. The single clean response (`P01`) is clean
because the model **declined** the request.

That is the thesis of Stage 2: retrieval quality is adequate — IOC recall 5/5,
ground-truth coverage 64–74% — but **citation and grounding discipline is the
failing axis**, and nothing in the 239-test suite measures it.

---

## Artifact index

| artifact | what it holds |
|---|---|
| `evaluation/baseline_results.csv` | 19 rows, 13 columns: `T01`–`T09` (standard), `B01`–`B04` (basic), `F01`–`F04` (full), `P01`, `P02` |
| `evaluation/step2_3_audit_detail.json` | probe audits, corpus scan, tooling scan, test-suite audit, pytest output |
| `evaluation/raw_outputs/P01_hk_s17_g3_probe.md` | verbatim P01 response + probe audit |
| `evaluation/raw_outputs/P02_ioc_kev_probe.md` | verbatim P02 response + probe audit |
| `evaluation/simulated_soc_alert.log` | 10-event synthetic SOC fixture |
| `evaluation/screenshots/05_probe1_hk_s17_g3_gap.png` | 2880×1800, P01 refusal + fallback |
| `evaluation/screenshots/06_probe2_ioc_kev_gap.png` | 2880×1800, P02 fabricated KEV status |
| `evaluation/step2_2_rag_comparison.md` | three-tier side-by-side with measurement caveats |
| `evaluation/run_step2_3_probes.py` | probe runner (`--audit-only` re-grades without inference) |

---

## Honest limitations

- **One sample per probe.** P01 and P02 were each run once. The unrelated
  Step 2.1 repeats showed GT scores moving 5/11 → 7/11 across identical prompts,
  so treat any single probe verdict as indicative rather than a rate.
- **P02's KEV facts are correct.** The defect is that the claim was manufactured,
  not that the answer was wrong. A probe using a CVE that is genuinely *not* in
  KEV would separate those two cases cleanly and is worth adding.
- **`standard` latencies are not strictly comparable** to `basic`/`full` — they
  came from the desktop launcher, the others from headless `server.app`.
- **Test-suite counting.** "208" counts `def test_*` in source; pytest reports
  239 collected. Both are stated so neither is mistaken for the other.
