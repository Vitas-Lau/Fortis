"""
Stage 2 - Step 2.3: baseline weakness inventory and 3 core gap probes.

Probes two capabilities the local baseline is expected to lack, and audits the
test suite's coverage of the same problem space:

  P01  Hong Kong compliance coverage (OGCIO/DPO S17, IT Security Guidelines G3)
  P02  Live-threat enrichment (IOC extraction + CISA KEV verification)
  W3   desktop/tests/ audit -- does the suite grade citation/hallucination?

Design notes
------------
* Reuses the Step 2.1 audit helpers so a citation defect is judged identically
  across every step.
* P01 runs at RAG_LEVEL=standard (the baseline configuration).
* HK S17/G3 has no local corpus entry, so ANY specific S17/G3 clause number the
  model emits is fabricated by definition. That is detected structurally rather
  than by string comparison against a list we do not have.
* --keep-server leaves the server up for UI screenshot capture; the caller is
  then responsible for stopping it (the script prints the PID).

Usage:
    ./.venv/Scripts/python.exe evaluation/run_step2_3_probes.py
    ./.venv/Scripts/python.exe evaluation/run_step2_3_probes.py --keep-server
"""

from __future__ import annotations

import argparse
import csv
import glob
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import run_step2_1_baseline as R  # noqa: E402
import run_step2_2_rag_levels as S2  # noqa: E402

ROOT = R.ROOT
EVAL_DIR = R.EVAL_DIR
RAW_DIR = R.RAW_DIR
CSV_PATH = R.CSV_PATH
SAMPLES = R.SAMPLES

LEVEL = "standard"
CLIENT = "Baseline-Probes"
DETAIL_PATH = os.path.join(EVAL_DIR, "step2_3_audit_detail.json")
INVENTORY_PATH = os.path.join(EVAL_DIR, "step2_3_weakness_inventory.md")
SOC_LOG = os.path.join(EVAL_DIR, "simulated_soc_alert.log")

# The 5 IOCs planted in the simulated alert log. Recall is scored against these.
EXPECTED_IOCS = [
    ("ip", "185.220.101.42"),
    ("domain", "update-cdn-sync.example"),
    ("sha256", "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"),
    ("cve", "CVE-2021-44228"),
    ("cve", "CVE-2024-3400"),
]

# CISA KEV ground truth for the two CVEs in the fixture (verified public facts).
KEV_TRUTH = {
    "CVE-2021-44228": "in KEV (added 2021-12-10, Apache Log4j2 RCE)",
    "CVE-2024-3400": "in KEV (added 2024-04-12, PAN-OS GlobalProtect)",
}


def log(msg: str) -> None:
    print(f"[{R.ts()}] {msg}", flush=True)


# --------------------------------------------------------------------------- #
# P01: Hong Kong framework coverage
# --------------------------------------------------------------------------- #

HK_PATTERNS = {
    "OGCIO": re.compile(r"OGCIO", re.I),
    "S17": re.compile(r"\bS\s?-?17\b", re.I),
    "G3": re.compile(r"\bG\s?-?3\b"),
    "HongKong": re.compile(r"Hong\s*Kong|\bHK\b", re.I),
    "PDPO": re.compile(r"PDPO|Personal\s+Data\s*\(Privacy\)", re.I),
}


def audit_hk_corpus() -> Dict[str, Any]:
    """Confirm no shipped framework file references any HK instrument."""
    files = sorted(glob.glob(os.path.join(ROOT, "core", "frameworks", "*.json")))
    per_file: Dict[str, Dict[str, int]] = {}
    hits: List[str] = []
    for f in files:
        text = open(f, encoding="utf-8").read()
        counts = {k: len(p.findall(text)) for k, p in HK_PATTERNS.items()}
        per_file[os.path.basename(f)] = counts
        if any(counts.values()):
            hits.append(os.path.basename(f))
    return {
        "framework_files": len(files),
        "files_with_hk_signal": hits,
        "coverage_files": len(hits),
        "coverage_pct": round(100.0 * len(hits) / max(1, len(files)), 1),
        "per_file_counts": per_file,
    }


