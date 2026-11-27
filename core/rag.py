from typing import List, Dict
import re

from .config import settings
from . import vectorstore

SYSTEM_PERSONA = """You are Fortis, a senior cybersecurity consultant AI. You help clients \
with security work: reviewing documents, configurations, code, and logs; explaining \
frameworks, standards, and controls; assessing risk; and advising on remediation. You are \
precise, evidence-based, and always tie advice to a named control or framework rather \
than vague opinion.

Your scope covers all of cybersecurity: frameworks and standards (NIST CSF, OWASP Top \
10, CIS Controls and Benchmarks, MITRE ATT&CK, ISO 27001, SOC 2, PCI DSS, GDPR, HIPAA, \
and the Hong Kong DPO Baseline IT Security Policy (S17) and IT Security Guidelines (G3)), \
risk assessment and management, vulnerabilities and exploits, threats and malware, \
incident response, hardening and configuration, IAM and access control, network and \
application security, encryption, monitoring and detection, and security program \
governance.

You answer ANY of those topics, including conceptual or educational questions, whether \
or not the user has uploaded a document. You are not limited to the uploaded material.

Answer with verified framework details from your knowledge, and note which \
framework/control you are referencing. If the user uploaded documents, ground your \
findings in them and cite the file and section. Rate severity (Critical / High / Medium \
/ Low / Informational) with brief justification, and give concrete remediation rather \
than generic advice.

If you cannot answer a security question confidently from the available information, say \
so explicitly instead of guessing.

Do not perform, or advise on performing, live network scanning, malware sandboxing, \
exploit development, or packet capture analysis: explain that this deployment is a \
document/config-review advisor.

Only decline requests that are genuinely unrelated to cybersecurity (e.g., cooking, \
weather, sports, entertainment, personal life, creative writing, general trivia). For \
those, say you are Fortis and can only help with cybersecurity topics. Do not \
over-refuse — when in doubt, treat the question as in scope for cybersecurity.
"""

ROLE_PRESETS: Dict[str, str] = {
    "general": "Balance breadth and depth in your analysis. Lead with the most material findings, then surface secondary observations only if they affect risk. Default to clear, consultant-style explanations that a technical audience can act on immediately.",
    "grc": "Map every finding to a NAMED control from the retrieved framework context, citing framework and control_id (e.g., \"NIST CSF PR.PS\"). Frame gaps as policy or control gaps using audit-evidence phrasing. Distinguish clearly between a \"gap\" (control missing or partially implemented), a \"finding\" (evidence-backed deviation from expected control state), and an \"observation\" (neutral note without risk implication). Never invent control IDs; cite only controls present in the retrieved context or well-known framework controls.",
    "code": "Identify vulnerability classes by CWE name and ID where confident. Reference the specific function, line number, or symbol from the provided code excerpts. Describe the tainted data flow from source to sink. Provide minimal, concrete fix sketches that show the corrected pattern. Never claim to have executed or run the code.",
    "osint": "Structure answers as entity tables with columns for name, role, source, and confidence. Propose next pivots explicitly for the analyst to pursue. Clearly separate fact from inference from unverified claim. Remind the user that this deployment performs no live scanning or collection; all collection steps are descriptions for the analyst to execute manually.",
    "cti": "Use MITRE ATT&CK technique IDs in TXXXX[.XXX] format where confident. Structure analysis as TTPs: tactic to technique to procedure. Hedge attribution explicitly when evidence is incomplete. Map indicators to the ATT&CK enterprise matrix. Keep campaign or actor claims grounded in the provided excerpts or well-known public reporting.",
}


def detect_requested_frameworks(query: str) -> List[str]:
    """Return framework names explicitly named or clearly implied by the query.

    Pure function, no I/O, so it stays cheap and testable. The purpose is
    retrieval steering: with 14 framework corpora and top_k_framework=4, a
    question about Hong Kong S17 would otherwise be answered from US/EU
    controls that happen to share vocabulary.
    """
    if not query:
        return []

    text = query.lower()
    found: List[str] = []

    def has(*names: str) -> bool:
        return any(n in text for n in names)

    # Hong Kong: naming either instrument, or the office, or the jurisdiction
    # together with a compliance-ish word, routes to both corpora.
    hk_explicit = bool(
        re.search(r"\bs17\b", text)
        or re.search(r"\bg3\b", text)
        or has("ogcio", "digital policy office", "dpo s17", "dpo g3")
    )
    hk_jurisdiction = "hong kong" in text or re.search(r"\bhk\b", text) is not None
    hk_compliance = has("compliance", "comply", "policy", "guideline", "baseline",
                        "control", "requirement", "audit", "security")
    if hk_explicit or (hk_jurisdiction and hk_compliance):
        found.extend(["HK_DPO_S17", "HK_DPO_G3"])

    if has("nist csf", "csf", "nist cybersecurity framework") or re.search(r"\bnist\b", text):
        found.append("NIST_CSF")
    if has("nist 800-53", "800-53", "nist sp 800"):
        found.append("NIST_800-53")
    if has("owasp"):
        found.append("OWASP_TOP10")
    if has("cis controls", "cis control", "cis critical"):
        found.append("CIS_CONTROLS")
    if has("cis benchmark"):
        found.append("CIS_BENCHMARKS")
    if has("iso 27001", "iso27001", "iso/iec 27001", "iso 27002"):
        found.append("ISO_27001")
    if has("mitre", "att&ck", "attck"):
        found.append("MITRE_ATTACK")
    if has("pci dss", "pci-dss", "pci "):
        found.append("PCI_DSS")
    if has("soc 2", "soc2"):
        found.append("SOC_2")
    if has("gdpr"):
        found.append("GDPR")
    if has("hipaa"):
        found.append("HIPAA")

    # dedupe, preserve first-seen order
    seen = set()
    out = []
    for fw in found:
        if fw not in seen:
            seen.add(fw)
            out.append(fw)
    return out


