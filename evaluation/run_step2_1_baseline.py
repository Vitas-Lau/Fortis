"""
Stage 2 - Step 2.1: Baseline verification on the 4 official lab documents.

Talks to the ALREADY-RUNNING Fortis FastAPI server over HTTP. It does not
import core/ modules, does not touch Chroma directly, and does not modify any
source outside evaluation/.

Design rules
------------
* One clean, separate Engagement per sample document (client "Baseline") so
  ChromaDB retrieval from doc N never pollutes doc N+1.
* Latency = wall clock around the full HTTP round trip, time.perf_counter().
* A warm-up turn on a throwaway engagement absorbs llama.cpp model load, so
  cold-start does not inflate T01.
* Citation audit is two-tier:
    - corpus check  : ID present verbatim in core/frameworks/*.json
    - registry check: ID exists in the real published framework edition
  An ID that fails BOTH is a hallucination. An ID that exists for real but is
  absent from the local corpus is reported separately as a corpus coverage gap
  (flagging those as hallucinations would be a measurement artefact, not a
  model error).
* Map-reduce path is confirmed from the app's own /api/report/progress feed,
  not assumed from document size.

Usage:
    ./.venv/Scripts/python.exe evaluation/run_step2_1_baseline.py
    ./.venv/Scripts/python.exe evaluation/run_step2_1_baseline.py --skip-report
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
import sys
import threading
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

import requests

# --------------------------------------------------------------------------- #
# Paths / constants
# --------------------------------------------------------------------------- #

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EVAL_DIR = os.path.join(ROOT, "evaluation")
RAW_DIR = os.path.join(EVAL_DIR, "raw_outputs")
CSV_PATH = os.path.join(EVAL_DIR, "baseline_results.csv")
DETAIL_PATH = os.path.join(EVAL_DIR, "step2_1_audit_detail.json")
PREFLIGHT_PATH = os.path.join(EVAL_DIR, "step2_1_preflight.json")
SAMPLES = os.path.join(ROOT, "training", "sample-lab-docs")

BASE_URL = os.getenv("FORTIS_BASE_URL", "http://127.0.0.1:8757")
CLIENT_NAME = "Baseline"

CSV_HEADER = [
    "test_id", "sample_file", "rag_level", "hardware_mode", "model_name",
    "prompt_category", "expected_framework_refs", "actual_citations",
    "citation_hit", "hallucination_detected", "hallucination_notes",
    "latency_sec", "key_weakness_tag",
]

CHAT_TIMEOUT = 900
REPORT_TIMEOUT = 2700
UPLOAD_TIMEOUT = 300

# --------------------------------------------------------------------------- #
# Small helpers
# --------------------------------------------------------------------------- #


def ts() -> str:
    return datetime.now().strftime("%H:%M:%S")


def log(msg: str) -> None:
    print(f"[{ts()}] {msg}", flush=True)


def ensure_dirs() -> None:
    os.makedirs(EVAL_DIR, exist_ok=True)
    os.makedirs(RAW_DIR, exist_ok=True)


class ApiError(RuntimeError):
    pass


def _req(method: str, path: str, timeout: float, **kwargs) -> requests.Response:
    url = BASE_URL.rstrip("/") + path
    try:
        resp = requests.request(method, url, timeout=timeout, **kwargs)
    except requests.exceptions.RequestException as exc:
        raise ApiError(f"{method} {path} failed: {type(exc).__name__}: {exc}") from exc
    if resp.status_code >= 400:
        raise ApiError(
            f"{method} {path} -> HTTP {resp.status_code}: {resp.text[:600]}"
        )
    return resp


def get_json(path: str, timeout: float = 60) -> Any:
    return _req("GET", path, timeout).json()


def post_json(path: str, payload: Dict[str, Any], timeout: float) -> Any:
    return _req("POST", path, timeout, json=payload).json()


# --------------------------------------------------------------------------- #
# Reference data
# --------------------------------------------------------------------------- #

FRAMEWORK_FILES = {
    "NIST_CSF": "nist_csf.json",
    "OWASP_TOP10": "owasp_top10.json",
    "CIS_CONTROLS": "cis_controls.json",
    "CIS_BENCHMARKS": "cis_benchmarks.json",
    "ISO_27001": "iso_27001.json",
    "MITRE_ATTACK": "mitre_attack.json",
    "NIST_800-53": "nist_800_53.json",
    "PCI_DSS": "pci_dss.json",
    "SOC_2": "soc2_tsc.json",
    "GDPR": "gdpr.json",
    "HIPAA": "hipaa_security_rule.json",
    "CWE_TOP25": "cwe_top25.json",
}


def load_local_corpus() -> Dict[str, set]:
    """Exact ID sets as shipped in core/frameworks/*.json."""
    corpus: Dict[str, set] = {}
    fw_dir = os.path.join(ROOT, "core", "frameworks")
    for name, fname in FRAMEWORK_FILES.items():
        path = os.path.join(fw_dir, fname)
        try:
            with open(path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
        except (OSError, json.JSONDecodeError) as exc:
            log(f"  ! could not load {fname}: {exc}")
            continue
        ids = set()
        for row in data:
            if isinstance(row, dict) and row.get("id"):
                ids.add(str(row["id"]).strip().upper())
        corpus[name] = ids
    return corpus


def load_registry() -> Dict[str, set]:
    """Real published ID sets, used only to separate 'invented' from
    'absent from local corpus'."""
    reg: Dict[str, set] = {}

    # CWE: full MITRE dictionary, built by build_refdata.py
    cwe_path = os.path.join(EVAL_DIR, "refdata", "cwe_full_ids.json")
    if os.path.isfile(cwe_path):
        with open(cwe_path, "r", encoding="utf-8") as fh:
            reg["CWE"] = set(json.load(fh))

    p = os.path.join(EVAL_DIR, "refdata", "attack_ids.json")
    if os.path.isfile(p):
        with open(p, "r", encoding="utf-8") as fh:
            reg["MITRE_ATTACK"] = set(json.load(fh))

    p = os.path.join(EVAL_DIR, "refdata", "nist80053_ids.json")
    if os.path.isfile(p):
        with open(p, "r", encoding="utf-8") as fh:
            raw = json.load(fh)
        # keep only bare control ids (AC-2, SC-7, SI-10 ...)
        reg["NIST_800-53"] = {
            i.upper() for i in raw
            if re.fullmatch(r"[A-Z]{2}-\d{1,3}(?:\.\d{1,3})?(?:\([0-9A-Za-z]+\))*", i)
        }

    p = os.path.join(EVAL_DIR, "refdata", "csf_ids.json")
    if os.path.isfile(p):
        with open(p, "r", encoding="utf-8") as fh:
            d = json.load(fh)
        reg["NIST_CSF_SUB"] = {s.upper() for s in d.get("sub", [])}

    # NIST CSF 2.0: active categories/subcategories, plus the withdrawn CSF 1.1
    # carry-overs. Pre-derived by build_refdata.py, so the runner needs no
    # network access at audit time.
    csf_path = os.path.join(EVAL_DIR, "refdata", "csf_core.json")
    if os.path.isfile(csf_path):
        with open(csf_path, "r", encoding="utf-8") as fh:
            csf = json.load(fh)
        for key in ("NIST_CSF_CAT", "NIST_CSF_WITHDRAWN",
                    "NIST_CSF_SUB", "NIST_CSF_SUB_WITHDRAWN"):
            if key in csf:
                reg[key] = set(csf[key])

    # OWASP Top 10:2025 (10 categories, A01..A10)
    reg["OWASP_TOP10"] = {f"A{i:02d}:2025" for i in range(1, 11)}

    # CIS Controls v8.1: 18 top-level controls
    reg["CIS_CONTROLS"] = {f"CIS-{i}" for i in range(1, 19)}

    # ISO/IEC 27001:2022 Annex A: complete 93-control set
    iso = set()
    for lo, hi in ((5, 37), (6, 8), (7, 14), (8, 34)):
        for i in range(1, hi + 1):
            iso.add(f"A.{lo}.{i}")
    reg["ISO_27001"] = iso

    # PCI DSS v4: requirements 1..12
    reg["PCI_DSS"] = {f"REQ{i}" for i in range(1, 13)}

    # SOC 2 TSC: CC1-CC9 + A1 + C1 + P1..P8 + PI1
    soc = {f"CC{i}" for i in range(1, 10)} | {"A1", "C1", "PI1"}
    soc |= {f"P{i}" for i in range(1, 9)}
    reg["SOC_2"] = soc

    # GDPR: articles 1..99
    reg["GDPR"] = {f"ART.{i}" for i in range(1, 100)}

    # HIPAA Security Rule citations
    reg["HIPAA"] = {
        "164.308(A)(1)", "164.308(A)(2)", "164.308(A)(3)", "164.308(A)(4)",
        "164.308(A)(5)", "164.308(A)(6)", "164.308(A)(7)", "164.308(A)(8)",
        "164.308(B)(1)", "164.310(A)", "164.310(B)", "164.310(C)",
        "164.310(D)", "164.312(A)", "164.312(B)", "164.312(C)", "164.312(D)",
        "164.312(E)", "164.314", "164.316",
    }
    return reg


_CSF_CAT_LINE = re.compile(r"^(\s*)- id: ((?:GV|ID|PR|DE|RS|RC)\.[A-Z]{2})\s*$")
_CSF_SUB_LINE = re.compile(r"^(\s*)- id: ((?:GV|ID|PR|DE|RS|RC)\.[A-Z]{2}-\d{2})\s*$")


def _parse_csf_core(path: str) -> Dict[str, set]:
    """Split the NIST CSF 2.0 core into active vs withdrawn categories and
    subcategories.

    The OSCAL YAML nests subcategories under a category's `controls:` key, and
    a `value: withdrawn` prop can belong to either level. A category's own
    props therefore stop at the first `controls:` line -- anything below that
    belongs to a child subcategory and must not mark the parent withdrawn.
    """
    with open(path, "r", encoding="utf-8") as fh:
        lines = fh.readlines()

    cats: List[Tuple[int, int, str]] = []  # (start, end, id)
    cur_start, cur_id = None, None
    for i, line in enumerate(lines):
        m = _CSF_CAT_LINE.match(line)
        if m:
            if cur_id is not None:
                cats.append((cur_start, i, cur_id))
            cur_start, cur_id = i, m.group(2).upper()
    if cur_id is not None:
        cats.append((cur_start, len(lines), cur_id))

    active_cat, withdrawn_cat = set(), set()
    active_sub, withdrawn_sub = set(), set()

    for start, end, cid in cats:
        # category's own props end at its `controls:` key
        own_end = end
        for j in range(start, end):
            if re.match(r"^\s*controls:\s*$", lines[j]):
                own_end = j
                break
        if any("value: withdrawn" in lines[j] for j in range(start, own_end)):
            withdrawn_cat.add(cid)
        else:
            active_cat.add(cid)

        # subcategories within this category
        subs: List[Tuple[int, str]] = []
        for j in range(start, end):
            m = _CSF_SUB_LINE.match(lines[j])
            if m:
                subs.append((j, m.group(2).upper()))
        for k, (s_start, sid) in enumerate(subs):
            s_end = subs[k + 1][0] if k + 1 < len(subs) else end
            own_s_end = s_end
            for j in range(s_start, s_end):
                if re.match(r"^\s*parts:\s*$", lines[j]):
                    own_s_end = j
                    break
            if any("value: withdrawn" in lines[j] for j in range(s_start, own_s_end)):
                withdrawn_sub.add(sid)
            else:
                active_sub.add(sid)

    return {
        "NIST_CSF_CAT": active_cat,
        "NIST_CSF_WITHDRAWN": withdrawn_cat,
        "NIST_CSF_SUB": active_sub,
        "NIST_CSF_SUB_WITHDRAWN": withdrawn_sub,
    }


# --------------------------------------------------------------------------- #
# Citation extraction
# --------------------------------------------------------------------------- #

RE_CSF = re.compile(r"\b((?:GV|ID|PR|DE|RS|RC)\.[A-Z]{2})(-\d{1,2})?\b")
RE_CWE = re.compile(r"\bCWE-(\d{1,4})\b", re.I)
RE_CIS_DASH = re.compile(r"\bCIS[- ](\d{1,2})(?:\.\d{1,3})?\b", re.I)
RE_CIS_WORD = re.compile(r"\bCIS\s+Controls?\s+(\d{1,2})(?:\.\d{1,3})?\b", re.I)
RE_ISO = re.compile(r"\bA\.(\d{1,2})\.(\d{1,2})\b")
RE_CSF53 = re.compile(
    r"\b(AC|AT|AU|CA|CM|CP|IA|IR|MP|PE|PL|PM|PS|RA|SA|SC|SI|SR|PT)-(\d{1,3})"
    r"(\((\d{1,2})\))?\b"
)
RE_ATTACK = re.compile(r"\bT(?:A)?(\d{4})(?:\.(\d{3}))?\b")
RE_OWASP_YEAR = re.compile(r"\bA(\d{1,2})\s*:\s*(20\d{2})\b", re.I)
RE_OWASP_NEAR = re.compile(r"\bOWASP\b[^.\n]{0,40}?\bA(\d{1,2})\b", re.I)
RE_PCI = re.compile(r"\bReq(?:uirement)?\.?\s*(\d{1,2})(?:\.(\d{1,2}))?\b", re.I)
RE_SOC2 = re.compile(r"\b(CC[1-9]|PI1|A1|C1|P[1-8])(?:\.\d{1,2})?\b")
RE_HIPAA = re.compile(r"\b164\.(\d{3})\(([a-z])\)(?:\((\d)\))?", re.I)
RE_GDPR = re.compile(r"\b(?:GDPR\s+)?Art(?:icle)?\.?\s*(\d{1,2})\b", re.I)

# Published OWASP Top 10 category titles. Used to catch a response that names
# the right-looking ID but attaches the wrong category to it -- e.g. calling
# A05:2021 "Insecure Deserialization" when A05:2021 is Security Misconfiguration
# and deserialization lives at A08.
OWASP_TITLES = {
    "2021": {
        "A01": "broken access control", "A02": "cryptographic failures",
        "A03": "injection", "A04": "insecure design",
        "A05": "security misconfiguration",
        "A06": "vulnerable and outdated components",
        "A07": "identification and authentication failures",
        "A08": "software and data integrity failures",
        "A09": "security logging and monitoring failures",
        "A10": "server-side request forgery",
    },
    "2025": {
        "A01": "broken access control", "A02": "security misconfiguration",
        "A03": "software supply chain failures", "A04": "cryptographic failures",
        "A05": "injection", "A06": "insecure design",
        "A07": "authentication failures",
        "A08": "software or data integrity failures",
        "A09": "logging & alerting failures",
        "A10": "mishandling of exceptional conditions",
    },
}

RE_OWASP_ANY = re.compile(r"\bA(\d{2})\s*:\s*(20\d{2})\b\s*[-–—:]?\s*([A-Za-z][A-Za-z &/-]{2,60})?")


def check_owasp_titles(text: str) -> List[str]:
    """Flag OWASP citations whose ID is real but whose attached category title
    belongs to a different category.

    Example caught: 'A05:2021 Insecure Deserialization' -- A05:2021 is Security
    Misconfiguration; deserialization is A08.
    """
    notes: List[str] = []
    for m in RE_OWASP_ANY.finditer(text):
        num, year = f"A{m.group(1)}", m.group(2)
        titles = OWASP_TITLES.get(year)
        if not titles or num not in titles:
            continue
        claimed = (m.group(3) or "").strip().lower()
        # trim trailing fragments from a following citation, e.g.
        # "broken access control / a" out of "A05:2025 – X / A06:2025 – Y"
        claimed = re.split(r"\s*/\s*a\d{2}\s*:", claimed)[0]
        claimed = re.sub(r"\s*/\s*a?\d{0,2}\s*$", "", claimed).strip(" -–—:/&")
        if not claimed:
            continue
        correct = titles[num]
        # same-category wording means the label is fine
        if correct in claimed or claimed.startswith(correct[:20]):
            continue
        # does the label name some *other* category for this number?
        others = {t for y in OWASP_TITLES.values() for k, t in y.items()
                  if k == num and t != correct}
        mismatch = next((o for o in others if o[:20] in claimed), None)
        if mismatch is None:
            keywords = ("injection", "deserializ", "misconfigur", "access control",
                        "cryptograph", "authentication", "integrity", "ssrf",
                        "logging", "design", "supply chain", "components")
            if not any(k in claimed for k in keywords):
                continue  # free-form wording, not a category claim
            mismatch = claimed[:40]
        notes.append(
            f"{num}:{year} labelled '{claimed[:44]}' but {num}:{year} is '{correct}'"
        )
    return notes


