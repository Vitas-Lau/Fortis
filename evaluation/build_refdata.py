"""
Fetch and derive the authoritative framework reference sets used by the
citation audit, into evaluation/refdata/.

The audit needs two things for every ID a response cites:
  1. is it in this project's corpus?  -> core/frameworks/*.json   (read at runtime)
  2. does it exist in reality?       -> the published edition      (built here)

Set (2) must be the real published edition, otherwise a legitimate control that
this project simply does not ship would be misreported as a hallucination. That
is why these lists are downloaded rather than hand-written.

Outputs (all under evaluation/refdata/):
  cwe_full_ids.json   all CWE weakness IDs from the MITRE CWE XML dictionary
  attack_ids.json     all MITRE ATT&CK enterprise technique/tactic IDs (STIX)
  nist80053_ids.json  all SP 800-53 Rev 5 control IDs (NIST OSCAL catalog)
  csf_core.json       NIST CSF 2.0 active categories/subcategories + the
                      withdrawn CSF 1.1 IDs, parsed from the NIST OSCAL YAML

Run once; the runner then works offline.
    ./.venv/Scripts/python.exe evaluation/build_refdata.py
"""

from __future__ import annotations

import csv
import io
import json
import os
import re
import sys
import urllib.request
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EVAL_DIR = os.path.join(ROOT, "evaluation")
OUT = os.path.join(EVAL_DIR, "refdata")

CWE_ZIP = "https://cwe.mitre.org/data/xml/cwec_latest.xml.zip"
ATTACK_JSON = ("https://raw.githubusercontent.com/mitre-attack/attack-stix-data/"
               "master/enterprise-attack/enterprise-attack.json")
NIST53_JSON = ("https://raw.githubusercontent.com/usnistgov/oscal-content/main/"
               "nist.gov/SP800-53/rev5/json/NIST_SP-800-53_rev5_catalog.json")
CSF_YAML = ("https://raw.githubusercontent.com/usnistgov/oscal-content/"
            "78650f02ad9321bb7b817846f8fbd4f2bcd620de/nist.gov/CSF/v2.0/yaml/"
            "NIST_CSF_v2.0_catalog.yaml")


def fetch(url: str, timeout: int = 180) -> bytes:
    print(f"  GET {url}")
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return r.read()


def build_cwe() -> None:
    raw = fetch(CWE_ZIP)
    zf = zipfile.ZipFile(io.BytesIO(raw))
    name = next(n for n in zf.namelist() if n.endswith(".xml"))
    xml = zf.read(name).decode("utf-8", errors="replace")
    ids = sorted({int(m) for m in re.findall(r'<Weakness[^>]*ID="(\d+)"', xml)})
    json.dump([f"CWE-{i}" for i in ids], open(os.path.join(OUT, "cwe_full_ids.json"), "w"))
    print(f"  -> {len(ids)} CWE weaknesses")


def build_attack() -> None:
    data = json.loads(fetch(ATTACK_JSON, timeout=300))
    ids = set()
    for obj in data.get("objects", []):
        if obj.get("type") in ("attack-pattern", "x-mitre-tactic"):
            for ref in obj.get("external_references", []):
                if ref.get("source_name") == "mitre-attack" and ref.get("external_id"):
                    ids.add(ref["external_id"])
    json.dump(sorted(ids), open(os.path.join(OUT, "attack_ids.json"), "w"))
    print(f"  -> {len(ids)} ATT&CK IDs")


