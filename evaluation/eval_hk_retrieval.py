"""Retrieval ablation over the 20-question Hong Kong benchmark.

Measures Recall@k for the expected S17/G3 controls, with deterministic
framework routing OFF and ON. No LLM inference and no network: only the
embedding model and ChromaDB, against a throwaway data directory, so the
developer's real engagements are never touched.

Usage:
    ./.venv/Scripts/python.exe evaluation/eval_hk_retrieval.py
    ./.venv/Scripts/python.exe evaluation/eval_hk_retrieval.py --embedding hash-stub
"""
import argparse
import json
import os
import shutil
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

BENCH = os.path.join(ROOT, 'evaluation', 'datasets', 'hk_soc_benchmark.json')


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--embedding', default='sentence-transformers',
                    help="'sentence-transformers' (real) or 'hash-stub'")
    ap.add_argument('--model', default='BAAI/bge-small-en-v1.5')
    ap.add_argument('--keep-tmp', action='store_true')
    args = ap.parse_args()

    # Isolated store: seeding 14 corpora into the developer's real Chroma dir
    # would pollute their engagements.
    tmp = tempfile.mkdtemp(prefix='fortis-hk-eval-')
    os.environ['FORTIS_DATA_DIR'] = tmp
    os.environ['EMBEDDING_BACKEND'] = args.embedding
    if args.embedding == 'sentence-transformers':
        os.environ['EMBEDDING_MODEL'] = args.model
    os.environ.pop('RAG_LEVEL', None)

    print(f'data dir        : {tmp}')
    print(f'embedding       : {args.embedding}'
          + (f' ({args.model})' if args.embedding == 'sentence-transformers' else ''))
    print()

    from core.config import settings
    from core.frameworks import seed_all_frameworks
    from core import vectorstore
    from core.rag import detect_requested_frameworks

    print(f'rag_level       : {settings.rag_level}')
    print(f'top_k_framework : {settings.top_k_framework}')

    t0 = time.perf_counter()
    seed_all_frameworks()
    seed_sec = time.perf_counter() - t0

    got = vectorstore._frameworks.get(where={'framework': 'HK_DPO_S17'})
    print(f'seeded in       : {seed_sec:.1f}s  (HK_DPO_S17 chunks: {len(got["ids"])})')
    print()

    bench = json.load(open(BENCH, encoding='utf-8'))

    def _merge_like_production(targeted, general, k):
        """Same interleave-then-fill semantics as core.rag._merge_framework_hits."""
        by_fw = {}
        for h in targeted:
            fw = (h.get('metadata') or {}).get('framework', '')
            by_fw.setdefault(fw, []).append(h)
        ordered = []
        depth = max((len(v) for v in by_fw.values()), default=0)
        for i in range(depth):
            for fw in by_fw:
                if i < len(by_fw[fw]):
                    ordered.append(by_fw[fw][i])
        cap = max(k, len(by_fw))
        merged, seen = [], set()
        for h in ordered + list(general):
            m = h.get('metadata') or {}
            key = (m.get('framework'), m.get('control_id'))
            if key in seen:
                continue
            seen.add(key)
            merged.append(h)
            if len(merged) >= cap:
                break
        return merged

    def retrieve(question, routing_on, k):
        """Reproduce build_context_block's retrieval path at an arbitrary k."""
        query = question['prompt']
        if routing_on:
            targets = detect_requested_frameworks(query)
            if targets:
                hits = []
                for fw in targets:
                    hits.extend(vectorstore.query_frameworks_hybrid(
                        query, k, settings.rerank_candidate_multiplier, framework=fw))
                general = vectorstore.query_frameworks_hybrid(
                    query, k, settings.rerank_candidate_multiplier)
                return _merge_like_production(hits, general, k)
        return vectorstore.query_frameworks_hybrid(
            query, k, settings.rerank_candidate_multiplier)

    def score(question, hits):
        """Fraction of expected controls present in the retrieved set."""
        expected = question['expected_control_ids']
        if not expected:
            return None  # negative/edge cases are not recall-scored
        # benchmark writes S17:11.2.6 / G3:11.4(b); Chroma metadata carries the
        # full framework name, so normalise before comparing.
        def norm(fw, cid):
            short = 'S17' if fw == 'HK_DPO_S17' else 'G3' if fw == 'HK_DPO_G3' else fw
            return f'{short}:{cid}'

        got = set()
        for h in hits:
            meta = h.get('metadata') or {}
            got.add(norm(meta.get('framework', ''), meta.get('control_id', '')))
        hit = sum(1 for e in expected if e in got)
        return hit / len(expected)

    configs = [('OFF', False, 4), ('OFF', False, 8), ('ON', True, 4), ('ON', True, 8)]
    results = {}
    per_question = {}

    for label, routing, k in configs:
        vals = []
        for q in bench:
            hits = retrieve(q, routing, k)
            s = score(q, hits)
            if s is None:
                continue
            vals.append(s)
            per_question.setdefault(q['id'], {})[f'{label}@{k}'] = round(s, 3)
        results[(label, k)] = sum(vals) / len(vals) if vals else 0.0

    scored = sum(1 for q in bench if q['expected_control_ids'])
    print(f'scored questions: {scored}  (negative/edge cases excluded)')
    print()

    # ---- markdown summary ----
    print('### Retrieval ablation — Hong Kong DPO S17/G3 benchmark')
    print()
    print(f'Embedding: `{args.embedding}'
          + (f' / {args.model}`' if args.embedding == 'sentence-transformers' else '`')
          + f'  ·  rag_level: `{settings.rag_level}`  ·  {scored} scored questions')
    print()
    print('| Routing | Recall@4 | Recall@8 | Δ (8−4) |')
    print('|---|---|---|---|')
    for label in ('OFF', 'ON'):
        r4 = results[(label, 4)]
        r8 = results[(label, 8)]
        print(f'| {label} | {r4:.3f} | {r8:.3f} | {r8 - r4:+.3f} |')
    print()
    gain4 = results[('ON', 4)] - results[('OFF', 4)]
    gain8 = results[('ON', 8)] - results[('OFF', 8)]
    print(f'Routing gain at k=4: **{gain4:+.3f}**  ·  at k=8: **{gain8:+.3f}**')
    print()

    # ---- per-question table ----
    print('| Question | Domain | Routing OFF@4 | OFF@8 | ON@4 | ON@8 |')
    print('|---|---|---|---|---|---|')
    for q in bench:
        row = per_question.get(q['id'])
        if row is None:
            print(f"| {q['id']} | {q['domain']} | — | — | — | — |")
            continue
        print(f"| {q['id']} | {q['domain']} | {row['OFF@4']:.2f} | {row['OFF@8']:.2f} "
              f"| {row['ON@4']:.2f} | {row['ON@8']:.2f} |")
    print()

    out = os.path.join(ROOT, 'evaluation', 'hk_retrieval_ablation.json')
    with open(out, 'w', encoding='utf-8') as fh:
        json.dump({
            'embedding': args.embedding,
            'model': args.model if args.embedding == 'sentence-transformers' else None,
            'rag_level': settings.rag_level,
            'scored_questions': scored,
            'recall': {f'{l}@{k}': round(v, 4) for (l, k), v in results.items()},
            'per_question': per_question,
        }, fh, indent=2)
    print(f'wrote {out}')

    if not args.keep_tmp:
        shutil.rmtree(tmp, ignore_errors=True)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