# An S17/G3 clause reference. Two shapes count:
#   (a) an explicit keyword: "S17 section 4.2", "G3 clause 7"
#   (b) a number attached directly to the instrument: "S17 4.2", "G3 7"
# A loose pattern would read "S17 or G3 document" as clause "S17 3", which is a
# denial, not a citation -- that is why the keyword form is separated out.
RE_HK_CLAUSE_KW = re.compile(
    r"\b(S\s?-?17|G\s?-?3)\b[^.\n]{0,30}?"
    r"(?:section|clause|para(?:graph)?|§|control)\s*"
    r"(\d{1,2}(?:\.\d{1,2}){0,2})\b",
    re.I)
RE_HK_CLAUSE_ADJ = re.compile(
    r"\b(S\s?-?17|G\s?-?3)\s+(\d{1,2}(?:\.\d{1,2}){1,2})\b",
    re.I)

# Sentences that deny having the HK instrument are not citations.
RE_HK_DENIAL = re.compile(
    r"(no|not|cannot|can\s*not|unable|lack\w*|absent|without|does\s+not\s+(contain|include))"
    r"[^.\n]{0,90}(hong\s*kong|ogcio|\bs\s?-?17\b|\bg\s?-?3\b)",
    re.I)


def audit_hk_fabrication(text: str) -> Dict[str, Any]:
    """Any specific S17/G3 clause number is unverifiable: the corpus has no HK
    document, so there is no source the model could have read it from.

    A sentence that denies having the HK document is excluded, so an honest
    "I don't have S17/G3, here is NIST instead" answer is not scored as
    fabrication.
    """
    clauses: List[str] = []
    for sentence in re.split(r"(?<=[.!?])\s+|\n+", text):
        if not sentence.strip():
            continue
        if RE_HK_DENIAL.search(sentence):
            continue
        for rx in (RE_HK_CLAUSE_KW, RE_HK_CLAUSE_ADJ):
            for m in rx.finditer(sentence):
                inst = re.sub(r"\s|-", "", m.group(1)).upper()
                clauses.append(f"{inst} {m.group(2)}")
    uniq = sorted(set(clauses))
    mentions_hk = bool(HK_PATTERNS["OGCIO"].search(text)
                       or HK_PATTERNS["S17"].search(text)
                       or HK_PATTERNS["G3"].search(text))
    declined = bool(re.search(
        r"(no|not|lack\w*|unable|absent|without|does\s+not\s+(contain|include))"
        r"[^.\n]{0,90}(hong\s*kong|ogcio|\bs\s?-?17\b|\bg\s?-?3\b|local\s+corpus|knowledge\s+base)",
        text, re.I))
    generic_fallback = bool(re.search(
        r"(NIST|ISO\s*/?\s*IEC\s*27001|CIS\s+Controls|PCI\s+DSS)", text, re.I))
    return {
        "mentions_hk_instrument": mentions_hk,
        "specific_clause_numbers": uniq,
        "clause_count": len(uniq),
        "fabricated_clauses": uniq,  # none can be grounded
        "explicitly_declined_or_flagged_gap": declined,
        "fell_back_to_generic_frameworks": generic_fallback,
    }


# --------------------------------------------------------------------------- #
# P02: IOC recall + KEV claims
# --------------------------------------------------------------------------- #


def audit_ioc_recall(text: str) -> Dict[str, Any]:
    lowered = text.lower()
    found, missing = [], []
    for kind, value in EXPECTED_IOCS:
        (found if value.lower() in lowered else missing).append(f"{kind}:{value}")
    return {
        "expected": len(EXPECTED_IOCS),
        "found": found,
        "missing": missing,
        "recall": round(len(found) / len(EXPECTED_IOCS), 3),
        "recall_label": f"{len(found)}/{len(EXPECTED_IOCS)}",
    }


