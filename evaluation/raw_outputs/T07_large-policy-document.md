# T07 — large-policy-document.txt (Map-Reduce latency test)

- engagement_id: `baseline-lab3-largepolicy` (Lab3-LargePolicy)
- endpoint: `POST /report/generate` format=`docx`
- rag_level: `standard`
- hardware_mode: `gpu_full(cuda)`
- model: `Qwen3.5-4B-Q4_K_M.gguf`
- latency_sec: **54.39**
- analysis_path_detected: **flat**
- findings_count: 6
- overall_risk_rating: Low
- download_url: /report/download/baseline-lab3-largepolicy_20261126100405_f6c322.docx

## Progress trace (from /api/report/progress)

- stage=map done=0/1 detail='Single-pass analysis'
- stage=idle done=1/1 detail='Single-pass analysis'

## Response payload

```json
{
  "download_url": "/report/download/baseline-lab3-largepolicy_20261126100405_f6c322.docx",
  "filename": "baseline-lab3-largepolicy_20261126100405_f6c322.docx",
  "format": "docx",
  "findings_count": 6,
  "overall_risk_rating": "Low",
  "engagement": {
    "client_name": "Baseline",
    "engagement_name": "Lab3-LargePolicy"
  }
}
```