def load_title_index() -> Dict[str, Dict[str, str]]:
    """id -> official title for CIS controls and CWE weaknesses, read from the
    local corpus so title checking uses the same source as citation checking."""
    idx: Dict[str, Dict[str, str]] = {}
    fw_dir = os.path.join(ROOT, "core", "frameworks")

    path = os.path.join(fw_dir, "cis_controls.json")
    if os.path.isfile(path):
        with open(path, "r", encoding="utf-8") as fh:
            idx["CIS_CONTROLS"] = {
                str(r["id"]).upper(): str(r.get("title", ""))
                for r in json.load(fh) if r.get("id")
            }

    path = os.path.join(fw_dir, "cwe_top25.json")
    if os.path.isfile(path):
        titles = {}
        with open(path, "r", encoding="utf-8") as fh:
            for r in json.load(fh):
                text = str(r.get("text", ""))
                m = re.match(r"CWE-\d+:\s*([^.]+)", text)
                if r.get("id") and m:
                    titles[str(r["id"]).upper()] = m.group(1).strip()
        idx["CWE"] = titles
    return idx


def _wordset(s: str) -> set:
    s = re.sub(r"[^a-z0-9 ]+", " ", s.lower())
    return {w for w in s.split() if w}


def _title_score(claimed: str, official: str) -> float:
    """Jaccard over all content words. Short but decisive words (os/sql, xss)
    must count -- they are exactly what distinguishes CWE-78 from CWE-89."""
    a, b = _wordset(claimed), _wordset(official)
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def check_cis_titles(text: str, titles: Dict[str, str]) -> List[str]:
    """Catch 'CIS Control 16: "Vendor Risk Management"' style mislabels.

    CIS-16 is Application Software Security; vendor risk is CIS-15.
    """
    notes: List[str] = []
    # strip edition markers first so 'v8.1' can never be read as a control number
    cleaned = re.sub(r"\bv?\d+\.\d+\b", " ", text)
    pat = re.compile(
        r"CIS\s*(?:Controls?)?\s*(?:Control\s*)?(\d{1,2})"
        r"[\s*_:)\-\u2013\u2014]*[\"'\u201c\u2018]?"
        r"([A-Za-z][A-Za-z0-9 &/,()\x27-]{3,70})",
        re.I)
    for m in pat.finditer(cleaned):
        n = int(m.group(1))
        if not 1 <= n <= 18:
            continue
        key = f"CIS-{n}"
        official = titles.get(key)
        if not official:
            continue
        claimed = re.split(r"[.;\n\"\u201d]", m.group(2))[0].strip()
        claimed = re.sub(r"\s*\(\s*$", "", claimed).strip()
        if len(claimed) < 4:
            continue
        # a title is capitalised prose, not a sentence fragment ("to address the
        # lack of ...") -- skip when the capture starts with a lower-case word
        # that reads as a connective
        if claimed[0].islower() or re.match(
                r"^(to|for|in|of|and|or|with|is|are|was|were|requires?|address\w*)\b",
                claimed, re.I):
            continue
        if _title_score(claimed, official) < 0.34:
            notes.append(f"{key} labelled '{claimed[:40]}' but {key} is '{official}'")
    return notes