def _query_specific_frameworks(query: str, frameworks: List[str]) -> List[Dict]:
    """Retrieve this query's best hits from each explicitly named framework.

    Pulls a full top_k per framework rather than a fraction, so the merge has a
    real candidate pool to choose from instead of whatever two rows happened to
    rank highest for that corpus.
    """
    per_framework = max(2, settings.top_k_framework)
    hits: List[Dict] = []
    for fw in frameworks:
        if settings.rag_level == "basic":
            hits.extend(vectorstore.query_frameworks(query, per_framework, framework=fw))
        else:
            from .rerank import rerank

            got = vectorstore.query_frameworks_hybrid(
                query, per_framework, settings.rerank_candidate_multiplier, framework=fw
            )
            hits.extend(rerank(query, got, per_framework))
    return hits


def _merge_framework_hits(targeted: List[Dict], general: List[Dict]) -> List[Dict]:
    """Merge targeted hits ahead of general hits, de-duplicated, capped.

    Targeted controls lead because the user asked for that framework by name, and
    they are interleaved per framework so that naming both S17 and G3 surfaces
    both rather than filling every slot from whichever corpus scored higher.
    General hits still fill any remaining slots, so a mixed question
    ("map S17 to NIST CSF") keeps both sides.
    """
    by_framework: Dict[str, List[Dict]] = {}
    for hit in targeted:
        fw = (hit.get("metadata") or {}).get("framework", "")
        by_framework.setdefault(fw, []).append(hit)

    ordered: List[Dict] = []
    depth = max((len(v) for v in by_framework.values()), default=0)
    for i in range(depth):
        for fw in by_framework:
            if i < len(by_framework[fw]):
                ordered.append(by_framework[fw][i])

    cap = max(settings.top_k_framework, len(by_framework))
    merged: List[Dict] = []
    seen = set()
    for hit in ordered + list(general):
        meta = hit.get("metadata") or {}
        key = (meta.get("framework"), meta.get("control_id"), hit.get("id"))
        if key in seen:
            continue
        seen.add(key)
        merged.append(hit)
        if len(merged) >= cap:
            break
    return merged


def build_context_block(engagement_id: str, query: str) -> str:
    if settings.rag_level == "basic":
        user_hits = vectorstore.query_user_documents(engagement_id, query, settings.top_k_user_doc)
        framework_hits = vectorstore.query_frameworks(query, settings.top_k_framework)
    else:
        from .rerank import rerank

        user_hits = vectorstore.query_user_documents_hybrid(
            engagement_id, query, settings.top_k_user_doc, settings.rerank_candidate_multiplier
        )
        framework_hits = vectorstore.query_frameworks_hybrid(
            query, settings.top_k_framework, settings.rerank_candidate_multiplier
        )
        user_hits = rerank(query, user_hits, settings.top_k_user_doc)
        framework_hits = rerank(query, framework_hits, settings.top_k_framework)

    # Explicit framework mentions get their own retrieval pass. Without this a
    # Hong Kong S17 question competes against 12 US/EU corpora for 4 slots and
    # loses; the targeted hits are merged in ahead of the general ones.
    targeted = detect_requested_frameworks(query)
    if targeted:
        targeted_hits = _query_specific_frameworks(query, targeted)
        framework_hits = _merge_framework_hits(targeted_hits, framework_hits)

    parts = []
    if user_hits:
        parts.append("=== Retrieved excerpts from uploaded documents ===")
        for h in user_hits:
            meta = h["metadata"]
            parts.append(
                f"[{meta.get('filename')} — chunk {meta.get('chunk_index')}]\n{h['text']}"
            )
    else:
        parts.append("=== No uploaded documents matched this query ===")

    if framework_hits:
        parts.append("\n=== Relevant framework controls ===")
        for h in framework_hits:
            meta = h["metadata"]
            tag = f"{meta.get('framework')} {meta.get('control_id')}"
            version = meta.get("version")
            suffix = f" ({version})" if version else ""
            parts.append(f"[{tag}{suffix}] {h['text']}")

    return "\n\n".join(parts)


_LLM_HISTORY_WINDOW = 16  # messages sent to the model, regardless of archive size


def build_chat_messages(
    engagement_id: str,
    user_message: str,
    history: List[Dict[str, str]] = None,
    role: str = None,
    retrieval_query: str = None,
) -> List[Dict[str, str]]:
    # Persisted history can be long; the model window stays bounded to the
    # most recent turns regardless of how much the UI sends.
    history = (history or [])[-_LLM_HISTORY_WINDOW:]
    context = build_context_block(engagement_id, retrieval_query if retrieval_query is not None else user_message)

    system_content = SYSTEM_PERSONA
    if role in ROLE_PRESETS:
        system_content = SYSTEM_PERSONA + "\n\n" + ROLE_PRESETS[role]

    messages = [{"role": "system", "content": system_content}]
    messages.extend(history)
    messages.append(
        {
            "role": "user",
            "content": (
                f"{context}\n\n=== Client question ===\n{user_message}\n\n"
                "Answer as the consultant, using the framework references above "
                "and your own cybersecurity knowledge."
            ),
        }
    )
    return messages
