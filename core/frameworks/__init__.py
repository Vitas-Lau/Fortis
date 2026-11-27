import json
import os

from ..vectorstore import seed_framework_chunks

_DIR = os.path.dirname(__file__)

_FILES = {
    "NIST_CSF": "nist_csf.json",
    "OWASP_TOP10": "owasp_top10.json",
    "CIS_CONTROLS": "cis_controls.json",
    "MITRE_ATTACK": "mitre_attack.json",
    "CIS_BENCHMARKS": "cis_benchmarks.json",
    "ISO_27001": "iso_27001.json",
    "PCI_DSS": "pci_dss.json",
    "SOC_2": "soc2_tsc.json",
    "GDPR": "gdpr.json",
    "HIPAA": "hipaa_security_rule.json",
    "NIST_800-53": "nist_800_53.json",
    "CWE_TOP25": "cwe_top25.json",
    "HK_DPO_S17": "hk_dpo_s17.json",
    "HK_DPO_G3": "hk_dpo_g3.json",
}


def seed_all_frameworks() -> None:
    for framework, filename in _FILES.items():
        path = os.path.join(_DIR, filename)
        # Explicit utf-8: these corpora contain non-ASCII punctuation, and the
        # default codec on a zh-HK Windows install is cp950, which would raise
        # UnicodeDecodeError here.
        with open(path, encoding="utf-8") as f:
            entries = json.load(f)
        seed_framework_chunks(framework, entries)


FRAMEWORK_NAMES = list(_FILES.keys())