def check_cwe_titles(text: str, titles: Dict[str, str]) -> List[str]:
    """Catch a CWE id carrying another weakness's title.

    Example: 'CWE-89 (Improper Neutralization of Special Elements used in an OS
    Command String)' -- that is CWE-78's title; CWE-89 is the SQL variant.

    Comparing only against the cited id's own title is not enough: CWE-78 and
    CWE-89 share the 'Improper Neutralization of Special Elements used in ...'
    boilerplate, so every candidate scores highly. The titled text is therefore
    matched against ALL known titles and only flagged when a different CWE is a
    clearly better fit than the one cited.
    """
    notes: List[str] = []
    pat = re.compile(r"\bCWE-(\d{1,4})\b\s*[\(\u2013\u2014:-]\s*([A-Za-z][^)\.\n]{6,110})")
    for m in pat.finditer(text):
        key = f"CWE-{m.group(1)}"
        claimed = m.group(2).strip()
        if not claimed:
            continue
        scores = {cid: _title_score(claimed, title) for cid, title in titles.items()}
        if not scores:
            continue
        own = scores.get(key, 0.0)
        best_id = max(scores, key=lambda k: scores[k])
        best = scores[best_id]
        if best_id == key:
            continue
        if best >= 0.5 and best - own >= 0.08:
            notes.append(
                f"{key} labelled '{claimed[:52]}' but that is {best_id}'s title "
                f"({titles[best_id][:38]})"
            )
    return notes


# Valid NIST CSF 2.0 function prefixes. A citation like "NIST CSF 2.0 (DR)" or
# "DR" as a function is not a real function identifier.
CSF_FUNCTIONS = {
    "GV": "Govern", "ID": "Identify", "PR": "Protect",
    "DE": "Detect", "RS": "Respond", "RC": "Recover",
}
RE_CSF_FUNC_PAREN = re.compile(
    r"(?:NIST\s+)?CSF\s*(?:2\.0|v?2)?\s*[\(\-]\s*([A-Z]{2,3}\))?")


def check_csf_function_abbrev(text: str) -> List[str]:
    """Flag invented CSF function abbreviations such as '(DR)'.

    CSF 2.0 has exactly six functions: GV, ID, PR, DE, RS, RC. 'DR' mixes
    Detect/Respond and is not a function identifier.
    """
    notes: List[str] = []
    for m in re.finditer(r"\bCSF\s*(?:2\.0|v2(?:\.0)?)?\s*\(\s*([A-Z]{2,4})\s*\)", text):
        tok = m.group(1).upper()
        if tok in CSF_FUNCTIONS:
            continue
        if tok.startswith(("T", "A")) and len(tok) <= 3:
            continue  # TECHNIQUE/OWASP style tokens
        notes.append(
            f"'NIST CSF ({tok})' is not a CSF 2.0 function "
            f"(valid: GV, ID, PR, DE, RS, RC)"
        )
    return notes


def check_iam_deny_logic(text: str) -> List[str]:
    """Catch the AWS IAM evaluation-order error: claiming an explicit Deny is
    overridden by a broader Allow. In IAM an explicit Deny always wins."""
    notes: List[str] = []
    for sent in re.split(r"(?<=[.!?])\s+|\n+", text):
        s = sent.lower()
        if "deny" not in s:
            continue
        if "overrid" not in s and "supersede" not in s and "precedence" not in s:
            continue
        if "allow" not in s:
            continue
        # a correct statement says Deny overrides / takes precedence over Allow
        deny_wins = re.search(
            r"deny[^.]{0,80}(override|takes?\s+precedence|wins?|supersede)", s) or \
            re.search(r"explicit\s+deny[^.]{0,60}(always|takes?\s+prec|win)", s)
        allow_wins = re.search(
            r"(allow|broader\s+allow)[^.]{0,80}(override|takes?\s+precedence|wins?|supersede)", s) or \
            re.search(r"deny[^.]{0,90}(is|being)\s+(likely\s+)?(overrid|supersed|negat)", s)
        if allow_wins and not deny_wins:
            notes.append(
                "claims explicit Deny can be overridden by Allow — in AWS IAM an "
                "explicit Deny always overrides any Allow"
            )
    return notes


def _norm_53(cid: str) -> str:
    """'CM-01' -> 'CM-1'; SP 800-53 writes base controls unpadded."""
    m = re.match(r"^([A-Z]{2})-0*(\d{1,3})((?:\([0-9A-Za-z]+\))*)$", cid.upper())
    if not m:
        return cid.upper()
    return f"{m.group(1)}-{m.group(2)}{m.group(3)}"


def extract_citations(text: str) -> Dict[str, List[str]]:
    """Pull every framework identifier out of a response, normalised."""
    out: Dict[str, List[str]] = {}

    def add(family: str, value: str) -> None:
        out.setdefault(family, [])
        if value not in out[family]:
            out[family].append(value)

    for m in RE_CSF.finditer(text):
        cat, sub = m.group(1).upper(), m.group(2)
        if sub:
            # normalise to zero-padded 2-digit form (PR.AC-5 -> PR.AC-05)
            add("NIST_CSF", f"{cat}-{int(sub[1:]):02d}")
        else:
            add("NIST_CSF", cat)

    for m in RE_CWE.finditer(text):
        add("CWE", f"CWE-{m.group(1)}")

    for m in RE_CIS_DASH.finditer(text):
        n = int(m.group(1))
        if 1 <= n <= 18:
            add("CIS_CONTROLS", f"CIS-{n}")
    for m in RE_CIS_WORD.finditer(text):
        n = int(m.group(1))
        if 1 <= n <= 18:
            add("CIS_CONTROLS", f"CIS-{n}")

    for m in RE_ISO.finditer(text):
        a, b = int(m.group(1)), int(m.group(2))
        if 5 <= a <= 8:
            add("ISO_27001", f"A.{a}.{b}")

    for m in RE_CSF53.finditer(text):
        base = _norm_53(f"{m.group(1)}-{m.group(2)}")
        if m.group(4):
            base += f"({m.group(4)})"
        add("NIST_800-53", base)

    for m in RE_ATTACK.finditer(text):
        tok = ("TA" if "TA" in m.group(0).upper() else "T") + m.group(1)
        if m.group(2):
            tok += "." + m.group(2)
        add("MITRE_ATTACK", tok)

    # OWASP: a year-qualified form wins over a bare category number. Bare
    # references are only recorded when the text contains no :20XX form of the
    # same category, so one response cannot inflate the count with duplicates.
    owasp_year: Dict[str, str] = {}
    for m in RE_OWASP_YEAR.finditer(text):
        n = int(m.group(1))
        if 1 <= n <= 10:
            owasp_year[f"A{n:02d}"] = f"A{n:02d}:{m.group(2)}"
    for key in sorted(owasp_year):
        add("OWASP_TOP10", owasp_year[key])
    for m in RE_OWASP_NEAR.finditer(text):
        key = f"A{int(m.group(1)):02d}"
        if int(m.group(1)) <= 10 and key not in owasp_year:
            add("OWASP_TOP10", key)

    for m in RE_PCI.finditer(text):
        n = int(m.group(1))
        if 1 <= n <= 12:
            add("PCI_DSS", f"Req{n}")

    for m in RE_SOC2.finditer(text):
        add("SOC_2", m.group(1).upper())

    for m in RE_HIPAA.finditer(text):
        base = f"164.{m.group(1)}({m.group(2).lower()})".upper()
        if m.group(3):
            base += f"({m.group(3)})"
        add("HIPAA", base)

    for m in RE_GDPR.finditer(text):
        n = int(m.group(1))
        if 1 <= n <= 99:
            add("GDPR", f"Art.{n}")

    return out


