"""
Re-run the citation / ground-truth audit over the responses already saved in
evaluation/raw_outputs/, and rewrite baseline_results.csv from them.

Why this exists: each audit pass costs ~8 minutes of LLM inference. When only
the *analysis* logic changes (regex, checklist, tag rules), re-reading the saved
verbatim responses gives identical latency numbers without paying for inference
again. Latency is copied from the existing rows so the measured values stay the
ones produced by the original run.

Usage:
    ./.venv/Scripts/python.exe evaluation/reaudit_baseline.py
"""

from __future__ import annotations

import csv
import json
import os
import sys
from typing import Dict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import run_step2_1_baseline as R  # noqa: E402


def load_existing_rows() -> dict:
    if not os.path.isfile(R.CSV_PATH):
        return {}
    with open(R.CSV_PATH, "r", encoding="utf-8", newline="") as fh:
        return {r["test_id"]: r for r in csv.DictReader(fh)}


def main() -> int:
    specs = {t["test_id"]: t for t in R.TESTS}
    existing = load_existing_rows()
    local = R.load_local_corpus()
    reg = R.load_registry()
    titles = R.load_title_index()

    # preserve the parts of the detail file that this re-audit does not rebuild
    prev_detail = {}
    if os.path.isfile(R.DETAIL_PATH):
        try:
            with open(R.DETAIL_PATH, "r", encoding="utf-8") as fh:
                prev_detail = json.load(fh)
        except (OSError, json.JSONDecodeError):
            prev_detail = {}

    rows = []
    detail = {}

    for tid, spec in specs.items():
        sample = spec["sample"]
        stem = os.path.splitext(sample)[0]
        path = os.path.join(R.RAW_DIR, f"{tid}_{stem}.md")
        if not os.path.isfile(path):
            print(f"{tid}: MISSING {path} -- skipped")
            continue
        text = open(path, encoding="utf-8").read()

        # latency + run metadata come from the original measured run
        prev = existing.get(tid, {})
        label = text.split("---", 1)[0]
        latency = prev.get("latency_sec", "")
        for line in label.splitlines():
            if line.startswith("- latency_sec:"):
                try:
                    latency = f"{float(line.split('**')[1]):.2f}"
                except (IndexError, ValueError):
                    pass

        body = text.split("## Verbatim response", 1)[-1]
        audit = R.audit_citations(body, local, reg, spec.get("required_editions"),
                                  spec.get("policy_checks", False), titles=titles)
        gt = R.score_gt(body, spec["gt"])
        hit = R.determine_citation_hit(spec["expected"], audit)

        notes = R.build_notes(audit)

        row = {
            "test_id": tid,
            "sample_file": sample,
            "rag_level": prev.get("rag_level", "standard"),
            "hardware_mode": prev.get("hardware_mode", ""),
            "model_name": prev.get("model_name", ""),
            "prompt_category": spec["role"],
            "expected_framework_refs": spec["expected"],
            "actual_citations": R._compact(audit),
            "citation_hit": hit,
            "hallucination_detected": "Yes" if R.is_hallucination(audit) else "No",
            "hallucination_notes": " | ".join(notes) if isinstance(notes, list) else notes,
            "latency_sec": latency,
            "key_weakness_tag": R.provisional_weakness_tag(audit, gt, len(spec["gt"])),
        }
        rows.append(row)
        detail[tid] = {"row": row, "audit": audit, "ground_truth": gt,
                       "raw_output": path}

    # keep T07 exactly as measured (it is not a chat response)
    if "T07" in existing:
        rows.append(existing["T07"])
    if "T07" in prev_detail.get("tests", {}):
        detail["T07"] = prev_detail["tests"]["T07"]

    rows.sort(key=lambda r: r["test_id"])
    R.write_rows(rows)

    out = {k: v for k, v in prev_detail.items() if k != "tests"}
    out["reaudited"] = True
    out["tests"] = detail
    with open(R.DETAIL_PATH, "w", encoding="utf-8") as fh:
        json.dump(out, fh, indent=2)

    print()
    print(f"{'test':<5} {'sample':<26} {'latency_s':>10} {'GT':>16} {'cite':>9} {'halluc':>7}  weakness")
    print("-" * 104)
    for r in rows:
        d = detail.get(r["test_id"], {})
        gt = d.get("ground_truth", {})
        print(f"{r['test_id']:<5} {r['sample_file']:<26} {r['latency_sec']:>10} "
              f"{gt.get('score', 'n/a'):>16} {r['citation_hit']:>9} "
              f"{r['hallucination_detected']:>7}  {r['key_weakness_tag']}")

    # hallucination rate over the 8 chat tests (T07 is report generation, not a chat turn)
    chat = [r for r in rows if r["test_id"] != "T07"]
    flagged = [r for r in chat if r["hallucination_detected"] == "Yes"]
    print()
    print("=" * 104)
    print(f"BASELINE HALLUCINATION RATE: {len(flagged)} / {len(chat)} chat tests "
          f"({100.0 * len(flagged) / max(1, len(chat)):.1f}%)")
    print("=" * 104)

    type_counts: Dict[str, int] = {}
    for r in flagged:
        d = detail.get(r["test_id"], {})
        for reason in R.hallucination_reasons(d.get("audit", {})):
            type_counts[reason] = type_counts.get(reason, 0) + 1
    if type_counts:
        print("breakdown by error type (a test may have more than one):")
        for reason, n in sorted(type_counts.items(), key=lambda kv: -kv[1]):
            tests = [r["test_id"] for r in flagged
                     if reason in R.hallucination_reasons(
                         detail.get(r["test_id"], {}).get("audit", {}))]
            print(f"  {reason:<20} {n:>2}  {', '.join(tests)}")
    else:
        print("no hallucination signals detected")

    clean = [r["test_id"] for r in chat if r["hallucination_detected"] == "No"]
    if clean:
        print(f"\nclean ({len(clean)}): {', '.join(clean)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