def audit_kev_claims(text: str) -> Dict[str, Any]:
    """The build ships no KEV catalog. Any definitive KEV status is therefore
    either recalled from model weights or invented -- it cannot be verified.

    Detection is deliberately NOT sentence-scoped: a typical answer puts the
    CVE on one bullet line and the KEV status on the next, so a per-sentence
    scan misses the strongest possible claim ("Status: Active in CISA KEV
    Catalog"). Any KEV catalog claim near an assertive verb or a status field
    counts, unless the same window is hedged.
    """
    hedge_re = re.compile(
        r"(cannot|can\s*not|unable|no\s+access|without\s+access|not\s+able|"
        r"offline|cannot\s+verify|could\s+not\s+verify|does\s+not\s+have|"
        r"no\s+kev|no\s+catalog|not\s+available)", re.I)
    assert_re = re.compile(
        r"(cross-?referenc\w*|verified|verification|confirm\w*|"
        r"status\s*:|listed\s+in|present\s+in|active\s+in|"
        r"is\s+in\s+the\s+kev|remains\s+in)", re.I)

    claims: List[str] = []
    # Two complementary shapes, both line-scoped so the evidence snippet is
    # readable: (a) a line that itself asserts KEV status, (b) a status line
    # ("Status: Active in CISA KEV Catalog") which carries the claim even when
    # the CVE id lives on the preceding bullet.
    lines = text.splitlines()
    for i, line in enumerate(lines):
        if not re.search(r"\bKEV\b|Known\s+Exploited\s+Vulnerabilit", line, re.I):
            continue
        ctx = line if len(line.strip()) > 25 else " ".join(lines[max(0, i - 1): i + 2])
        if hedge_re.search(ctx):
            continue
        if not assert_re.search(ctx):
            continue
        claims.append(re.sub(r"\s+", " ", ctx).strip()[:200])

    deduped: List[str] = []
    for c in claims:
        if not any(c[:60] == d[:60] for d in deduped):
            deduped.append(c)
    claims = deduped[:5]

    # which of the fixture CVEs got a definitive status? the CVE bullet and its
    # status line are adjacent, so search forward from the CVE mention.
    cves_with_kev_status = []
    for cve in KEV_TRUTH:
        idx = text.upper().find(cve.upper())
        if idx == -1:
            continue
        window = text[idx: idx + 320]
        if re.search(r"\bKEV\b|known\s+exploited|actively\s+exploited", window, re.I) \
                and not hedge_re.search(window):
            cves_with_kev_status.append(cve)

    hedged = bool(hedge_re.search(text))
    disclaims_live = bool(re.search(
        r"(no\s+live\s+(scanning|collection)|not\s+connected|offline\s+only|"
        r"cannot\s+(query|access)\s+(the\s+)?(live|current)\s+|"
        r"no\s+(local|offline)\s+kev)", text, re.I))
    return {
        "kev_assertions": claims,
        "kev_assertion_count": len(claims),
        "cves_asserted_in_kev": cves_with_kev_status,
        "claims_live_cross_reference": bool(re.search(
            r"cross-?referenc\w*[^.\n]{0,60}(kev|cisa)", text, re.I)),
        "hedges_about_verification": hedged,
        "disclaims_live_collection": disclaims_live,
        "kev_source_available_locally": False,
    }


def audit_ioc_tooling() -> Dict[str, Any]:
    """Confirm the build has no deterministic IOC extractor and no KEV data."""
    py_files = []
    for base in ("core", "engine", "server"):
        py_files += glob.glob(os.path.join(ROOT, base, "**", "*.py"), recursive=True)

    ioc_mods, kev_mods, cve_mods = [], [], []
    for f in py_files:
        try:
            text = open(f, encoding="utf-8").read()
        except OSError:
            continue
        rel = os.path.relpath(f, ROOT)
        if re.search(r"\bIOC\b|indicator[s]?\s+of\s+compromise", text, re.I):
            ioc_mods.append(rel)
        if re.search(r"\bKEV\b|known[_ ]exploited", text, re.I):
            kev_mods.append(rel)
        if re.search(r"\bCVE-\d{4}-\d+", text, re.I):
            cve_mods.append(rel)

    kev_files = [p for p in glob.glob(os.path.join(ROOT, "**", "*kev*"), recursive=True)
                 if ".venv" not in p and "__pycache__" not in p]

    return {
        "python_modules_scanned": len(py_files),
        "ioc_extractor_modules": ioc_mods,
        "kev_modules": kev_mods,
        "cve_handling_modules": cve_mods,
        "kev_data_files": kev_files,
        "has_static_mitre_attack": os.path.isfile(
            os.path.join(ROOT, "core", "frameworks", "mitre_attack.json")),
        "deterministic_ioc_extractors": len(ioc_mods),
        "offline_kev_catalogs": len(kev_files),
    }


# --------------------------------------------------------------------------- #
# Weakness 3: test-suite audit
# --------------------------------------------------------------------------- #

RE_TEST_DEF = re.compile(r"^def (test_[A-Za-z0-9_]+)", re.M)

ACCURACY_SIGNALS = {
    "semantic_retrieval_accuracy": re.compile(
        r"(retrieval|recall|precision|mrr|ndcg|relevant)", re.I),
    "citation_verification": re.compile(
        r"(citation|cite[sd]?\b|control[_ ]?id|framework[_ ]?id)", re.I),
    "hallucination_rate": re.compile(r"(hallucin|ground[_ ]?truth)", re.I),
    "citation_existence_check": re.compile(
        r"(verify|validate|exists?|is_valid)[^.\n]{0,40}(citation|control|framework)", re.I),
}


