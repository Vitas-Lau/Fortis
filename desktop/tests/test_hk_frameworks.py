"""Hong Kong DPO S17 / G3 corpus, routing and benchmark integrity tests.

Fully offline: EMBEDDING_BACKEND=hash-stub is the caller's responsibility, and
nothing here needs the LLM, the network, or the real embedding model.
"""
import json
import os

import pytest

from core import vectorstore
from core.frameworks import FRAMEWORK_NAMES, seed_all_frameworks
from core.rag import detect_requested_frameworks

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
FW_DIR = os.path.join(ROOT, 'core', 'frameworks')
BENCH = os.path.join(ROOT, 'evaluation', 'datasets', 'hk_soc_benchmark.json')

WHITELIST = {'id', 'text', 'title', 'category', 'function', 'version', 'source_url', 'framework'}
HK_FILES = {'hk_dpo_s17.json': 'HK_DPO_S17', 'hk_dpo_g3.json': 'HK_DPO_G3'}


def _load(filename):
    with open(os.path.join(FW_DIR, filename), encoding='utf-8') as fh:
        return json.load(fh)


@pytest.fixture(scope='module')
def corpora():
    return {name: _load(name) for name in HK_FILES}


# --------------------------------------------------------------------------- #
# 1. Schema validity
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize('filename', sorted(HK_FILES))
def test_corpus_schema_is_valid(corpora, filename):
    entries = corpora[filename]
    assert len(entries) >= 25, f'{filename} has only {len(entries)} entries'

    seen = set()
    for e in entries:
        assert isinstance(e.get('id'), str) and e['id'].strip(), 'missing id'
        assert isinstance(e.get('text'), str) and e['text'].strip(), f'empty text for {e["id"]}'
        assert e['id'] not in seen, f'duplicate id {e["id"]}'
        seen.add(e['id'])
        extra = set(e) - WHITELIST
        assert not extra, f'{e["id"]} has non-whitelisted keys: {extra}'


@pytest.mark.parametrize('filename', sorted(HK_FILES))
def test_files_are_ascii_utf8(filename):
    raw = open(os.path.join(FW_DIR, filename), 'rb').read()
    # ASCII-safe on disk, and readable as UTF-8 on a cp950 Windows locale.
    assert raw.isascii(), f'{filename} contains non-ASCII bytes'
    raw.decode('utf-8')


@pytest.mark.parametrize('filename,framework', sorted(HK_FILES.items()))
def test_corpus_carries_provenance(corpora, filename, framework):
    for e in corpora[filename]:
        assert e.get('framework') == framework
        assert e.get('version'), f'{e["id"]} missing version'
        assert e.get('source_url', '').startswith('https://www.govcert.gov.hk/')


def test_s17_ids_use_clause_numbering(corpora):
    import re
    for e in corpora['hk_dpo_s17.json']:
        assert re.fullmatch(r'\d{1,2}\.\d{1,2}\.\d{1,3}', e['id']), \
            f'S17 id {e["id"]} is not x.y.z clause notation'


def test_g3_ids_use_lettered_subitems(corpora):
    import re
    for e in corpora['hk_dpo_g3.json']:
        assert re.fullmatch(r'\d{1,2}\.\d{1,2}\([a-z]\)', e['id']), \
            f'G3 id {e["id"]} is not x.y(letter) notation'


# --------------------------------------------------------------------------- #
# 2. Registration and seeding
# --------------------------------------------------------------------------- #

def test_framework_names_include_hk():
    assert 'HK_DPO_S17' in FRAMEWORK_NAMES
    assert 'HK_DPO_G3' in FRAMEWORK_NAMES


def test_seed_all_frameworks_includes_hk_without_error():
    seed_all_frameworks()
    for fw in ('HK_DPO_S17', 'HK_DPO_G3'):
        got = vectorstore._frameworks.get(where={'framework': fw})
        assert got['ids'], f'{fw} seeded nothing'
        assert all(i.startswith(f'{fw}::') for i in got['ids'])


def test_seeded_metadata_carries_version_and_source(corpora):
    seed_all_frameworks()
    got = vectorstore._frameworks.get(where={'framework': 'HK_DPO_S17'})
    metas = got['metadatas']
    assert metas
    assert all(m.get('version') for m in metas)
    assert all(m.get('source_url') for m in metas)
    assert all(m.get('control_id') for m in metas)


# --------------------------------------------------------------------------- #
# 3. Intent routing
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize('query,expected', [
    ('Assess this IAM policy against HK OGCIO/DPO S17 and G3.',
     ['HK_DPO_S17', 'HK_DPO_G3']),
    ('What are the password requirements under Hong Kong government policy?',
     ['HK_DPO_S17', 'HK_DPO_G3']),
    ('Map these findings to NIST CSF 2.0', ['NIST_CSF']),
    ('Which OWASP Top 10 category applies here?', ['OWASP_TOP10']),
    ('Check ISO 27001 A.8.20 coverage', ['ISO_27001']),
    ('Compare S17 with NIST CSF and ISO 27001',
     ['HK_DPO_S17', 'HK_DPO_G3', 'NIST_CSF', 'ISO_27001']),
])
def test_detect_requested_frameworks_routes(query, expected):
    assert detect_requested_frameworks(query) == expected


