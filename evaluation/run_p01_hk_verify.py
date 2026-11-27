"""Stage 3 verification: re-run probe P01 to confirm HK_COMPLIANCE_GAP is closed.

Proves the enhancement end-to-end through the same HTTP path Stage 2 measured:
fresh engagement -> upload iam-policy.json -> role=grc -> the original P01
prompt. The verdict is not "did it mention S17" but "did it cite real S17/G3
clause numbers that exist in the shipped corpora".
"""
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

HOST = 'http://127.0.0.1:8757'
RAW = os.path.join(ROOT, 'evaluation', 'raw_outputs', 'P01_stage3_hk_verification.md')
OUT = os.path.join(ROOT, 'evaluation', 'step3_p01_verification.json')

PROMPT = ('Assess this IAM policy against Hong Kong Government OGCIO/DPO Baseline IT '
          'Security Policy (S17) and IT Security Guidelines (G3). Cite the exact S17 and '
          'G3 section/clause numbers for each finding.')
SAMPLE = os.path.join(ROOT, 'training', 'sample-lab-docs', 'iam-policy.json')


def log(msg):
    print(f'[{datetime.now():%H:%M:%S}] {msg}', flush=True)


def req(method, path, body=None, timeout=600):
    data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(HOST + path, data=data, method=method,
                               headers={'Content-Type': 'application/json'})
    with urllib.request.urlopen(r, timeout=timeout) as resp:
        return json.loads(resp.read().decode())


def upload(engagement_id, path):
    boundary = '----FortisBoundary' + str(int(time.time()))
    name = os.path.basename(path)
    content = open(path, 'rb').read()
    parts = []
    parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="engagement_id"\r\n\r\n'
                 f'{engagement_id}\r\n'.encode())
    parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="file"; '
                 f'filename="{name}"\r\nContent-Type: application/octet-stream\r\n\r\n'.encode())
    parts.append(content)
    parts.append(f'\r\n--{boundary}--\r\n'.encode())
    body = b''.join(parts)
    r = urllib.request.Request(f'{HOST}/upload', data=body, method='POST',
                               headers={'Content-Type': f'multipart/form-data; boundary={boundary}'})
    with urllib.request.urlopen(r, timeout=300) as resp:
        return json.loads(resp.read().decode())


def load_corpus():
    fw = os.path.join(ROOT, 'core', 'frameworks')
    s17 = {e['id'] for e in json.load(open(os.path.join(fw, 'hk_dpo_s17.json'), encoding='utf-8'))}
    g3 = {e['id'] for e in json.load(open(os.path.join(fw, 'hk_dpo_g3.json'), encoding='utf-8'))}
    return s17, g3


def audit(text, s17, g3):
    """Extract every S17/G3-looking citation and check it really exists."""
    found, valid, invalid = [], [], []

    for m in re.finditer(r'\bS17[\s\-:]*(?:clause\s*|section\s*)?(\d{1,2}\.\d{1,2}\.\d{1,3})\b', text, re.I):
        found.append(('S17', m.group(1)))
    for m in re.finditer(r'\bG3[\s\-:]*(?:clause\s*|section\s*)?(\d{1,2}\.\d{1,2})\s*\(([a-z])\)', text, re.I):
        found.append(('G3', f'{m.group(1)}({m.group(2).lower()})'))

    seen = set()
    for fw, cid in found:
        if (fw, cid) in seen:
            continue
        seen.add((fw, cid))
        pool = s17 if fw == 'S17' else g3
        (valid if cid in pool else invalid).append(f'{fw} {cid}')

    # A bare "S17 clause 11.2.4" without the S17 prefix also counts.
    for m in re.finditer(r'(?<![\w.])(\d{1,2}\.\d{1,2}\.\d{1,3})(?![\w.])', text):
        cid = m.group(1)
        if ('S17', cid) in seen:
            continue
        seen.add(('S17', cid))
        (valid if cid in s17 else invalid).append(f'S17 {cid} (bare)')

    mentions_hk = bool(re.search(r'\bS17\b|\bG3\b|OGCIO|Digital Policy Office|DPO\b', text))
    declined = bool(re.search(r"do not contain the text|cannot (answer|verify)|not available in|"
                              r"no information (about|on)|unable to (cite|verify|confirm)", text, re.I))
    return {
        'citations_found': len(seen),
        'valid_citations': sorted(set(valid)),
        'invalid_citations': sorted(set(invalid)),
        'mentions_hk_instrument': mentions_hk,
        'still_declines_or_hedges': declined,
    }