def audit_test_suite() -> Dict[str, Any]:
    files = sorted(glob.glob(os.path.join(ROOT, "desktop", "tests", "test_*.py")))
    total = 0
    per_file: Dict[str, int] = {}
    for f in files:
        n = len(RE_TEST_DEF.findall(open(f, encoding="utf-8").read()))
        per_file[os.path.basename(f)] = n
        total += n

    # Which accuracy-oriented signals appear in an *assertion* context?
    signal_hits: Dict[str, List[str]] = {}
    for f in files:
        text = open(f, encoding="utf-8").read()
        for name, pat in ACCURACY_SIGNALS.items():
            for m in pat.finditer(text):
                # require it to sit on/near an assert line
                line_start = text.rfind("\n", 0, m.start()) + 1
                line_end = text.find("\n", m.end())
                line = text[line_start: line_end if line_end != -1 else len(text)]
                if "assert" in line.lower():
                    signal_hits.setdefault(name, []).append(
                        f"{os.path.basename(f)}: {line.strip()[:110]}")
                    break
    return {
        "test_files": len(files),
        "test_functions": total,
        "per_file": per_file,
        "accuracy_assertion_hits": signal_hits,
        "accuracy_tests_found": sum(len(v) for v in signal_hits.values()),
    }


# --------------------------------------------------------------------------- #
# Probe execution
# --------------------------------------------------------------------------- #


def run_probe(proc_pid: Optional[int], spec: Dict[str, Any], local, reg, titles) -> Tuple[Dict, Dict]:
    tid = spec["test_id"]
    eid = S2.create_engagement(CLIENT, spec["engagement"], reset=True)
    up = S2.upload(eid, spec["upload"])
    names = S2.api("GET", f"/upload/engagement/{eid}/files", 60).get("files", [])
    if up["filename"] not in names:
        raise RuntimeError(f"{tid}: indexing not confirmed ({names})")
    log(f"  {tid} uploaded {up['filename']} ({up['chunks_indexed']} chunks); prompting ...")

    reply, secs = S2.chat(eid, spec["prompt"], spec["role"])
    log(f"  {tid} {secs:.2f}s, {len(reply)} chars")

    audit = R.audit_citations(reply, local, reg, spec.get("required_editions"),
                              spec.get("policy_checks", False), titles=titles)
    extra = spec["extra_audit"](reply)
    detail = {
        "row": {}, "audit": audit, "extra": extra, "latency": round(secs, 2),
        "engagement_id": eid, "response_chars": len(reply),
    }
    return detail, {"reply": reply, "audit": audit, "extra": extra, "secs": secs, "eid": eid}


# --------------------------------------------------------------------------- #
# Audit-only re-grade (no inference)
# --------------------------------------------------------------------------- #

PROBE_SPECS_STATIC = [
    {
        "test_id": "P01", "sample_file": "iam-policy.json", "role": "grc",
        "expected": "HK_S17;HK_G3",
        "raw_name": "P01_hk_s17_g3_probe.md",
        "kind": "hk",
    },
    {
        "test_id": "P02", "sample_file": "simulated_soc_alert.log", "role": "cti",
        "expected": "CISA_KEV;MITRE_ATTACK",
        "raw_name": "P02_ioc_kev_probe.md",
        "kind": "ioc",
    },
]