@pytest.mark.parametrize('query', [
    'What are the biggest security gaps in this policy?',
    'Review this firewall configuration for issues.',
    'What vulnerabilities are in this application?',
    'Summarise the key risks in the uploaded document.',
    '',
])
def test_detect_requested_frameworks_is_neutral_for_generic_queries(query):
    assert detect_requested_frameworks(query) == []


def test_detect_requested_frameworks_is_pure():
    # No I/O, no global state mutation; repeated calls agree.
    q = 'Assess against S17 and NIST CSF'
    assert detect_requested_frameworks(q) == detect_requested_frameworks(q)


# --------------------------------------------------------------------------- #
# 4. Context block actually surfaces HK chunks
# --------------------------------------------------------------------------- #

def _fake_hits(framework, n=6):
    entries = _load('hk_dpo_s17.json' if framework == 'HK_DPO_S17' else 'hk_dpo_g3.json')
    out = []
    for i, e in enumerate(entries[:n]):
        out.append({
            'id': f'{framework}::{e["id"]}',
            'text': e['text'],
            'metadata': {'framework': framework, 'control_id': e['id'],
                         'version': e.get('version'), 'source_url': e.get('source_url')},
            'distance': 0.1 * i,
        })
    return out


@pytest.mark.parametrize('query', [
    'What does Hong Kong S17 require for logging?',
    'Assess this IAM policy against G3 access control requirements.',
])
def test_context_block_returns_hk_chunks(monkeypatch, query):
    hk = _fake_hits('HK_DPO_S17') + _fake_hits('HK_DPO_G3')

    def fake_specific(q, frameworks):
        return [h for h in hk if h['metadata']['framework'] in frameworks]

    monkeypatch.setattr('core.rag._query_specific_frameworks', fake_specific)
    monkeypatch.setattr(vectorstore, 'query_frameworks', lambda *a, **k: [])
    monkeypatch.setattr(vectorstore, 'query_frameworks_hybrid', lambda *a, **k: [])
    monkeypatch.setattr(vectorstore, 'query_user_documents', lambda *a, **k: [])
    monkeypatch.setattr(vectorstore, 'query_user_documents_hybrid', lambda *a, **k: [])

    from core.rag import build_context_block
    block = build_context_block('dummy-engagement', query)

    assert 'HK_DPO_S17' in block
    assert 'HK_DPO_G3' in block
    assert '=== Relevant framework controls ===' in block
    # the citation tag the model is told to reuse
    assert 'HK_DPO_S17 ' in block


def test_context_block_unchanged_for_generic_query(monkeypatch):
    """No framework named -> routing must not run and general hits pass through."""
    calls = []

    def spy(*a, **k):
        calls.append(a)
        return []

    monkeypatch.setattr(vectorstore, 'query_frameworks', spy)
    monkeypatch.setattr(vectorstore, 'query_frameworks_hybrid', spy)
    monkeypatch.setattr(vectorstore, 'query_user_documents', spy)
    monkeypatch.setattr(vectorstore, 'query_user_documents_hybrid', spy)

    from core.rag import build_context_block
    block = build_context_block('dummy', 'Summarise the uploaded document.')

    assert calls, 'general framework retrieval should still run'
    assert '=== Relevant framework controls ===' not in block
    assert '=== No uploaded documents matched this query ===' in block


# --------------------------------------------------------------------------- #
# 5. Benchmark ground truth resolves
# --------------------------------------------------------------------------- #

def test_benchmark_is_well_formed():
    data = json.load(open(BENCH, encoding='utf-8'))
    assert len(data) == 20
    assert [q['id'] for q in data] == [f'HK{i:02d}' for i in range(1, 21)]
    for q in data:
        assert q['domain']
        assert q['soc_role'] in ('grc', 'cti', 'general', 'code')
        assert q['prompt'].strip()
        assert isinstance(q['expected_frameworks'], list)
        assert isinstance(q['expected_control_ids'], list)


def test_every_expected_control_id_exists(corpora):
    """Zero broken ground truth: every cited id resolves to a real corpus entry."""
    s17 = {e['id'] for e in corpora['hk_dpo_s17.json']}
    g3 = {e['id'] for e in corpora['hk_dpo_g3.json']}
    data = json.load(open(BENCH, encoding='utf-8'))

    broken = []
    for q in data:
        for cid in q['expected_control_ids']:
            prefix, _, clause = cid.partition(':')
            pool = {'S17': s17, 'G3': g3}.get(prefix)
            assert pool is not None, f'{q["id"]}: bad prefix in {cid}'
            if clause not in pool:
                broken.append(f'{q["id"]}: {cid}')
    assert not broken, 'unresolved ground truth: ' + '; '.join(broken)


def test_benchmark_covers_required_domains():
    data = json.load(open(BENCH, encoding='utf-8'))
    domains = {q['domain'] for q in data}
    for required in ('IAM', 'Firewall/Gateway', 'Password/MFA', 'Logging',
                     'IncidentReporting', 'Cloud/ThirdParty', 'Endpoint/Remote',
                     'Backup', 'Negative/Edge'):
        assert required in domains, f'missing domain {required}'


def test_benchmark_sample_files_exist():
    data = json.load(open(BENCH, encoding='utf-8'))
    for q in data:
        if q['sample_file']:
            assert os.path.exists(os.path.join(ROOT, q['sample_file'])), \
                f'{q["id"]}: {q["sample_file"]} not found'