def _base_53(cid: str) -> str:
    return cid.split("(")[0].upper()


def verify_id(family: str, cid: str, local: Dict[str, set], reg: Dict[str, set]) -> Tuple[bool, bool, str]:
    """Return (in_local_corpus, exists_in_registry, note)."""
    up = cid.upper()

    if family == "NIST_CSF":
        in_local = up in local.get("NIST_CSF", set())
        cat = up.split("-")[0]
        if "-" in up:  # subcategory form
            in_local = up in local.get("NIST_CSF", set())
            if up in reg.get("NIST_CSF_SUB", set()):
                return in_local, True, ""
            if up in reg.get("NIST_CSF_SUB_WITHDRAWN", set()):
                return in_local, True, "CSF 1.1 subcategory withdrawn in CSF 2.0"
            if cat in reg.get("NIST_CSF_WITHDRAWN", set()):
                return in_local, True, "CSF 1.1 category withdrawn in CSF 2.0"
            return in_local, False, "subcategory id not in published CSF 2.0 core"
        if cat in reg.get("NIST_CSF_CAT", set()):
            exists, note = True, ""
        elif cat in reg.get("NIST_CSF_WITHDRAWN", set()):
            exists, note = True, "CSF 1.1 category withdrawn in CSF 2.0"
        else:
            exists, note = False, "category does not exist in CSF 2.0"
        return in_local, exists, note

    if family == "CWE":
        in_local = up in local.get("CWE_TOP25", set())
        return in_local, up in reg.get("CWE", set()), ""

    if family == "CIS_CONTROLS":
        in_local = up in local.get("CIS_CONTROLS", set())
        return in_local, up in reg.get("CIS_CONTROLS", set()), ""

    if family == "ISO_27001":
        # local corpus holds ISO/IEC 27001:2022 Annex A (controls A.5-A.8)
        in_local = up in local.get("ISO_27001", set())
        if in_local:
            return True, True, ""
        # ISO 27001:2013 Annex A used A.5-A.18. A.17.x is the 2013 numbering for
        # business continuity; ::2022 folded it into A.5.29/A.5.30 and A.8.13/14
        m = re.match(r"A\.(\d{1,2})\.(\d{1,2})", up)
        if m and 5 <= int(m.group(1)) <= 18:
            if int(m.group(1)) > 8:
                return False, True, "ISO 27001:2013 Annex A numbering (2013 edition replaced by :2022)"
            return False, True, "not in ISO 27001:2022 Annex A"
        return False, False, "control id not in ISO 27001 Annex A"

    if family == "NIST_800-53":
        in_local = up in local.get("NIST_800-53", set()) or _base_53(up) in local.get("NIST_800-53", set())
        base = _base_53(up)
        exists = base in reg.get("NIST_800-53", set())
        note = "" if exists else "control id not in SP 800-53 Rev 5 catalog"
        if exists and not in_local:
            note = "enhancement/base not in local corpus"
        return in_local, exists, note

    if family == "MITRE_ATTACK":
        in_local = up in local.get("MITRE_ATTACK", set())
        base = up.split(".")[0]
        if "." in up:
            # a sub-technique must exist exactly; falling back to the base id
            # would silently accept an invented sub-technique such as T1190.001
            if up in reg.get("MITRE_ATTACK", set()):
                return in_local, True, ""
            if base in reg.get("MITRE_ATTACK", set()):
                return in_local, False, "sub-technique id does not exist in ATT&CK enterprise"
            return in_local, False, "technique id does not exist in ATT&CK enterprise"
        return in_local, up in reg.get("MITRE_ATTACK", set()), ""

    if family == "OWASP_TOP10":
        # local corpus holds the 2025 edition only
        in_local = up in local.get("OWASP_TOP10", set())
        if ":" in up:
            num, _, year = up.partition(":")
            # both 2021 and 2025 are real published editions
            exists = year in OWASP_TITLES and num in OWASP_TITLES[year]
            if not exists:
                note = "not a published Top10 category id"
            elif year != "2025":
                note = f"{year} edition (local corpus holds 2025 only)"
            else:
                note = ""
        else:
            # bare A01 -- resolvable when A01:2025 exists, but it is a citation
            # without an edition, so record the imprecision
            norm = f"{up}:2025"
            exists = norm in reg.get("OWASP_TOP10", set())
            note = "cited without edition/year" if exists else "category number out of range"
        return in_local, exists, note

    if family == "PCI_DSS":
        in_local = up in local.get("PCI_DSS", set())
        return in_local, up in reg.get("PCI_DSS", set()), ""

    if family == "SOC_2":
        in_local = up in local.get("SOC_2", set())
        return in_local, up in reg.get("SOC_2", set()), ""

    if family == "HIPAA":
        in_local = up in local.get("HIPAA", set())
        # subparagraph citations (164.312(a)(1)) roll up to their base paragraph
        base = re.sub(r"\(\d\)$", "", up)
        exists = up in reg.get("HIPAA", set()) or base in reg.get("HIPAA", set())
        return in_local, exists, ""

    if family == "GDPR":
        in_local = up in local.get("GDPR", set())
        return in_local, up in reg.get("GDPR", set()), ""

    return False, False, "unknown family"


def audit_citations(text: str, local: Dict[str, set], reg: Dict[str, set],
                    required_editions: Optional[Dict[str, str]] = None,
                    apply_policy_checks: bool = False,
                    titles: Optional[Dict[str, Dict[str, str]]] = None) -> Dict[str, Any]:
    """Audit every framework ID in a response.

    required_editions maps a family to the edition the prompt demanded, e.g.
    {"OWASP_TOP10": "2025"}. A real ID from the wrong edition is a defect but
    not a fabrication, so it is reported as an edition mismatch instead of
    being lumped in with invented IDs.
    """
    found = extract_citations(text)
    required_editions = required_editions or {}
    titles = titles or {}
    verified, corpus_gap, invented, withdrawn, edition_mismatch = [], [], [], [], []
    legacy_edition: List[str] = []

    for family, ids in found.items():
        want_edition = required_editions.get(family)
        for cid in ids:
            in_local, exists, note = verify_id(family, cid, local, reg)
            tag = f"{family}:{cid}"
            if exists and want_edition and ":" in cid:
                got = cid.split(":")[-1]
                if got != want_edition:
                    edition_mismatch.append(f"{tag} (prompt required {want_edition})")
                    if note:
                        edition_mismatch[-1] += f" [{note}]"
                    continue
            if in_local:
                verified.append(tag)
            elif exists:
                corpus_gap.append(tag if not note else f"{tag} ({note})")
                if "withdrawn" in note:
                    withdrawn.append(tag)
                elif "2013" in note:
                    legacy_edition.append(tag)
            else:
                invented.append(tag if not note else f"{tag} ({note})")

    title_notes = (check_owasp_titles(text)
                   + check_cis_titles(text, titles.get("CIS_CONTROLS", {}))
                   + check_cwe_titles(text, titles.get("CWE", {})))
    func_notes = check_csf_function_abbrev(text)
    iam_notes = check_iam_deny_logic(text)
    false_absence = detect_false_absence_claims(text) if apply_policy_checks else []

    return {
        "by_family": found,
        "verified_local": sorted(verified),
        "corpus_gap": sorted(corpus_gap),
        "invented": sorted(invented + func_notes),
        "withdrawn_ids": sorted(withdrawn),
        "edition_mismatch": sorted(edition_mismatch + legacy_edition),
        "title_notes": title_notes,
        "iam_logic_notes": iam_notes,
        "false_absence_claims": false_absence,
        "total_ids": sum(len(v) for v in found.values()),
    }


# --------------------------------------------------------------------------- #
# Ground-truth checklists
# --------------------------------------------------------------------------- #

# Ground-truth checklists.
# Each item is (name, [aspect_patterns]). An item scores full when every aspect
# matches, partial when some do, missing when none do -- so a response that
# names DES/MD5 but never the plaintext PSK is recorded as partial rather than
# silently counted as a total miss.
GT_FIREWALL = [
    ("wan_permit_all", [r"permit\s+ip\s+any\s+any|PERMIT_ALL"]),
    ("default_mgmt_creds", [r"admin123|cisco123|P@ssw0rd123|default\s+(management\s+|admin\s+)?(password|credential|login)"]),
    ("telnet_mgmt", [r"\btelnet\b"]),
    ("http_mgmt", [r"ip\s+http\s+server|http\s+server|plain-?text\s+http|unencrypted\s+http|http\s+management"]),
    ("des_md5_vpn", [r"\bdes\b", r"\bmd5\b"]),
    ("plaintext_psk", [r"(pre-?shared\s*(key|secret)|isakmp\s+key|mysecretpass|plaintext\s*(psk|pre-?shared))"]),
    ("snmp_community_public", [r"\bsnmp\b", r"\bpublic\b"]),
]

GT_IAM = [
    ("principal_star", [r"principal\W{0,20}\*|FullAdminAccess"]),
    ("wildcard_action", [r"action\W{0,15}(\"|')?\*|action\W{0,10}:\s*wildcard|full\s+admin"]),
    ("mfa_denial", [r"DenyMFASetup|deny[\s\S]{0,80}(mfa|multi-factor)|(mfa|multi-factor)[\s\S]{0,80}(deni|block|prevent)"]),
    ("cloudtrail_stop_delete", [r"stop\s*logging|StopLogging|DeleteTrail|cloudtrail[\s\S]{0,80}(stop|delete)"]),
]