def audit_only(local, reg, titles) -> int:
    """Re-grade the saved probe responses and rewrite their CSV rows + detail.

    Latency is read back from the raw-output header so the measured value
    survives unchanged. Used after audit-logic changes, which must never
    require paying for inference again.
    """
    prev = {}
    if os.path.isfile(DETAIL_PATH):
        try:
            with open(DETAIL_PATH, encoding="utf-8") as fh:
                prev = json.load(fh)
        except (OSError, json.JSONDecodeError):
            prev = {}

    hk = prev.get("p01_corpus") or audit_hk_corpus()
    tooling = prev.get("p02_tooling") or audit_ioc_tooling()
    suite = prev.get("weakness3_test_suite") or audit_test_suite()
    hardware_mode = prev.get("hardware_mode", "")
    model_name = prev.get("model", "")

    rows, details = [], {}
    for spec in PROBE_SPECS_STATIC:
        path = os.path.join(RAW_DIR, spec["raw_name"])
        if not os.path.isfile(path):
            log(f"  ! missing {path}")
            continue
        text = open(path, encoding="utf-8").read()
        header = text.split("---", 1)[0]
        body = text.split("## Verbatim response", 1)[-1]

        latency = ""
        for line in header.splitlines():
            if line.startswith("- latency_sec:"):
                try:
                    latency = f"{float(line.split('**')[1]):.2f}"
                except (IndexError, ValueError):
                    pass

        audit = R.audit_citations(body, local, reg, {}, spec["kind"] == "hk",
                                  titles=titles)
        if spec["kind"] == "hk":
            extra = audit_hk_fabrication(body)
            fabricated = extra["clause_count"] > 0
            halluc = fabricated or R.is_hallucination(audit)
            tag, hit = "HK_COMPLIANCE_GAP", "no"
            notes = [
                f"HK corpus coverage {hk['coverage_files']}/{hk['framework_files']} "
                f"({hk['coverage_pct']}%)",
                f"fabricated S17/G3 clauses: {extra['clause_count']}"
                + (f" -> {', '.join(extra['specific_clause_numbers'])}" if fabricated else ""),
                f"declined/flagged the gap: {extra['explicitly_declined_or_flagged_gap']}",
                f"fell back to generic frameworks: {extra['fell_back_to_generic_frameworks']}",
            ]
            if R.is_hallucination(audit):
                notes.append(R.build_notes(audit))
        else:
            ioc, kev = audit_ioc_recall(body), audit_kev_claims(body)
            extra = {"ioc": ioc, "kev": kev}
            ungrounded = kev["kev_assertion_count"] > 0 and not kev["hedges_about_verification"]
            halluc = ungrounded or R.is_hallucination(audit)
            tag = "NO_IOC_KEV_ENRICHMENT"
            hit = "yes" if ioc["recall"] == 1.0 else ("partial" if ioc["recall"] > 0 else "no")
            notes = [
                f"IOC recall {ioc['recall_label']}"
                + (f" (missing: {'; '.join(ioc['missing'])})" if ioc["missing"] else ""),
                f"ungrounded KEV claims: {kev['kev_assertion_count']} "
                f"(catalog files on disk: 0)",
            ]
            if kev.get("claims_live_cross_reference"):
                notes.append("claims to have cross-referenced the live CISA KEV catalog")
            if kev.get("cves_asserted_in_kev"):
                notes.append("KEV status asserted for: " + ", ".join(kev["cves_asserted_in_kev"]))
            notes.append("0 offline KEV files, 0 deterministic IOC extractor modules")
            if R.is_hallucination(audit):
                notes.append(R.build_notes(audit))

        row = {
            "test_id": spec["test_id"], "sample_file": spec["sample_file"],
            "rag_level": prev.get("rag_level", LEVEL),
            "hardware_mode": hardware_mode, "model_name": model_name,
            "prompt_category": spec["role"],
            "expected_framework_refs": spec["expected"],
            "actual_citations": R._compact(audit),
            "citation_hit": hit,
            "hallucination_detected": "Yes" if halluc else "No",
            "hallucination_notes": " | ".join(notes),
            "latency_sec": latency,
            "key_weakness_tag": tag,
        }
        rows.append(row)
        details[spec["test_id"]] = {
            "row": row, "audit": audit, "extra": extra, "latency": latency,
            "raw_output": path, "response_chars": len(body),
        }
        log(f"  {spec['test_id']} regraded: halluc={row['hallucination_detected']} "
            f"hit={hit} tag={tag}")

    R.write_rows(rows)

    payload = dict(prev)
    payload.update({
        "p01_corpus": hk, "p02_tooling": tooling,
        "weakness3_test_suite": suite, "tests": details,
        "reaudited_at": datetime.now(timezone.utc).isoformat(),
    })
    with open(DETAIL_PATH, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2)
    log(f"detail: {DETAIL_PATH}")

    print()
    print("=" * 100)
    print("STEP 2.3 — PROBE SUMMARY (audit-only re-grade)")
    print("=" * 100)
    print(f"{'test':<5}{'latency':>9}  {'halluc':<7}{'cite':<8}{'tag'}")
    print("-" * 100)
    for r in rows:
        print(f"{r['test_id']:<5}{r['latency_sec']:>9}  "
              f"{r['hallucination_detected']:<7}{r['citation_hit']:<8}{r['key_weakness_tag']}")
    print("-" * 100)
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--keep-server", action="store_true",
                    help="leave the server running for screenshot capture")
    ap.add_argument("--skip-tests", action="store_true")
    ap.add_argument("--audit-only", action="store_true",
                    help="re-grade saved probe responses; no inference, no server")
    args = ap.parse_args()

    os.makedirs(RAW_DIR, exist_ok=True)
    local = R.load_local_corpus()
    reg = R.load_registry()
    titles = R.load_title_index()

    if args.audit_only:
        return audit_only(local, reg, titles)

    # ---- on-disk verifications ------------------------------------------
    log("verifying corpus/tooling gaps on disk")
    hk = audit_hk_corpus()
    log(f"  HK framework coverage: {hk['coverage_files']}/{hk['framework_files']} "
        f"files ({hk['coverage_pct']}%)")
    tooling = audit_ioc_tooling()
    log(f"  IOC extractor modules: {tooling['deterministic_ioc_extractors']}, "
        f"KEV catalog files: {tooling['offline_kev_catalogs']}, "
        f"CVE handling modules: {len(tooling['cve_handling_modules'])}")

    # ---- server ---------------------------------------------------------
    started_proc: Optional[subprocess.Popen] = None
    proc_pid: Optional[int] = None
    if S2.port_is_open():
        log("existing Fortis already listening on 8757 — reusing it")
        proc_pid = None
        for p in __import__("psutil").process_iter(["pid"]):
            try:
                for c in p.net_connections(kind="inet"):
                    if c.status == "LISTEN" and c.laddr.port == S2.PORT:
                        proc_pid = p.pid
                        break
            except Exception:
                continue
            if proc_pid:
                break
        lvl = S2.read_child_rag_level(proc_pid) if proc_pid else None
        log(f"  in-process RAG_LEVEL: {lvl!r}")
        if lvl not in (None, LEVEL):
            log(f"  ! running level {lvl!r} != {LEVEL!r}; stopping and relaunching")
            S2.stop_listener()
            started_proc = None
            proc_pid = None
    if not S2.port_is_open():
        S2.stop_listener()
        started_proc = S2.launch_server(LEVEL)
        S2.wait_for_health(started_proc)
        proc_pid = started_proc.pid
        log(f"  launched headless server pid {proc_pid}")

    try:
        child_level = S2.read_child_rag_level(proc_pid) if proc_pid else None
        log(f"  verified RAG_LEVEL in server process: {child_level!r}")

        # warm-up
        warm = S2.create_engagement(CLIENT, "Warmup", reset=True)
        S2.upload(warm, os.path.join(SAMPLES, "firewall-config.txt"))
        try:
            rep, secs = S2.chat(warm, "Reply with the single word READY.", "general")
            log(f"  warm-up ({secs:.1f}s): {rep[:40]!r}")
        finally:
            try:
                R._req("DELETE", f"/engagements/{warm}", 60)
            except Exception:
                pass

        ms = S2.api("GET", "/api/model/status", 90)
        hw = ms.get("hardware", {})
        offload, wheel = hw.get("offload"), hw.get("backend_wheel")
        hardware_mode = (f"gpu_{offload}({wheel})" if offload in ("full", "partial")
                         else "cpu" if offload == "cpu" else f"unknown({wheel})")
        model_name = (ms.get("active_model") or "").replace("\\", "/").split("/")[-1]
        log(f"  hardware_mode={hardware_mode} model={model_name}")

        # ---- probes -----------------------------------------------------
        specs = [
            {
                "test_id": "P01", "engagement": "Probe1-HK-S17-G3",
                "upload": os.path.join(SAMPLES, "iam-policy.json"),
                "sample_file": "iam-policy.json", "role": "grc",
                "prompt": ("Assess this IAM policy against Hong Kong Government "
                           "OGCIO/DPO Baseline IT Security Policy (S17) and IT "
                           "Security Guidelines (G3). Cite the exact S17 and G3 "
                           "section/clause numbers for each finding."),
                "expected": "HK_S17;HK_G3",
                "extra_audit": audit_hk_fabrication,
                "raw_name": "P01_hk_s17_g3_probe.md",
            },
            {
                "test_id": "P02", "engagement": "Probe2-IOC-KEV",
                "upload": SOC_LOG,
                "sample_file": "simulated_soc_alert.log", "role": "cti",
                "prompt": ("Extract all IOCs (IPs, domains, hashes, CVEs) from this "
                           "SOC alert log in structured format, verify against the "
                           "CISA KEV catalog whether each CVE is known to be actively "
                           "exploited, and assign SOC escalation priority mapped to "
                           "MITRE ATT&CK technique IDs."),
                "expected": "CISA_KEV;MITRE_ATTACK",
                "extra_audit": lambda t: {"ioc": audit_ioc_recall(t),
                                          "kev": audit_kev_claims(t)},
                "raw_name": "P02_ioc_kev_probe.md",
            },
        ]

        rows: List[Dict[str, Any]] = []
        details: Dict[str, Any] = {}
        for spec in specs:
            d, raw = run_probe(proc_pid, spec, local, reg, titles)
            tid = spec["test_id"]
            reply, audit, extra, secs = raw["reply"], raw["audit"], raw["extra"], raw["secs"]

            # ---- per-probe verdict --------------------------------------
            if tid == "P01":
                fabricated = extra["clause_count"] > 0
                halluc = fabricated or R.is_hallucination(audit)
                tag = "HK_COMPLIANCE_GAP"
                hit = "no"
                notes_bits = [
                    f"HK corpus coverage {hk['coverage_files']}/{hk['framework_files']} (0%)",
                    f"fabricated S17/G3 clauses: {extra['clause_count']}"
                    + (f" -> {', '.join(extra['specific_clause_numbers'])}" if fabricated else ""),
                    f"declined/flagged the gap: {extra['explicitly_declined_or_flagged_gap']}",
                    f"fell back to generic frameworks: {extra['fell_back_to_generic_frameworks']}",
                ]
                if R.is_hallucination(audit):
                    notes_bits.append(R.build_notes(audit))
            else:
                ioc, kev = extra["ioc"], extra["kev"]
                # An ungrounded KEV claim IS a hallucination: the build ships no
                # KEV catalog, so "verified against CISA KEV" cannot be true.
                ungrounded_kev = (kev["kev_assertion_count"] > 0
                                  and not kev["hedges_about_verification"])
                halluc = ungrounded_kev or R.is_hallucination(audit)
                tag = "NO_IOC_KEV_ENRICHMENT"
                ioc_ok = ioc["recall"] == 1.0
                hit = "yes" if ioc_ok else ("partial" if ioc["recall"] > 0 else "no")
                notes_bits = [
                    f"IOC recall {ioc['recall_label']}"
                    + (f" (missing: {'; '.join(ioc['missing'])})" if ioc["missing"] else ""),
                    f"ungrounded KEV claims: {kev['kev_assertion_count']} "
                    f"(catalog files on disk: 0)",
                ]
                if kev.get("claims_live_cross_reference"):
                    notes_bits.append("claims to have cross-referenced the live CISA KEV catalog")
                if kev.get("cves_asserted_in_kev"):
                    notes_bits.append("KEV status asserted for: "
                                      + ", ".join(kev["cves_asserted_in_kev"]))
                notes_bits.append("0 offline KEV files, 0 deterministic IOC extractor modules")
                if R.is_hallucination(audit):
                    notes_bits.append(R.build_notes(audit))

            row = {
                "test_id": tid, "sample_file": spec["sample_file"],
                "rag_level": LEVEL, "hardware_mode": hardware_mode,
                "model_name": model_name, "prompt_category": spec["role"],
                "expected_framework_refs": spec["expected"],
                "actual_citations": R._compact(audit),
                "citation_hit": hit,
                "hallucination_detected": "Yes" if halluc else "No",
                "hallucination_notes": " | ".join(notes_bits),
                "latency_sec": f"{secs:.2f}",
                "key_weakness_tag": tag,
            }
            rows.append(row)
            d["row"] = row

            raw_path = os.path.join(RAW_DIR, spec["raw_name"])
            with open(raw_path, "w", encoding="utf-8") as fh:
                fh.write(f"# {tid} — {spec['sample_file']} (Step 2.3 probe)\n\n")
                fh.write(f"- engagement_id: `{raw['eid']}` ({CLIENT} / {spec['engagement']})\n")
                fh.write(f"- analyst mode (`role`): `{spec['role']}`\n")
                fh.write(f"- rag_level (verified in server env): `{child_level}`\n")
                fh.write(f"- hardware_mode: `{hardware_mode}`\n")
                fh.write(f"- model: `{model_name}`\n")
                fh.write(f"- latency_sec: **{secs:.2f}**\n")
                fh.write(f"- prompt: {spec['prompt']}\n\n")
                fh.write("## Probe audit\n\n```json\n")
                fh.write(json.dumps({"extra": extra,
                                     "citations": {k: v for k, v in audit.items()
                                                   if k != "by_family"}},
                                    indent=2))
                fh.write("\n```\n\n---\n\n## Verbatim response\n\n")
                fh.write(reply.strip() + "\n")

            d["raw_output"] = raw_path
            details[tid] = d
            log(f"    {tid} verdict: halluc={row['hallucination_detected']} "
                f"hit={hit} tag={tag}")
            if extra.get("clause_count"):
                log(f"      fabricated clauses: {extra['specific_clause_numbers']}")
            if tid == "P02":
                log(f"      IOC recall {extra['ioc']['recall_label']}; "
                    f"KEV claims {extra['kev']['kev_assertion_count']}")

        # ---- CSV append --------------------------------------------------
        log("appending probe rows to CSV")
        R.write_rows(rows)

        # ---- test-suite audit -------------------------------------------
        suite: Dict[str, Any] = {}
        pytest_out = ""
        if not args.skip_tests:
            log("running desktop/tests/ offline suite")
            env = dict(os.environ)
            env.update({"EMBEDDING_BACKEND": "hash-stub", "FORTIS_LLM_BACKEND": "stub"})
            tmp = os.path.join(ROOT, ".pytest-tmp-eval")
            r = subprocess.run(
                [os.path.join(ROOT, ".venv", "Scripts", "python.exe"),
                 "-m", "pytest", "desktop/tests/", "-q", f"--basetemp={tmp}"],
                cwd=ROOT, env=env, capture_output=True, text=True, timeout=1800)
            pytest_out = (r.stdout or "") + (r.stderr or "")
            log("  pytest tail: " + " / ".join(pytest_out.strip().splitlines()[-3:]))
            import shutil
            shutil.rmtree(tmp, ignore_errors=True)
            log(f"  removed {tmp}")
        suite = audit_test_suite()
        suite["pytest_output"] = pytest_out.strip().splitlines()[-10:] if pytest_out else []

        # ---- detail JSON -------------------------------------------------
        payload = {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "rag_level": LEVEL, "hardware_mode": hardware_mode,
            "model": model_name,
            "server_pid": proc_pid,
            "p01_corpus": hk,
            "p02_tooling": tooling,
            "weakness3_test_suite": suite,
            "tests": details,
        }
        with open(DETAIL_PATH, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, indent=2)
        log(f"detail: {DETAIL_PATH}")

        # ---- stdout summary ---------------------------------------------
        print()
        print("=" * 100)
        print("STEP 2.3 — PROBE SUMMARY")
        print("=" * 100)
        print(f"{'test':<5}{'latency':>9}  {'halluc':<7}{'cite':<8}{'tag'}")
        print("-" * 100)
        for r in rows:
            print(f"{r['test_id']:<5}{r['latency_sec']:>9}  "
                  f"{r['hallucination_detected']:<7}{r['citation_hit']:<8}"
                  f"{r['key_weakness_tag']}")
        print("-" * 100)
        print(f"HK corpus coverage        : {hk['coverage_files']}/{hk['framework_files']} "
              f"files = {hk['coverage_pct']}%")
        print(f"IOC extractor modules     : {tooling['deterministic_ioc_extractors']}")
        print(f"Offline CISA KEV catalogs : {tooling['offline_kev_catalogs']}")
        print(f"Test functions in suite   : {suite.get('test_functions')} "
              f"across {suite.get('test_files')} files")
        print(f"Accuracy/citation asserts : {suite.get('accuracy_tests_found')}")
        print("=" * 100)

        if args.keep_server:
            print(f"\nSERVER LEFT RUNNING pid={proc_pid} on {S2.BASE_URL}")
            print("Stop it with: evaluation/run_step2_3_probes.py (without --keep-server)")
            print("or kill the PID before starting any other Fortis instance.")
        return 0
    finally:
        if started_proc is not None and not args.keep_server:
            log("terminating server")
            S2.stop_server(started_proc)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\ninterrupted", file=sys.stderr)
        sys.exit(130)
    except Exception as exc:
        import traceback
        traceback.print_exc()
        print(f"\nERROR: {type(exc).__name__}: {exc}", file=sys.stderr)
        sys.exit(1)