def main():
    s17, g3 = load_corpus()
    log(f'corpora: S17 {len(s17)} clauses, G3 {len(g3)} sub-items')

    # Prefer an already-running server; only start one if the port is free.
    started = None
    try:
        h = req('GET', '/health', timeout=5)
        log(f'using running server: frameworks_loaded includes HK = '
            f'{"HK_DPO_S17" in h.get("frameworks_loaded", [])}')
    except Exception:
        log('no server on 8757; starting one')
        env = dict(os.environ)
        env['RAG_LEVEL'] = 'standard'
        started = subprocess.Popen(
            [os.path.join(ROOT, '.venv', 'Scripts', 'python.exe'), '-m', 'server.app', '--port', '8757'],
            cwd=ROOT, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        for _ in range(120):
            try:
                req('GET', '/health', timeout=3)
                break
            except Exception:
                time.sleep(1)
        log('server up')

    try:
        eng = req('POST', '/engagements',
                  {'client_name': 'Stage3-Verify', 'engagement_name': 'P01-HK-Rerun'})
        eid = eng.get('id') or eng.get('engagement_id')
        log(f'engagement {eid}')

        up = upload(eid, SAMPLE)
        log(f'uploaded: {json.dumps(up)[:180]}')
        time.sleep(2)

        # warm-up on a separate throwaway turn absorbs model load, so the timed
        # turn measures inference only
        try:
            req('POST', '/chat', {'engagement_id': eid, 'message': 'ok',
                                  'role': 'grc', 'history': []}, timeout=900)
        except Exception as exc:
            log(f'warm-up skipped: {type(exc).__name__}')

        t0 = time.perf_counter()
        resp = req('POST', '/chat', {'engagement_id': eid, 'message': PROMPT,
                                     'role': 'grc', 'history': []}, timeout=900)
        latency = round(time.perf_counter() - t0, 2)
        answer = resp.get('response') or resp.get('answer') or json.dumps(resp)
        log(f'response {len(answer)} chars in {latency}s')

        result = audit(answer, s17, g3)
        resolved = (len(result['valid_citations']) > 0
                    and not result['invalid_citations']
                    and not result['still_declines_or_hedges'])

        os.makedirs(os.path.dirname(RAW), exist_ok=True)
        with open(RAW, 'w', encoding='utf-8') as fh:
            fh.write('# P01 (Stage 3) - Hong Kong S17/G3 compliance probe\n\n')
            fh.write(f'- ran: {datetime.now(timezone.utc).isoformat()}\n')
            fh.write(f'- mode: grc\n- latency_sec: **{latency}**\n')
            fh.write(f'- prompt: {PROMPT}\n\n## Verbatim response\n\n{answer}\n')

        payload = {'ran_at': datetime.now(timezone.utc).isoformat(), 'latency_sec': latency,
                   'audit': result, 'gap_resolved': resolved, 'raw_output': RAW}
        with open(OUT, 'w', encoding='utf-8') as fh:
            json.dump(payload, fh, indent=2)

        print()
        print('=' * 78)
        print('P01 STAGE 3 VERIFICATION')
        print('=' * 78)
        print(f'latency                 : {latency}s')
        print(f'citations found         : {result["citations_found"]}')
        print(f'valid S17/G3 clauses    : {len(result["valid_citations"])}'
              f' {result["valid_citations"][:8]}')
        print(f'invalid / invented      : {len(result["invalid_citations"])}'
              f' {result["invalid_citations"][:8]}')
        print(f'mentions HK instrument  : {result["mentions_hk_instrument"]}')
        print(f'still declines / hedges : {result["still_declines_or_hedges"]}')
        print('-' * 78)
        print(f'HK_COMPLIANCE_GAP resolved: {"YES" if resolved else "NO"}')
        print('=' * 78)
        return 0 if resolved else 1
    finally:
        if started is not None:
            started.terminate()
            try:
                started.wait(timeout=20)
            except subprocess.TimeoutExpired:
                started.kill()
            log('started server stopped')


if __name__ == '__main__':
    raise SystemExit(main())