# Sample 3 has no official checkpoint list in the task brief, so these are the
# gaps verified by reading the document itself: it mandates SIEM (10.5), so a
# "no SIEM" checkpoint would be a false item and is deliberately excluded.
GT_LARGE_POLICY = [
    ("asset_inventory", [r"asset\s+(inventory|register|management|discovery|classification)|inventory\s+of\s+(all\s+)?(assets|hardware|software)|cmdb"]),
    ("risk_assessment_register", [r"risk\s+(assessment|register|appetite|treatment|methodology|analysis)"]),
    ("business_continuity_dr", [r"business\s+continuity|disaster\s+recovery|\bbcp\b|\bdr\s+(plan|test|site)\b"]),
    ("exception_waiver_process", [r"exception\s+(process|handling|management)|\bwaiver\b|deviation\s+(process|approval)"]),
    ("supplier_due_diligence", [r"(vendor|third[- ]party|supplier)[\s\S]{0,150}(assessment|due\s+diligence|risk\s+(tier|rating)|security\s+questionnaire)"]),
    ("key_management", [r"key\s+(management|lifecycle|rotation|custody)|cryptographic\s+key"]),
    ("personnel_screening", [r"(background|pre-?employment)\s+(check|screen|verif)"]),
]

GT_VULN = [
    ("hardcoded_credentials", [r"hard-?coded[\s\S]{0,80}(credential|password|secret|api\s*key)|Sup3rS3cret|DB_PASS"]),
    ("md5_password_hashing", [r"\bmd5\b[\s\S]{0,40}(hash|password)|(hash|password)[\s\S]{0,40}\bmd5\b|\bmd5\b"]),
    ("sql_injection", [r"\bsql\s*injection|\bsqli\b"]),
    ("admin_endpoint_no_authz", [
        r"(no|missing|lacks?|without|absent)[\s\S]{0,120}(authoriz|authentication|access\s+control)",
        r"(authoriz|authentication)[\s\S]{0,120}(not\s+(checked|enforced|verified)|missing|lacks?)",
        r"\bidor\b|broken\s+access\s+control|insecure\s+direct\s+object",
    ]),
    ("password_hash_exposure", [r"(expos|leak|return|disclos)\w*[\s\S]{0,60}(password\s+hash|hashes)"]),
    ("command_injection", [r"command\s+injection|shell\s*=\s*True|os\s+command\s+injection|subprocess"]),
    ("insecure_deserialization", [r"pickle|insecure\s+deserializ|unsafe\s+deserializ"]),
    ("path_traversal", [r"path\s+traversal|directory\s+traversal|\.\./"]),
    ("ssrf", [r"\bssrf\b|server-?side\s+request\s+forgery"]),
    ("unsafe_yaml_load", [r"yaml\.load|unsafe\s+yaml|yaml\s+deserializ"]),
    ("debug_mode_exposed", [r"debug\s*=\s*True|debug\s+mode|debugger"]),
]


# Controls the sample policy DOES mandate, with the section that proves it.
# A response claiming one of these is missing is asserting a false negative --
# a materially worse error than an omission, so it is detected explicitly.
POLICY_PRESENT_CONTROLS = [
    ("MFA", r"\bmulti-?factor\b|\bMFA\b", "3.4"),
    ("SIEM", r"\bSIEM\b", "10.5"),
    ("encryption_at_rest", r"\bencrypt\w*\s+at\s+rest\b|\bAES-256\b", "4.3"),
    ("encryption_in_transit", r"\bTLS\s+1\.2\b", "4.4"),
    ("vulnerability_scanning", r"\bvulnerability\s+scan", "9.1"),
    ("penetration_testing", r"\bpenetration\s+test|\bpentest", "9.3"),
    ("patch_management", r"\bpatch(es)?\s+(management|applied)", "6.3"),
    ("incident_response", r"\bincident\s+response\b|\bincident\s+classification\b", "8.1"),
    ("code_review", r"\bpeer\s+review\b|\bcode\s+review\b", "7.2"),
    ("access_reviews", r"\baccess\s+reviews?\b", "2.3"),
    ("security_awareness_training", r"\bsecurity\s+awareness\s+training\b", "12.2"),
    ("phishing_simulation", r"\bphishing\s+simulation", "12.3"),
    ("backup", r"\bbackup", "4.7"),
    ("logging", r"\blogging\b|\bcloudtrail\b|\baudit\s+log", "10.5"),
    ("data_classification", r"\bclassification\s+levels?\b|\bdata\s+classification\b", "4.1"),
    ("third_party_risk", r"\bthird-?party\b|\bvendor", "2.9,10.1"),
    ("supply_chain", r"\bSBOM\b|\bsupply\s+chain\b|\bdependenc", "7.3"),
    ("container_security", r"\bcontainer\s+image", "7.6"),
    ("iam_federation", r"\bSAML\b|\bOIDC\b|\bidentity\s+provider\b", "3.8"),
    ("risk_acceptance", r"\brisk\s+acceptance\b", "9.5"),
    ("incident_severity_tiers", r"\bP1\b|\bP2\b|\bseverity", "8.1"),
    ("change_management", r"\bchange\s+management\b", "5.2"),
    ("remote_access_vpn", r"\bVPN\b", "2.7,5.4"),
    ("secrets_management", r"\bSecrets\s+Manager\b", "7.7"),
]

# Negative-claim patterns: "lacks MFA", "fails to mandate MFA", "no MFA policy"
_NEG_BEFORE = re.compile(
    r"(lack\w*|fail\w*\s+to|absent|missing|without|insufficient|omit\w*|"
    r"does\s+not|doesn't|never|\bno)\s+(?:\w+\s+){0,4}$", re.I)
_NEG_AFTER = re.compile(
    r"^\s*(?:is|are)?\s*(?:not\s+)?(?:mandat\w*|required|present|defined|addressed|"
    r"specified|documented|covered|enforced|established|mentioned|included)\b",
    re.I)

# A sentence that reads as a positive requirement itself is evidence of presence,
# not of a false absence claim ("MFA is required for all users").
_POSITIVE_REQUIREMENT = re.compile(
    r"\b(must|shall|is|are)\s+(be\s+)?(required|mandated|enforced)\b|"
    r"\brequires?\b|\bmandates?\b", re.I)


def detect_false_absence_claims(text: str) -> List[str]:
    """Find statements asserting a control is absent when the sample document
    actually mandates it. Sentence-scoped, and skips sentences that are merely
    restating the requirement."""
    notes: List[str] = []
    sentences = re.split(r"(?<=[.!?;])\s+|\n+", text)
    for sent in sentences:
        if not sent.strip():
            continue
        if _POSITIVE_REQUIREMENT.search(sent) and not re.search(
                r"\b(lack\w*|fails?\s+to|absent|missing|not\s+mandat|no\s+\w+\s+(policy|control|requirement))\b",
                sent, re.I):
            continue
        for name, pat, section in POLICY_PRESENT_CONTROLS:
            hit = False
            for m in re.finditer(pat, sent, re.I):
                before = sent[max(0, m.start() - 90):m.start()]
                after = sent[m.end():m.end() + 80]
                if _NEG_BEFORE.search(before) or _NEG_AFTER.search(after):
                    notes.append(
                        f"claims {name} missing/absent, but section {section} requires it"
                    )
                    hit = True
                    break
            if hit:
                break
    seen, out = set(), []
    for n in notes:
        if n not in seen:
            seen.add(n)
            out.append(n)
    return out


def score_gt(text: str, checklist: List[Tuple]) -> Dict[str, Any]:
    """Grades each checklist item as full / partial / missing."""
    found, partial, missing = [], [], []
    for item in checklist:
        name, aspects = item[0], item[1]
        hits = sum(1 for p in aspects if re.search(p, text, re.I | re.S))
        if hits == len(aspects):
            found.append(name)
        elif hits:
            partial.append(name)
        else:
            missing.append(name)
    total = len(checklist)
    score = len(found) + 0.5 * len(partial)
    label = f"{len(found)}/{total}"
    if partial:
        label += f" (+{len(partial)} partial)"
    return {
        "found": found, "partial": partial, "missing": missing,
        "score": label, "numeric": score, "total": total,
    }


# --------------------------------------------------------------------------- #
# Test specification
# --------------------------------------------------------------------------- #

