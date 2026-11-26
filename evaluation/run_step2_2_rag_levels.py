"""
Stage 2 - Step 2.2: controlled RAG_LEVEL benchmark (basic vs standard vs full).

Runs the same four citation-focused prompts under each retrieval tier so the
results are comparable apples-to-apples, then writes them into the shared
baseline CSV alongside the existing T01-T09 standard rows.

Design notes
------------
* RAG_LEVEL is passed through the CHILD process environment only. .env is never
  edited, so it stays at standard.
* Only one Fortis/llama.cpp process may exist at a time on a 6 GB GPU. The
  script kills whatever listens on the port, starts a headless server, then
  tears it down before the next level.
* The effective rag_level is verified by reading the *child process* environment
  via psutil -- there is no HTTP endpoint that reports it. This proves what the
  server actually loaded rather than trusting the setting we asked for.
* A single warm-up turn (throwaway engagement) absorbs model load and the first
  cross-encoder load, so cold start never inflates a measured latency.
* Audit logic is imported from run_step2_1_baseline so both steps grade
  citations identically.

Usage:
    ./.venv/Scripts/python.exe evaluation/run_step2_2_rag_levels.py
    ./.venv/Scripts/python.exe evaluation/run_step2_2_rag_levels.py --levels basic
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import signal
import socket
import subprocess
import sys
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

import requests

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import run_step2_1_baseline as R  # noqa: E402

ROOT = R.ROOT
EVAL_DIR = R.EVAL_DIR
RAW_DIR = R.RAW_DIR
CSV_PATH = R.CSV_PATH
SAMPLES = R.SAMPLES

PORT = int(os.getenv("FORTIS_PORT", "8757"))
BASE_URL = f"http://127.0.0.1:{PORT}"

DETAIL_PATH = os.path.join(EVAL_DIR, "step2_2_audit_detail.json")
COMPARISON_PATH = os.path.join(EVAL_DIR, "step2_2_rag_comparison.md")
SERVER_LOG = os.path.join(EVAL_DIR, "step2_2_server.log")

CHAT_TIMEOUT = 1200
UPLOAD_TIMEOUT = 300


def log(msg: str) -> None:
    print(f"[{R.ts()}] {msg}", flush=True)


# --------------------------------------------------------------------------- #
# Server lifecycle
# --------------------------------------------------------------------------- #


def port_is_open(port: int = PORT) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.5)
        return s.connect_ex(("127.0.0.1", port)) == 0


def stop_listener(port: int = PORT) -> bool:
    """Stop whatever holds the port. Returns True if something was stopped.

    Graceful first (closes the SQLite handle cleanly), then forceful. Nothing
    else on this machine may use the GPU while a benchmark level is running.
    """
    import psutil

    stopped = False
    for proc in psutil.process_iter(["pid", "name"]):
        if proc.pid == os.getpid():
            continue
        try:
            for conn in proc.net_connections(kind="inet"):
                if conn.status == psutil.CONN_LISTEN and conn.laddr.port == port:
                    log(f"  stopping pid {proc.pid} ({proc.name()}) holding :{port}")
                    try:
                        cmd = " ".join(proc.cmdline()[:3])
                    except Exception:
                        cmd = "?"
                    log(f"    cmd: {cmd}")
                    proc.terminate()
                    try:
                        proc.wait(timeout=15)
                    except psutil.TimeoutExpired:
                        log("    did not exit, killing")
                        proc.kill()
                        proc.wait(timeout=10)
                    stopped = True
                    break
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue

    for _ in range(40):
        if not port_is_open(port):
            return stopped
        time.sleep(0.25)
    raise RuntimeError(f"port {port} still open after stop attempt")


def read_child_rag_level(pid: int) -> Optional[str]:
    """Read RAG_LEVEL from the server's own environment. Proves what the running
    process actually loaded, not what we intended to set."""
    import psutil

    try:
        return psutil.Process(pid).environ().get("RAG_LEVEL")
    except Exception as exc:
        log(f"  ! could not read child env: {type(exc).__name__}: {exc}")
        return None


def launch_server(level: str) -> subprocess.Popen:
    env = dict(os.environ)
    env["RAG_LEVEL"] = level
    env["PYTHONUNBUFFERED"] = "1"
    env.pop("FORTIS_LLM_BACKEND", None)

    cmd = [os.path.join(ROOT, ".venv", "Scripts", "python.exe"),
           "-m", "server.app", "--port", str(PORT)]
    logf = open(SERVER_LOG, "a", encoding="utf-8")
    logf.write(f"\n\n===== RAG_LEVEL={level} start {datetime.now().isoformat()} =====\n")
    logf.flush()
    log(f"  launching: {' '.join(cmd)}  (RAG_LEVEL={level})")
    proc = subprocess.Popen(
        cmd, cwd=ROOT, env=env, stdout=logf, stderr=subprocess.STDOUT,
        creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0),
    )
    proc._fortis_log = logf  # type: ignore[attr-defined]
    return proc


def wait_for_health(proc: subprocess.Popen, timeout: float = 180) -> Dict[str, Any]:
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        if proc.poll() is not None:
            raise RuntimeError(
                f"server exited early (code {proc.returncode}); see {SERVER_LOG}")
        try:
            r = requests.get(BASE_URL + "/health", timeout=5)
            if r.status_code == 200:
                data = r.json()
                if data.get("status") == "ok":
                    return data
                last = data
        except requests.exceptions.RequestException as exc:
            last = str(exc)
        time.sleep(0.5)
    raise RuntimeError(f"health never became ok (last={last!r})")


def stop_server(proc: subprocess.Popen) -> None:
    if proc.poll() is not None:
        return
    log(f"  terminating server pid {proc.pid}")
    try:
        proc.terminate()
        proc.wait(timeout=20)
    except subprocess.TimeoutExpired:
        proc.kill()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            pass
    logf = getattr(proc, "_fortis_log", None)
    if logf:
        try:
            logf.close()
        except Exception:
            pass
    # ensure the port is actually released before the next level
    for _ in range(40):
        if not port_is_open(PORT):
            return
        time.sleep(0.25)
    stop_listener(PORT)


# --------------------------------------------------------------------------- #
# Server interaction
# --------------------------------------------------------------------------- #


def api(method: str, path: str, timeout: float = 60, **kw) -> Any:
    return R._req(method, path, timeout, **kw).json()


def create_engagement(client: str, name: str, reset: bool) -> str:
    eng = api("POST", "/engagements", timeout=60,
              json={"client_name": client, "engagement_name": name,
                    "notes": f"step2.2 {client}"})
    eid = eng["id"]
    if reset:
        for path in (f"/upload/engagement/{eid}", f"/chat/history/{eid}?confirm=true"):
            try:
                R._req("DELETE", path, 60)
            except Exception:
                pass
    return eid


def upload(engagement_id: str, path: str) -> Dict[str, Any]:
    with open(path, "rb") as fh:
        return R._req("POST", "/upload", UPLOAD_TIMEOUT,
                      files={"file": (os.path.basename(path), fh, "application/octet-stream")},
                      data={"engagement_id": engagement_id}).json()


def chat(engagement_id: str, message: str, role: str) -> Tuple[str, float]:
    payload: Dict[str, Any] = {"engagement_id": engagement_id, "message": message}
    if role:
        payload["role"] = role
    t0 = time.perf_counter()
    data = api("POST", "/chat", CHAT_TIMEOUT, json=payload)
    return data.get("reply", ""), time.perf_counter() - t0


# --------------------------------------------------------------------------- #
# Test specification
# --------------------------------------------------------------------------- #

PROMPTS = [
    {
        "slot": 1, "sample": "firewall-config.txt", "role": "general",
        "prompt": ("Which finding is highest risk and why? What remediation order "
                   "would you recommend for the top three findings, and which NIST "
                   "CSF functions do they map to?"),
        "expected": "NIST_CSF(categories)", "gt": "GT_FIREWALL",
        "policy_checks": False, "required_editions": {},
        "maps_to_standard": "T02",
    },
    {
        "slot": 2, "sample": "iam-policy.json", "role": "general",
        "prompt": ("Which finding is highest risk and why? For each finding, cite "
                   "the exact section and map it to NIST CSF, CIS Controls, or ISO "
                   "27001 control IDs."),
        "expected": "NIST_CSF;CIS_CONTROLS;ISO_27001", "gt": "GT_IAM",
        "policy_checks": False, "required_editions": {},
        "maps_to_standard": "T04",
    },
    {
        "slot": 3, "sample": "large-policy-document.txt", "role": "grc",
        "prompt": "What are the biggest security gaps in this policy?",
        "expected": "NIST_CSF;CIS_CONTROLS;ISO_27001", "gt": "GT_LARGE_POLICY",
        "policy_checks": True, "required_editions": {},
        "maps_to_standard": "T06",
    },
    {
        "slot": 4, "sample": "vulnerable-app.py", "role": "code",
        "prompt": ("Map each vulnerability to its exact CWE ID and OWASP Top 10:2025 "
                   "category, citing the exact function name and line."),
        "expected": "CWE;OWASP_TOP10:2025", "gt": "GT_VULN",
        "policy_checks": False, "required_editions": {"OWASP_TOP10": "2025"},
        "maps_to_standard": "T09",
    },
]

LEVEL_PREFIX = {"basic": "B", "full": "F"}


# --------------------------------------------------------------------------- #
# One level
# --------------------------------------------------------------------------- #


def run_level(level: str, local: Dict[str, set], reg: Dict[str, set],
              titles: Dict[str, Dict[str, str]]) -> List[Dict[str, Any]]:
    prefix = LEVEL_PREFIX[level]
    client = f"Baseline-RAG-{level}"
    rows: List[Dict[str, Any]] = []
    detail: Dict[str, Any] = {}

    log(f"===== RAG_LEVEL={level} =====")
    stop_listener(PORT)
    proc = launch_server(level)
    try:
        health = wait_for_health(proc)
        log(f"  health ok; frameworks={len(health.get('frameworks_loaded', []))}")

        child_level = read_child_rag_level(proc.pid)
        log(f"  effective RAG_LEVEL in server process: {child_level!r}")
        if child_level != level:
            raise RuntimeError(
                f"server env RAG_LEVEL={child_level!r} but requested {level!r}")

        # ---- warm-up (throwaway, never measured) -------------------------
        warm_id = create_engagement(client, "Warmup", reset=True)
        upload(warm_id, os.path.join(SAMPLES, "firewall-config.txt"))
        try:
            reply, secs = chat(warm_id, "Reply with the single word READY.", "general")
            log(f"  warm-up ({secs:.1f}s): {reply[:50]!r}")
        finally:
            try:
                R._req("DELETE", f"/engagements/{warm_id}", 60)
            except Exception as exc:
                log(f"  ! warm-up cleanup failed: {exc}")

        ms = api("GET", "/api/model/status", 90)
        hw = ms.get("hardware", {})
        offload, wheel = hw.get("offload"), hw.get("backend_wheel")
        if offload in ("full", "partial"):
            hardware_mode = f"gpu_{offload}({wheel})"
        elif offload == "cpu":
            hardware_mode = "cpu"
        else:
            hardware_mode = f"unknown({wheel})"
        model_name = (ms.get("active_model") or "").replace("\\", "/").split("/")[-1]
        log(f"  loaded={ms.get('loaded')} offload={offload} -> {hardware_mode}")
        log(f"  model={model_name}")

        # reranker availability is a property of the 'full'/'standard' path
        rerank_note = ""
        if level in ("standard", "full"):
            rr = subprocess.run(
                [os.path.join(ROOT, ".venv", "Scripts", "python.exe"), "-c",
                 "import sys;sys.path.insert(0,'.');from core import rerank;"
                 "print(rerank.is_available())"],
                cwd=ROOT, capture_output=True, text=True, timeout=120)
            rerank_note = (rr.stdout or rr.stderr).strip().splitlines()[-1] if rr.stdout or rr.stderr else "?"
            log(f"  rerank.is_available() -> {rerank_note}")

        # ---- the four prompts -------------------------------------------
        for spec in PROMPTS:
            tid = f"{prefix}{spec['slot']:02d}"
            sample = spec["sample"]
            eid = create_engagement(client, f"RAG-{level}-S{spec['slot']}", reset=True)
            up = upload(eid, os.path.join(SAMPLES, sample))
            names = api("GET", f"/upload/engagement/{eid}/files", 60).get("files", [])
            if up["filename"] not in names:
                raise RuntimeError(f"{tid}: indexing not confirmed ({names})")
            log(f"  {tid} [{sample} / {spec['role']}] uploaded "
                f"{up['chunks_indexed']} chunks; prompting ...")

            try:
                reply, secs = chat(eid, spec["prompt"], spec["role"])
            except Exception as exc:
                log(f"  ! {tid} FAILED: {type(exc).__name__}: {exc}")
                rows.append({
                    "test_id": tid, "sample_file": sample, "rag_level": level,
                    "hardware_mode": hardware_mode, "model_name": model_name,
                    "prompt_category": spec["role"],
                    "expected_framework_refs": spec["expected"],
                    "actual_citations": "", "citation_hit": "error",
                    "hallucination_detected": "Yes",
                    "hallucination_notes": f"request failed: {exc}",
                    "latency_sec": "", "key_weakness_tag": "request_failed",
                })
                continue

            audit = R.audit_citations(reply, local, reg, spec["required_editions"],
                                      spec["policy_checks"], titles=titles)
            gt = R.score_gt(reply, getattr(R, spec["gt"]))
            hit = R.determine_citation_hit(spec["expected"], audit)
            halluc = R.is_hallucination(audit)
            notes = R.build_notes(audit)
            tag = R.provisional_weakness_tag(audit, gt, len(getattr(R, spec["gt"])))

            stem = os.path.splitext(sample)[0]
            raw_path = os.path.join(RAW_DIR, f"{tid}_{level}_{stem}.md")
            with open(raw_path, "w", encoding="utf-8") as fh:
                fh.write(f"# {tid} — {sample} @ RAG_LEVEL={level}\n\n")
                fh.write(f"- engagement_id: `{eid}` ({client} / RAG-{level}-S{spec['slot']})\n")
                fh.write(f"- analyst mode (`role`): `{spec['role']}`\n")
                fh.write(f"- rag_level (verified in server env): `{child_level}`\n")
                fh.write(f"- hardware_mode: `{hardware_mode}`\n")
                fh.write(f"- model: `{model_name}`\n")
                if rerank_note:
                    fh.write(f"- rerank.is_available(): `{rerank_note}`\n")
                fh.write(f"- latency_sec: **{secs:.2f}**\n")
                fh.write(f"- prompt: {spec['prompt']}\n\n")
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
                if audit.get("iam_logic_notes"):
                    fh.write(f"- domain_logic: {'; '.join(audit['iam_logic_notes'])}\n")
                if audit.get("false_absence_claims"):
                    fh.write(f"- false_absence_claims: {'; '.join(audit['false_absence_claims'])}\n")
                if audit["withdrawn_ids"]:
                    fh.write(f"- withdrawn_csf_ids: {'; '.join(audit['withdrawn_ids'])}\n")
                fh.write("\n---\n\n## Verbatim response\n\n")
                fh.write(reply.strip() + "\n")

            row = {
                "test_id": tid, "sample_file": sample, "rag_level": level,
                "hardware_mode": hardware_mode, "model_name": model_name,
                "prompt_category": spec["role"],
                "expected_framework_refs": spec["expected"],
                "actual_citations": R._compact(audit),
                "citation_hit": hit,
                "hallucination_detected": "Yes" if halluc else "No",
                "hallucination_notes": notes,
                "latency_sec": f"{secs:.2f}",
                "key_weakness_tag": tag,
            }
            rows.append(row)
            detail[tid] = {
                "row": row, "audit": audit, "ground_truth": gt,
                "raw_output": raw_path, "latency": round(secs, 2),
                "maps_to_standard": spec["maps_to_standard"],
                "rag_level_verified": child_level,
                "rerank_available": rerank_note,
                "response_chars": len(reply),
            }
            log(f"    {tid} {secs:.2f}s | GT {gt['score']} | hit={hit} | "
                f"halluc={'Yes' if halluc else 'No'} | verified={len(audit['verified_local'])} "
                f"gap={len(audit['corpus_gap'])} invented={len(audit['invented'])}")
    finally:
        stop_server(proc)

    return rows, detail


# --------------------------------------------------------------------------- #
# Comparison
# --------------------------------------------------------------------------- #


def find_standard_counterparts() -> Dict[str, Dict[str, Any]]:
    """Pull the matching T-rows out of the audit detail so the comparison uses
    the same measured values as Step 2.1."""
    out: Dict[str, Dict[str, Any]] = {}
    if not os.path.isfile(R.DETAIL_PATH):
        return out
    try:
        with open(R.DETAIL_PATH, "r", encoding="utf-8") as fh:
            d = json.load(fh)
    except (OSError, json.JSONDecodeError):
        return out
    tests = d.get("tests", {})
    for slot in (1, 2, 3, 4):
        std_tid = f"T{ {1: '02', 2: '04', 3: '06', 4: '09'}[slot] }"
        t = tests.get(std_tid)
        if t:
            out[f"S{slot}"] = {
                "test_id": std_tid,
                "sample_file": t.get("row", {}).get("sample_file", ""),
                "latency": float(t.get("row", {}).get("latency_sec") or 0),
                "ground_truth": t.get("ground_truth", {}),
                "audit": t.get("audit", {}),
                "citation_hit": t.get("row", {}).get("citation_hit", ""),
                "hallucination": t.get("row", {}).get("hallucination_detected", ""),
                "tag": t.get("row", {}).get("key_weakness_tag", ""),
            }
    return out


def summarise(rows: List[Dict[str, Any]], detail: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
    lats = [float(r["latency_sec"]) for r in rows if r.get("latency_sec")]
    errs: Dict[str, int] = {}
    for r in rows:
        d = detail.get(r["test_id"], {})
        for reason in R.hallucination_reasons(d.get("audit", {})):
            errs[reason] = errs.get(reason, 0) + 1
    gt_num = sum(detail.get(r["test_id"], {}).get("ground_truth", {}).get("numeric", 0)
                 for r in rows)
    gt_tot = sum(detail.get(r["test_id"], {}).get("ground_truth", {}).get("total", 0)
                 for r in rows)
    hits = [r["citation_hit"] for r in rows]
    return {
        "n": len(rows),
        "latencies": lats,
        "mean_latency": round(sum(lats) / len(lats), 2) if lats else 0.0,
        "gt_found": gt_num,
        "gt_total": gt_tot,
        "gt_rate": round(gt_num / gt_tot, 3) if gt_tot else 0.0,
        "citation_yes": hits.count("yes"),
        "citation_partial": hits.count("partial"),
        "citation_no": hits.count("no"),
        "citation_na": hits.count("n/a"),
        "halluc_tests": sum(1 for r in rows if r["hallucination_detected"] == "Yes"),
        "error_breakdown": dict(sorted(errs.items(), key=lambda kv: -kv[1])),
    }


def write_comparison(basic: Dict[str, Any], standard: Dict[str, Any],
                     full: Dict[str, Any], std_rows: Dict[str, Dict[str, Any]],
                     detail: Dict[str, Dict[str, Any]]) -> str:
    lines: List[str] = []
    lines.append("# Step 2.2 — RAG_LEVEL comparison (basic / standard / full)\n")
    lines.append(f"Generated {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} · "
                 f"model `{next(iter(detail.values()))['row']['model_name']}` · "
                 f"hardware `{next(iter(detail.values()))['row']['hardware_mode']}`\n")

    lines.append("\n## Aggregate\n")
    lines.append("| metric | basic | standard | full |")
    lines.append("|---|---|---|---|")
    lines.append(f"| prompts measured | {basic['n']} | {standard['n']} | {full['n']} |")
    lines.append(f"| mean latency (s) | {basic['mean_latency']} | "
                 f"{standard['mean_latency']} | {full['mean_latency']} |")
    lines.append(f"| ground-truth coverage | {basic['gt_found']:.1f}/{basic['gt_total']} "
                 f"({basic['gt_rate']*100:.0f}%) | {standard['gt_found']:.1f}/"
                 f"{standard['gt_total']} ({standard['gt_rate']*100:.0f}%) | "
                 f"{full['gt_found']:.1f}/{full['gt_total']} ({full['gt_rate']*100:.0f}%) |")
    lines.append(f"| citation hit yes/partial/no | {basic['citation_yes']}/"
                 f"{basic['citation_partial']}/{basic['citation_no']} | "
                 f"{standard['citation_yes']}/{standard['citation_partial']}/"
                 f"{standard['citation_no']} | {full['citation_yes']}/"
                 f"{full['citation_partial']}/{full['citation_no']} |")
    lines.append(f"| tests with citation error | {basic['halluc_tests']} | "
                 f"{standard['halluc_tests']} | {full['halluc_tests']} |")

    lines.append("\n## Per-prompt latency (s)\n")
    lines.append("| slot | prompt | basic | standard | full |")
    lines.append("|---|---|---|---|---|")
    slots = [
        (1, "T02 / B01 / F01 — firewall, general"),
        (2, "T04 / B02 / F02 — IAM, general"),
        (3, "T06 / B03 / F03 — large policy, grc"),
        (4, "T09 / B04 / F04 — vuln app, code"),
    ]
    for slot, label in slots:
        std = std_rows.get(f"S{slot}", {})
        lines.append(
            f"| {slot} | {label} | "
            f"{detail.get(f'B{slot:02d}', {}).get('latency', '—')} | "
            f"{std.get('latency', '—')} | "
            f"{detail.get(f'F{slot:02d}', {}).get('latency', '—')} |")

    lines.append("\n## Per-prompt detail\n")
    for slot, _ in slots:
        b = detail.get(f"B{slot:02d}", {})
        f_ = detail.get(f"F{slot:02d}", {})
        s = std_rows.get(f"S{slot}", {})
        lines.append(f"### Slot {slot}\n")
        lines.append("| field | basic | standard | full |")
        lines.append("|---|---|---|---|")
        lines.append(f"| test id | B{slot:02d} | {s.get('test_id','—')} | F{slot:02d} |")
        lines.append(f"| latency (s) | {b.get('latency','—')} | {s.get('latency','—')} | "
                     f"{f_.get('latency','—')} |")
        lines.append(f"| ground truth | {b.get('ground_truth',{}).get('score','—')} | "
                     f"{s.get('ground_truth',{}).get('score','—')} | "
                     f"{f_.get('ground_truth',{}).get('score','—')} |")
        lines.append(f"| citation hit | {b.get('row',{}).get('citation_hit','—')} | "
                     f"{s.get('citation_hit','—')} | "
                     f"{f_.get('row',{}).get('citation_hit','—')} |")
        lines.append(f"| citation error | {b.get('row',{}).get('hallucination_detected','—')} | "
                     f"{s.get('hallucination','—')} | "
                     f"{f_.get('row',{}).get('hallucination_detected','—')} |")
        lines.append(f"| weakness tag | {b.get('row',{}).get('key_weakness_tag','—')} | "
                     f"{s.get('tag','—')} | "
                     f"{f_.get('row',{}).get('key_weakness_tag','—')} |")
        lines.append("")

    lines.append("\n## Citation-error breakdown\n")
    lines.append("A test can contribute more than one error type.\n")
    lines.append("| error type | basic | standard | full |")
    lines.append("|---|---|---|---|")
    keys = sorted(set(basic["error_breakdown"]) | set(standard["error_breakdown"])
                  | set(full["error_breakdown"]))
    for k in keys:
        lines.append(f"| {k} | {basic['error_breakdown'].get(k,0)} | "
                     f"{standard['error_breakdown'].get(k,0)} | "
                     f"{full['error_breakdown'].get(k,0)} |")
    if not keys:
        lines.append("| (none) | 0 | 0 | 0 |")

    lines.append("\n## What each tier actually changes\n")
    lines.append("- `basic`: dense vector retrieval only.")
    lines.append("- `standard`: adds hybrid BM25/RRF fusion plus cross-encoder "
                 "reranking; heuristic query condensation (no extra LLM call).")
    lines.append("- `full`: adds LLM query condensation on top. On a single-turn "
                 "prompt there is no history to condense, so `full` and `standard` "
                 "share the same retrieval path and the same reranker.")
    lines.append("\nReranker note: this venv's torch build has no CUDA support, so "
                 "the cross-encoder demotes to CPU. Reranking therefore costs CPU "
                 "time on every retrieval and competes with the GPU-resident LLM.\n")

    text = "\n".join(lines)
    with open(COMPARISON_PATH, "w", encoding="utf-8") as fh:
        fh.write(text)
    return text


# --------------------------------------------------------------------------- #
# Main
# --------------------------------------------------------------------------- #


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--levels", nargs="+", default=["basic", "full"],
                    choices=["basic", "standard", "full"])
    ap.add_argument("--skip-stop", action="store_true",
                    help="do not stop an existing listener first (unsafe on 6GB VRAM)")
    args = ap.parse_args()

    os.makedirs(RAW_DIR, exist_ok=True)

    local = R.load_local_corpus()
    reg = R.load_registry()
    titles = R.load_title_index()
    log(f"reference sets: corpus {sum(len(v) for v in local.values())} ids, "
        f"registry {sum(len(v) for v in reg.values())} ids")

    if not args.skip_stop:
        if port_is_open(PORT):
            log(f"port {PORT} is occupied — freeing it before benchmarking")
            stop_listener(PORT)
        else:
            log(f"port {PORT} is free")

    all_rows: List[Dict[str, Any]] = []
    all_detail: Dict[str, Any] = {}
    per_level: Dict[str, Dict[str, Any]] = {}

    for level in args.levels:
        rows, detail = run_level(level, local, reg, titles)
        all_rows.extend(rows)
        all_detail.update({k: {kk: vv for kk, vv in v.items()} for k, v in detail.items()})
        per_level[level] = summarise(rows, detail)

    # ---- merge into the shared CSV, preserving every existing row --------
    log("writing CSV")
    R.write_rows(all_rows)

    std_rows = find_standard_counterparts()
    std_summary = {
        "n": len(std_rows),
        "mean_latency": round(sum(v["latency"] for v in std_rows.values())
                              / len(std_rows), 2) if std_rows else 0.0,
        "gt_found": sum(v.get("ground_truth", {}).get("numeric", 0) for v in std_rows.values()),
        "gt_total": sum(v.get("ground_truth", {}).get("total", 0) for v in std_rows.values()),
        "citation_yes": sum(1 for v in std_rows.values() if v.get("citation_hit") == "yes"),
        "citation_partial": sum(1 for v in std_rows.values() if v.get("citation_hit") == "partial"),
        "citation_no": sum(1 for v in std_rows.values() if v.get("citation_hit") == "no"),
        "citation_na": sum(1 for v in std_rows.values() if v.get("citation_hit") == "n/a"),
        "halluc_tests": sum(1 for v in std_rows.values() if v.get("hallucination") == "Yes"),
        "error_breakdown": {},
    }
    errs: Dict[str, int] = {}
    for v in std_rows.values():
        for reason in R.hallucination_reasons(v.get("audit", {})):
            errs[reason] = errs.get(reason, 0) + 1
    std_summary["error_breakdown"] = dict(sorted(errs.items(), key=lambda kv: -kv[1]))
    std_summary["gt_rate"] = (round(std_summary["gt_found"] / std_summary["gt_total"], 3)
                              if std_summary["gt_total"] else 0.0)

    basic = per_level.get("basic") or {"n": 0, "mean_latency": 0, "gt_found": 0,
                                       "gt_total": 0, "gt_rate": 0, "citation_yes": 0,
                                       "citation_partial": 0, "citation_no": 0,
                                       "citation_na": 0, "halluc_tests": 0,
                                       "error_breakdown": {}}
    full = per_level.get("full") or basic

    text = write_comparison(basic, std_summary, full, std_rows, all_detail)

    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "levels_measured": args.levels,
        "standard_source": "evaluation/baseline_results.csv T02/T04/T06/T09 (Step 2.1)",
        "summaries": {"basic": basic, "standard": std_summary, "full": full},
        "tests": all_detail,
    }
    with open(DETAIL_PATH, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2)
    log(f"detail: {DETAIL_PATH}")
    log(f"comparison: {COMPARISON_PATH}")

    print()
    print(text)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\ninterrupted", file=sys.stderr)
        sys.exit(130)
    except Exception as exc:
        print(f"\nERROR: {type(exc).__name__}: {exc}", file=sys.stderr)
        sys.exit(1)