def build_nist53() -> None:
    cat = json.loads(fetch(NIST53_JSON))["catalog"]
    ids = set()

    def walk(node):
        if isinstance(node, dict):
            i = node.get("id")
            if isinstance(i, str) and len(i) <= 14:
                ids.add(i.upper())
            for key in ("controls", "groups", "parts", "control"):
                if isinstance(node.get(key), (list, dict)):
                    walk(node[key])
        elif isinstance(node, list):
            for x in node:
                walk(x)

    walk(cat)
    ctrl = sorted(
        i for i in ids
        if i[:2] in ("AC", "AT", "AU", "CA", "CM", "CP", "IA", "IR", "MP", "PE",
                     "PL", "PM", "PS", "PT", "RA", "SA", "SC", "SI", "SR")
        and "-" in i
    )
    json.dump(ctrl, open(os.path.join(OUT, "nist80053_ids.json"), "w"))
    print(f"  -> {len(ctrl)} SP 800-53 control IDs")


def parse_csf_core(yaml_text: str) -> dict:
    """Two-level parse: a category's own props stop at its `controls:` key, and
    a subcategory's own props stop at its `parts:` key, so a child's withdrawn
    status never marks the parent."""
    lines = yaml_text.split("\n")
    cat_re = re.compile(r"^(\s*)- id: ((?:GV|ID|PR|DE|RS|RC)\.[A-Z]{2})\s*$")
    sub_re = re.compile(r"^(\s*)- id: ((?:GV|ID|PR|DE|RS|RC)\.[A-Z]{2}-\d{2})\s*$")

    cats, cur_start, cur_id = [], None, None
    for i, line in enumerate(lines):
        m = cat_re.match(line)
        if m:
            if cur_id is not None:
                cats.append((cur_start, i, cur_id))
            cur_start, cur_id = i, m.group(2).upper()
    if cur_id is not None:
        cats.append((cur_start, len(lines), cur_id))

    active_cat, withdrawn_cat = set(), set()
    active_sub, withdrawn_sub = set(), set()

    for start, end, cid in cats:
        own_end = end
        for j in range(start, end):
            if re.match(r"^\s*controls:\s*$", lines[j]):
                own_end = j
                break
        (withdrawn_cat if any("value: withdrawn" in lines[j]
                              for j in range(start, own_end)) else active_cat).add(cid)

        subs = [(j, m.group(2).upper())
                for j in range(start, end)
                if (m := sub_re.match(lines[j]))]
        for k, (s_start, sid) in enumerate(subs):
            s_end = subs[k + 1][0] if k + 1 < len(subs) else end
            own_s_end = s_end
            for j in range(s_start, s_end):
                if re.match(r"^\s*parts:\s*$", lines[j]):
                    own_s_end = j
                    break
            (withdrawn_sub if any("value: withdrawn" in lines[j]
                                  for j in range(s_start, own_s_end))
             else active_sub).add(sid)

    return {
        "NIST_CSF_CAT": sorted(active_cat),
        "NIST_CSF_WITHDRAWN": sorted(withdrawn_cat),
        "NIST_CSF_SUB": sorted(active_sub),
        "NIST_CSF_SUB_WITHDRAWN": sorted(withdrawn_sub),
        "_edition": "NIST CSF 2.0 (OSCAL v1.2.2)",
        "_source": CSF_YAML,
    }


def build_csf() -> None:
    text = fetch(CSF_YAML, timeout=180).decode("utf-8", errors="replace")
    core = parse_csf_core(text)
    json.dump(core, open(os.path.join(OUT, "csf_core.json"), "w", encoding="utf-8"),
              indent=1)
    print(f"  -> {len(core['NIST_CSF_CAT'])} active categories, "
          f"{len(core['NIST_CSF_SUB'])} active subcategories, "
          f"{len(core['NIST_CSF_WITHDRAWN'])} withdrawn categories")


def main() -> int:
    os.makedirs(OUT, exist_ok=True)
    for name, fn in (("CWE", build_cwe), ("ATT&CK", build_attack),
                     ("NIST 800-53", build_nist53), ("NIST CSF", build_csf)):
        print(f"[{name}]")
        try:
            fn()
        except Exception as exc:
            print(f"  ! FAILED: {type(exc).__name__}: {exc}", file=sys.stderr)
            return 1
    print(f"\nReference data written to {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