TESTS = [
    {
        "test_id": "T01", "sample": "firewall-config.txt",
        "engagement": "Lab1-Firewall", "role": "general",
        "prompt": "What are the security issues in this firewall configuration?",
        "expected": "none_requested", "gt": GT_FIREWALL,
    },
    {
        "test_id": "T02", "sample": "firewall-config.txt",
        "engagement": "Lab1-Firewall", "role": "general",
        "prompt": ("Which finding is highest risk and why? What remediation order would "
                   "you recommend for the top three findings, and which NIST CSF "
                   "functions do they map to?"),
        "expected": "NIST_CSF(categories)", "gt": GT_FIREWALL,
    },
    {
        "test_id": "T03", "sample": "iam-policy.json",
        "engagement": "Lab2-IAM", "role": "general",
        "prompt": "What are the security issues in this IAM policy?",
        "expected": "none_requested", "gt": GT_IAM,
    },
    {
        "test_id": "T04", "sample": "iam-policy.json",
        "engagement": "Lab2-IAM", "role": "general",
        "prompt": ("Which finding is highest risk and why? For each finding, cite the "
                   "exact section and map it to NIST CSF, CIS Controls, or ISO 27001 "
                   "control IDs."),
        "expected": "NIST_CSF;CIS_CONTROLS;ISO_27001", "gt": GT_IAM,
    },
    {
        "test_id": "T05", "sample": "large-policy-document.txt",
        "engagement": "Lab3-LargePolicy", "role": "general",
        "prompt": "What are the biggest security gaps in this policy?",
        "expected": "none_requested", "gt": GT_LARGE_POLICY,
        "policy_checks": True,
    },
    {
        "test_id": "T06", "sample": "large-policy-document.txt",
        "engagement": "Lab3-LargePolicy", "role": "grc",
        "prompt": "What are the biggest security gaps in this policy?",
        "expected": "NIST_CSF;CIS_CONTROLS;ISO_27001", "gt": GT_LARGE_POLICY,
        "policy_checks": True,
    },
    {
        "test_id": "T08", "sample": "vulnerable-app.py",
        "engagement": "Lab4-VulnApp", "role": "code",
        "prompt": "What vulnerabilities are in this application?",
        "expected": "CWE(optional)", "gt": GT_VULN,
        "required_editions": {},
    },
    {
        "test_id": "T09", "sample": "vulnerable-app.py",
        "engagement": "Lab4-VulnApp", "role": "code",
        "prompt": ("Map each vulnerability to its exact CWE ID and OWASP Top 10:2025 "
                   "category, citing the exact function name and line."),
        "expected": "CWE;OWASP_TOP10:2025", "gt": GT_VULN,
        "required_editions": {"OWASP_TOP10": "2025"},
    },
]


def determine_citation_hit(expected: str, audit: Dict[str, Any]) -> str:
    fams = audit["by_family"]
    if expected.startswith("none_requested"):
        return "n/a"
    if expected.startswith("CWE(optional)"):
        return "yes" if "CWE" in fams else "partial"
    want = [e.split("(")[0] for e in re.split(r"[;]", expected)]
    hit = 0
    for w in want:
        w = w.strip()
        if not w:
            continue
        if w == "NIST_CSF" and "NIST_CSF" in fams:
            hit += 1
        elif w == "CIS_CONTROLS" and "CIS_CONTROLS" in fams:
            hit += 1
        elif w == "ISO_27001" and "ISO_27001" in fams:
            hit += 1
        elif w == "CWE" and "CWE" in fams:
            hit += 1
        elif w.startswith("OWASP_TOP10") and "OWASP_TOP10" in fams:
            hit += 1
    if hit == len([w for w in want if w.strip()]):
        return "yes"
    return "partial" if hit else "no"


def hallucination_reasons(audit: Dict[str, Any]) -> List[str]:
    """Collect every signal that counts as a citation/semantic hallucination.

    A real control that is merely absent from the local seed corpus
    (corpus_gap) is NOT a hallucination -- the corpus is a subset of the real
    framework, so absence there says nothing about correctness.
    """
    reasons: List[str] = []
    if audit.get("invented"):
        reasons.append("invented_id")
    if audit.get("title_notes"):
        reasons.append("mislabelled_title")
    if audit.get("false_absence_claims"):
        reasons.append("false_absence")
    if audit.get("edition_mismatch"):
        reasons.append("wrong_edition")
    if audit.get("withdrawn_ids"):
        reasons.append("withdrawn_id")
    if audit.get("iam_logic_notes"):
        reasons.append("domain_logic_error")
    return reasons


def is_hallucination(audit: Dict[str, Any]) -> bool:
    return bool(hallucination_reasons(audit))


def build_notes(audit: Dict[str, Any]) -> str:
    """One-line, human-readable evidence string for the CSV."""
    notes: List[str] = []
    if audit.get("invented"):
        notes.append("INVENTED: " + "; ".join(audit["invented"]))
    if audit.get("title_notes"):
        notes.append("MISLABELLED: " + "; ".join(audit["title_notes"]))
    if audit.get("edition_mismatch"):
        notes.append("WRONG_EDITION: " + "; ".join(audit["edition_mismatch"]))
    if audit.get("iam_logic_notes"):
        notes.append("DOMAIN_LOGIC: " + "; ".join(audit["iam_logic_notes"]))
    if audit.get("false_absence_claims"):
        notes.append("FALSE_ABSENCE: " + "; ".join(audit["false_absence_claims"]))
    if audit.get("withdrawn_ids"):
        notes.append("WITHDRAWN: " + "; ".join(audit["withdrawn_ids"]))
    if audit.get("corpus_gap"):
        notes.append("CORPUS_GAP: " + "; ".join(audit["corpus_gap"]))
    if not notes:
        notes.append("all cited ids resolve to core/frameworks/")
    return " | ".join(notes)


def provisional_weakness_tag(audit: Dict[str, Any], gt: Dict[str, Any], total_gt: int) -> str:
    n = len(gt["found"])
    p = len(gt.get("partial", []))
    if audit.get("invented"):
        return "hallucinated_control_id"
    if audit.get("iam_logic_notes"):
        return "domain_logic_error"
    if audit.get("false_absence_claims"):
        return "false_absence_claim"
    if audit.get("title_notes"):
        return "mislabelled_framework_category"
    if audit.get("edition_mismatch"):
        return "wrong_framework_edition"
    if audit.get("withdrawn_ids"):
        return "withdrawn_framework_id"
    if total_gt and n == 0 and p == 0:
        return "no_gt_coverage"
    if total_gt and (n + p) < total_gt:
        return "incomplete_gt_coverage"
    if p:
        return "partial_gt_coverage"
    if audit["corpus_gap"]:
        return "corpus_coverage_gap"
    return "none_observed"


# --------------------------------------------------------------------------- #
# Server interaction
# --------------------------------------------------------------------------- #


def preflight() -> Dict[str, Any]:
    log("Pre-flight: /health")
    health = get_json("/health", timeout=60)
    if health.get("status") != "ok":
        raise SystemExit(
            f"STOP: server not healthy -> status={health.get('status')!r} "
            f"detail={health.get('detail')!r}. Select the real model in the "
            f"Fortis UI (Settings > Model) and re-run."
        )

    log("Pre-flight: /api/model/status")
    ms = get_json("/api/model/status", timeout=90)
    active = (ms.get("active_model") or "").replace("\\", "/").split("/")[-1]
    warning = ms.get("warning")
    if not active:
        raise SystemExit("STOP: no active model file. Choose a model in Settings > Model.")
    if warning and "stub" in warning.lower():
        raise SystemExit(f"STOP: engine reports stub backend -> {warning}")

    info = {
        "health": health,
        "active_model_file": active,
        "model_loaded_at_preflight": ms.get("loaded"),
        "model_warning": warning,
        "hardware": ms.get("hardware"),
        "suggested_tier": ms.get("suggested_tier"),
        "captured_at": datetime.now(timezone.utc).isoformat(),
    }
    log(f"  model = {active}")
    log(f"  health.detail = {health.get('detail')!r}  warning = {warning!r}")
    log(f"  vram = {ms.get('hardware', {}).get('vram_gb')} GB, "
        f"wheel = {ms.get('hardware', {}).get('backend_wheel')}, "
        f"offload = {ms.get('hardware', {}).get('offload')}")

    # effective config, read from the app's own module (read-only import)
    sys.path.insert(0, ROOT)
    try:
        from core.config import settings  # noqa: WPS433
        info["rag_level"] = settings.rag_level
        info["hierarchical_threshold_tokens"] = settings.hierarchical_threshold_tokens
        info["map_batch_tokens"] = settings.map_batch_tokens
        info["n_ctx"] = settings.n_ctx
        info["n_gpu_layers"] = settings.n_gpu_layers
        info["top_k_user_doc"] = settings.top_k_user_doc
        info["top_k_framework"] = settings.top_k_framework
        log(f"  RAG_LEVEL = {settings.rag_level} (threshold "
            f"{settings.hierarchical_threshold_tokens} tokens)")
        if settings.rag_level != "standard":
            raise SystemExit(
                f"STOP: RAG_LEVEL={settings.rag_level!r}, baseline requires 'standard'. "
                f"Fix .env and restart Fortis."
            )
    except SystemExit:
        raise
    except Exception as exc:  # pragma: no cover
        log(f"  ! could not read core.config in-process: {exc}")
        info["rag_level"] = "unknown"

    with open(PREFLIGHT_PATH, "w", encoding="utf-8") as fh:
        json.dump(info, fh, indent=2)
    return info


def create_or_reset_engagement(name: str, reset_docs: bool) -> str:
    eng = post_json("/engagements", {
        "client_name": CLIENT_NAME, "engagement_name": name, "notes": "Step 2.1 baseline",
    }, timeout=60)
    eid = eng["id"]
    if reset_docs:
        try:
            _req("DELETE", f"/upload/engagement/{eid}", timeout=60)
        except ApiError:
            pass
        try:
            _req("DELETE", f"/chat/history/{eid}?confirm=true", timeout=60)
        except ApiError:
            pass
    return eid


def upload_doc(engagement_id: str, path: str) -> Dict[str, Any]:
    fname = os.path.basename(path)
    with open(path, "rb") as fh:
        files = {"file": (fname, fh, "application/octet-stream")}
        resp = _req("POST", "/upload", UPLOAD_TIMEOUT,
                    files=files, data={"engagement_id": engagement_id})
    return resp.json()


def confirm_indexing(engagement_id: str, expected_filename: str) -> List[str]:
    files = get_json(f"/upload/engagement/{engagement_id}/files", timeout=60)
    names = files.get("files", [])
    if expected_filename not in names:
        raise ApiError(
            f"indexing not confirmed: {expected_filename!r} absent from {names!r}"
        )
    return names


def chat_turn(engagement_id: str, message: str, role: str,
              history: List[Dict[str, str]]) -> Tuple[str, float]:
    payload: Dict[str, Any] = {"engagement_id": engagement_id, "message": message}
    if role and role != "none":
        payload["role"] = role
    if history:
        payload["history"] = history
    t0 = time.perf_counter()
    data = post_json("/chat", payload, CHAT_TIMEOUT)
    elapsed = time.perf_counter() - t0
    return data.get("reply", ""), elapsed


def generate_report(engagement_id: str) -> Dict[str, Any]:
    """POST /report/generate, polling /api/report/progress to capture which
    analysis path the app actually took."""
    snapshots: List[Dict[str, Any]] = []
    stop = threading.Event()

    def poll() -> None:
        while not stop.is_set():
            try:
                snap = get_json("/api/report/progress", timeout=15)
                key = (snap.get("stage"), snap.get("done"), snap.get("total"), snap.get("detail"))
                if not snapshots or snapshots[-1].get("_key") != key:
                    snap["_key"] = key
                    snapshots.append(snap)
            except Exception:
                pass
            time.sleep(0.4)

    th = threading.Thread(target=poll, daemon=True)
    th.start()
    t0 = time.perf_counter()
    try:
        result = post_json("/report/generate", {
            "engagement_id": engagement_id, "format": "docx",
            "focus_instructions": "", "allow_stub": False,
        }, REPORT_TIMEOUT)
        elapsed = time.perf_counter() - t0
    finally:
        stop.set()
        th.join(timeout=3)

    stages = [s for s in snapshots if s.get("stage") not in (None, "idle")]
    path = "unknown"
    for s in snapshots:
        d = (s.get("detail") or "").lower()
        if "single-pass" in d:
            path = "flat"
        elif "batches across" in d:
            path = "map-reduce"
    if path == "unknown" and stages:
        path = "map-reduce" if any((s.get("total") or 0) > 1 for s in stages) else "flat"

    return {
        "latency_sec": round(elapsed, 2),
        "result": result,
        "path": path,
        "progress_snapshots": [{k: v for k, v in s.items() if k != "_key"} for s in snapshots],
    }


# --------------------------------------------------------------------------- #
# CSV
# --------------------------------------------------------------------------- #


def write_rows(rows: List[Dict[str, Any]]) -> None:
    existing: List[Dict[str, str]] = []
    if os.path.isfile(CSV_PATH):
        with open(CSV_PATH, "r", encoding="utf-8", newline="") as fh:
            reader = csv.DictReader(fh)
            header = reader.fieldnames or []
            if header[:len(CSV_HEADER)] != CSV_HEADER:
                log(f"  ! existing CSV header differs, preserving as-is: {header}")
            existing = [r for r in reader]
    new_ids = {r["test_id"] for r in rows}
    kept = [r for r in existing if r.get("test_id") not in new_ids]
    kept.extend(rows)
    kept.sort(key=lambda r: (r.get("sample_file", ""), r.get("test_id", "")))
    with open(CSV_PATH, "w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=CSV_HEADER, extrasaction="ignore")
        w.writeheader()
        for r in kept:
            w.writerow({k: r.get(k, "") for k in CSV_HEADER})
    log(f"  CSV written: {CSV_PATH} ({len(kept)} rows total)")


# --------------------------------------------------------------------------- #
# Main
# --------------------------------------------------------------------------- #


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--skip-report", action="store_true", help="skip T07 report generation")
    args = ap.parse_args()

    ensure_dirs()
    info = preflight()

    local = load_local_corpus()
    reg = load_registry()
    titles = load_title_index()
    log(f"Reference sets: local corpus {sum(len(v) for v in local.values())} ids, "
        f"registry {sum(len(v) for v in reg.values())} ids")

    rag_level = info.get("rag_level", "unknown")

    # ---- warm-up so model load does not inflate T01 -----------------------
    log("Warm-up turn (excluded from measurements)")
    warm_id = create_or_reset_engagement("Baseline-Warmup", reset_docs=True)
    upload_doc(warm_id, os.path.join(SAMPLES, "firewall-config.txt"))
    try:
        reply, warm_elapsed = chat_turn(warm_id, "Reply with the single word READY.", "general", [])
        log(f"  warm-up reply ({warm_elapsed:.1f}s): {reply[:60]!r}")
    except ApiError as exc:
        log(f"  ! warm-up failed: {exc}")
    # tear the throwaway engagement down
    try:
        _req("DELETE", f"/engagements/{warm_id}", timeout=60)
        log("  warm-up engagement deleted")
    except ApiError as exc:
        log(f"  ! could not delete warm-up engagement: {exc}")

    post = get_json("/api/model/status", timeout=90)
    hw = post.get("hardware", {})
    offload = hw.get("offload")
    wheel = hw.get("backend_wheel")
    if offload in ("full", "partial"):
        hardware_mode = f"gpu_{offload}({wheel})"
    elif offload == "cpu":
        hardware_mode = "cpu"
    else:
        hardware_mode = f"unknown({wheel})"
    log(f"Post-warmup: loaded={post.get('loaded')} offload={offload} -> hardware_mode={hardware_mode}")

    info["post_warmup"] = {
        "loaded": post.get("loaded"), "offload": offload,
        "offload_layers": hw.get("offload_layers"),
        "backend_wheel": wheel, "hardware_mode": hardware_mode,
    }
    with open(PREFLIGHT_PATH, "w", encoding="utf-8") as fh:
        json.dump(info, fh, indent=2)

    model_name = info["active_model_file"]

    # ---- engagement setup ------------------------------------------------
    engagements: Dict[str, str] = {}
    for t in TESTS:
        name = t["engagement"]
        if name in engagements:
            continue
        eid = create_or_reset_engagement(name, reset_docs=True)
        engagements[name] = eid
        up = upload_doc(eid, os.path.join(SAMPLES, t["sample"]))
        names = confirm_indexing(eid, up["filename"])
        log(f"Engagement {name} -> {eid}: uploaded {up['filename']} "
            f"({up['chunks_indexed']} chunks, {up['characters_extracted']} chars), "
            f"files now {names}")

    # ---- run the chat tests ---------------------------------------------
    rows: List[Dict[str, Any]] = []
    detail: Dict[str, Any] = {}
    history_by_eng: Dict[str, List[Dict[str, str]]] = {}

    for t in TESTS:
        tid, sample, mode = t["test_id"], t["sample"], t["role"]
        eid = engagements[t["engagement"]]
        hist = history_by_eng.setdefault(eid, [])
        log(f"{tid} [{sample} / {mode}] prompting ...")
        try:
            reply, elapsed = chat_turn(eid, t["prompt"], mode, hist)
        except ApiError as exc:
            log(f"  ! {tid} FAILED: {exc}")
            rows.append({
                "test_id": tid, "sample_file": sample, "rag_level": rag_level,
                "hardware_mode": hardware_mode, "model_name": model_name,
                "prompt_category": mode, "expected_framework_refs": t["expected"],
                "actual_citations": "", "citation_hit": "error",
                "hallucination_detected": "Yes", "hallucination_notes": f"request failed: {exc}",
                "latency_sec": "", "key_weakness_tag": "request_failed",
            })
            continue

        hist.append({"role": "user", "content": t["prompt"]})
        hist.append({"role": "assistant", "content": reply})

        audit = audit_citations(reply, local, reg, t.get("required_editions"),
                                t.get("policy_checks", False), titles=titles)
        gt = score_gt(reply, t["gt"])
        hit = determine_citation_hit(t["expected"], audit)
        halluc = is_hallucination(audit)
        notes = build_notes(audit)

        raw_path = os.path.join(RAW_DIR, f"{tid}_{os.path.splitext(sample)[0]}.md")
        with open(raw_path, "w", encoding="utf-8") as fh:
            fh.write(f"# {tid} — {sample}\n\n")
            fh.write(f"- engagement_id: `{eid}` ({t['engagement']})\n")
            fh.write(f"- analyst mode (`role`): `{mode}`\n")
            fh.write(f"- rag_level: `{rag_level}`\n")
            fh.write(f"- hardware_mode: `{hardware_mode}`\n")
            fh.write(f"- model: `{model_name}`\n")
            fh.write(f"- latency_sec: **{elapsed:.2f}**\n")
            fh.write(f"- history_turns_sent: {len(hist) - 2}\n")
            fh.write(f"- prompt: {t['prompt']}\n\n")
            fh.write(f"- ground_truth: {gt['score']} "
                     f"(found: {', '.join(gt['found']) or 'none'}; "
                     f"partial: {', '.join(gt.get('partial') or []) or 'none'}; "
                     f"missing: {', '.join(gt['missing']) or 'none'})\n")
            fh.write(f"- citations_verified_local: {len(audit['verified_local'])}\n")
            fh.write(f"- citations_corpus_gap: {len(audit['corpus_gap'])}\n")
            fh.write(f"- citations_invented: {len(audit['invented'])}\n")
            if audit["edition_mismatch"]:
                fh.write(f"- wrong_edition: {'; '.join(audit['edition_mismatch'])}\n")
            if audit["title_notes"]:
                fh.write(f"- mislabelled_category: {'; '.join(audit['title_notes'])}\n")
            if audit.get("false_absence_claims"):
                fh.write(f"- false_absence_claims: {'; '.join(audit['false_absence_claims'])}\n")
            if audit["withdrawn_ids"]:
                fh.write(f"- withdrawn_csf_ids: {'; '.join(audit['withdrawn_ids'])}\n\n")
            fh.write("---\n\n## Verbatim response\n\n")
            fh.write(reply.strip() + "\n")

        row = {
            "test_id": tid,
            "sample_file": sample,
            "rag_level": rag_level,
            "hardware_mode": hardware_mode,
            "model_name": model_name,
            "prompt_category": mode,
            "expected_framework_refs": t["expected"],
            "actual_citations": _compact(audit),
            "citation_hit": hit,
            "hallucination_detected": "Yes" if halluc else "No",
            "hallucination_notes": notes,
            "latency_sec": f"{elapsed:.2f}",
            "key_weakness_tag": provisional_weakness_tag(audit, gt, len(t["gt"])),
        }
        rows.append(row)
        detail[tid] = {
            "row": row, "audit": audit, "ground_truth": gt,
            "raw_output": raw_path, "response_chars": len(reply),
            "reply": reply,
        }
        log(f"  {tid} done in {elapsed:.2f}s | GT {gt['score']} | "
            f"cites verified={len(audit['verified_local'])} gap={len(audit['corpus_gap'])} "
            f"invented={len(audit['invented'])}")

    # ---- T07: report generation -----------------------------------------
    if not args.skip_report:
        eid = engagements.get("Lab3-LargePolicy")
        if eid:
            log("T07 [large-policy-document.txt] /report/generate docx ...")
            try:
                rep = generate_report(eid)
                result = rep["result"]
                latency = rep["latency_sec"]
                path_used = rep["path"]
                raw_path = os.path.join(RAW_DIR, "T07_large-policy-document.md")
                with open(raw_path, "w", encoding="utf-8") as fh:
                    fh.write("# T07 — large-policy-document.txt (Map-Reduce latency test)\n\n")
                    fh.write(f"- engagement_id: `{eid}` (Lab3-LargePolicy)\n")
                    fh.write(f"- endpoint: `POST /report/generate` format=`docx`\n")
                    fh.write(f"- rag_level: `{rag_level}`\n")
                    fh.write(f"- hardware_mode: `{hardware_mode}`\n")
                    fh.write(f"- model: `{model_name}`\n")
                    fh.write(f"- latency_sec: **{latency:.2f}**\n")
                    fh.write(f"- analysis_path_detected: **{path_used}**\n")
                    fh.write(f"- findings_count: {result.get('findings_count')}\n")
                    fh.write(f"- overall_risk_rating: {result.get('overall_risk_rating')}\n")
                    fh.write(f"- download_url: {result.get('download_url')}\n\n")
                    fh.write("## Progress trace (from /api/report/progress)\n\n")
                    for s in rep["progress_snapshots"]:
                        fh.write(f"- stage={s.get('stage')} done={s.get('done')}/"
                                 f"{s.get('total')} detail={s.get('detail')!r}\n")
                    fh.write("\n## Response payload\n\n```json\n")
                    fh.write(json.dumps(result, indent=2))
                    fh.write("\n```\n")

                expected = "NIST_CSF;CIS_CONTROLS;ISO_27001"
                rows.append({
                    "test_id": "T07",
                    "sample_file": "large-policy-document.txt",
                    "rag_level": rag_level,
                    "hardware_mode": hardware_mode,
                    "model_name": model_name,
                    "prompt_category": "report",
                    "expected_framework_refs": expected,
                    "actual_citations": (f"report_docx;findings={result.get('findings_count')};"
                                         f"path={path_used}"),
                    "citation_hit": "n/a",
                    "hallucination_detected": "No",
                    "hallucination_notes": (
                        f"report rendered; framework mapping inside the .docx, not "
                        f"audited here. analysis_path={path_used}, "
                        f"findings={result.get('findings_count')}"
                    ),
                    "latency_sec": f"{latency:.2f}",
                    "key_weakness_tag": (
                        "flat_path_not_mapreduce" if path_used == "flat"
                        else "none_observed"
                    ),
                })
                detail["T07"] = {
                    "row": rows[-1], "raw_output": raw_path,
                    "analysis_path": path_used,
                    "progress_snapshots": rep["progress_snapshots"],
                    "result": result,
                }
                log(f"  T07 done in {latency:.2f}s | path={path_used} | "
                    f"findings={result.get('findings_count')}")
            except ApiError as exc:
                log(f"  ! T07 FAILED: {exc}")
                rows.append({
                    "test_id": "T07", "sample_file": "large-policy-document.txt",
                    "rag_level": rag_level, "hardware_mode": hardware_mode,
                    "model_name": model_name, "prompt_category": "report",
                    "expected_framework_refs": "NIST_CSF;CIS_CONTROLS;ISO_27001",
                    "actual_citations": "", "citation_hit": "error",
                    "hallucination_detected": "Yes",
                    "hallucination_notes": f"report generation failed: {exc}",
                    "latency_sec": "", "key_weakness_tag": "request_failed",
                })

    rows.sort(key=lambda r: r["test_id"])
    write_rows(rows)

    with open(DETAIL_PATH, "w", encoding="utf-8") as fh:
        json.dump({
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "preflight": info,
            "engagements": engagements,
            "tests": {k: {kk: vv for kk, vv in v.items() if kk != "reply"}
                      for k, v in detail.items()},
        }, fh, indent=2)
    log(f"  audit detail: {DETAIL_PATH}")

    print_summary(rows, detail)
    return 0


def _compact(audit: Dict[str, Any], limit: int = 700) -> str:
    parts = []
    for fam, ids in audit["by_family"].items():
        parts.append(f"{fam}[{','.join(ids)}]")
    s = ";".join(parts) if parts else "none"
    flags = []
    if audit.get("invented"):
        flags.append(f"invented={len(audit['invented'])}")
    if audit.get("title_notes"):
        flags.append(f"mislabelled={len(audit['title_notes'])}")
    if audit.get("edition_mismatch"):
        flags.append(f"wrong_edition={len(audit['edition_mismatch'])}")
    if audit.get("withdrawn_ids"):
        flags.append(f"withdrawn={len(audit['withdrawn_ids'])}")
    if audit.get("corpus_gap"):
        flags.append(f"corpus_gap={len(audit['corpus_gap'])}")
    if flags:
        s += " || " + ",".join(flags)
    if len(s) > limit:
        s = s[:limit] + f"...(+{len(s) - limit} chars)"
    return s


def print_summary(rows: List[Dict[str, Any]], detail: Dict[str, Any]) -> None:
    print()
    print("=" * 108)
    print("STEP 2.1 BASELINE — SUMMARY")
    print("=" * 108)
    hdr = f"{'test':<5} {'sample':<26} {'latency_s':>10} {'GT found':>9} " \
          f"{'cite_hit':>9} {'halluc':>7}  weakness"
    print(hdr)
    print("-" * 108)
    for r in rows:
        tid = r["test_id"]
        d = detail.get(tid, {})
        gt = d.get("ground_truth", {})
        gt_s = gt.get("score", f"n/a ({r.get('key_weakness_tag')})" if tid == "T07" else "n/a")
        print(f"{tid:<5} {r['sample_file']:<26} {r['latency_sec']:>10} "
              f"{gt_s:>9} {r['citation_hit']:>9} {r['hallucination_detected']:>7}  "
              f"{r['key_weakness_tag']}")
    print("-" * 108)
    lat = [float(r["latency_sec"]) for r in rows if r.get("latency_sec")]
    if lat:
        print(f"total wall clock across tests: {sum(lat):.1f}s   "
              f"mean: {sum(lat)/len(lat):.1f}s   max: {max(lat):.1f}s")
    halluc = [r["test_id"] for r in rows if r["hallucination_detected"] == "Yes"]
    print(f"hallucination flagged: {', '.join(halluc) if halluc else 'none'}")
    print("=" * 108)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\nInterrupted.", file=sys.stderr)
        sys.exit(130)
    except ApiError as exc:
        print(f"\nAPI ERROR: {exc}", file=sys.stderr)
        sys.exit(1)
