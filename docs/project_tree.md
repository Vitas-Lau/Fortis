# Fortis — Project Tree

- Root: `D:\SynologyDrive\LYN\Project\COMP3010HK CW2\Fortis`
- Generated: 2026-11-23 16:02 (local time)
- Inventory: **98 files**, 15 directories, 5.0 MB; 53 Python files, 9,303 Python lines.
- Excluded (generated or version-control noise): `.git/`, `.venv/`, `__pycache__/`, `.pytest_cache/`, `.pytest-tmp/`, `desktop/data/`, `.pytest-self-test.log`, `fortis-out.log`, `fortis-err.log`, `.llama-cuda-ok`, `training.zip`, `*.pyc`.

## Tree

```text
Fortis/
├── core                                      40 files
│   ├── frameworks                            14 files
│   │   ├── __init__.py                       869 B
│   │   ├── cis_benchmarks.json               11.9 KB
│   │   ├── cis_controls.json                 5.5 KB
│   │   ├── cwe_top25.json                    9.4 KB
│   │   ├── gdpr.json                         7.4 KB
│   │   ├── hipaa_security_rule.json          9.4 KB
│   │   ├── iso_27001.json                    28.8 KB
│   │   ├── loader.py                         2.7 KB
│   │   ├── mitre_attack.json                 11.9 KB
│   │   ├── nist_800_53.json                  28.1 KB
│   │   ├── nist_csf.json                     6.9 KB
│   │   ├── owasp_top10.json                  3.9 KB
│   │   ├── pci_dss.json                      7.1 KB
│   │   └── soc2_tsc.json                     6.2 KB
│   ├── reports                               6 files
│   │   ├── __init__.py                       0 B
│   │   ├── charts.py                         2.3 KB
│   │   ├── docx_report.py                    5.5 KB
│   │   ├── pdf_report.py                     7.9 KB
│   │   ├── pptx_report.py                    9.1 KB
│   │   └── schema.py                         1.6 KB
│   ├── routers                               5 files
│   │   ├── __init__.py                       0 B
│   │   ├── chat.py                           6.6 KB
│   │   ├── engagements.py                    3.4 KB
│   │   ├── report.py                         4.2 KB
│   │   └── upload.py                         3.0 KB
│   ├── analysis.py                           14.9 KB
│   ├── bm25.py                               3.2 KB
│   ├── condense.py                           2.9 KB
│   ├── config.py                             6.1 KB
│   ├── embeddings.py                         3.6 KB
│   ├── ingestion.py                          8.4 KB
│   ├── main.py                               1.3 KB
│   ├── progress.py                           1.7 KB
│   ├── rag.py                                6.8 KB
│   ├── report_intent.py                      4.6 KB
│   ├── rerank.py                             4.1 KB
│   ├── store.py                              10.1 KB
│   ├── structured.py                         6.4 KB
│   ├── token_utils.py                        1021 B
│   └── vectorstore.py                        8.1 KB
├── desktop                                   23 files
│   ├── tests                                 22 files
│   │   ├── fixtures                          7 files
│   │   │   └── structured                    7 files
│   │   │       ├── invalid_shape_syntax.mmd  72 B
│   │   │       ├── invalid_table.md          77 B
│   │   │       ├── invalid_unbalanced.mmd    97 B
│   │   │       ├── valid_flowchart_lr.mmd    138 B
│   │   │       ├── valid_flowchart_td.mmd    195 B
│   │   │       ├── valid_sequence.mmd        222 B
│   │   │       └── valid_table.md            180 B
│   │   ├── conftest.py                       2.4 KB
│   │   ├── test_charts_tables.py             6.6 KB
│   │   ├── test_chat_history.py              9.9 KB
│   │   ├── test_chat_report_export.py        11.0 KB
│   │   ├── test_hardware_offload.py          14.9 KB
│   │   ├── test_integration.py               20.1 KB
│   │   ├── test_llama_engine_chat.py         2.9 KB
│   │   ├── test_rag_upgrade.py               9.6 KB
│   │   ├── test_report_progress.py           8.6 KB
│   │   ├── test_rerank_retry.py              3.6 KB
│   │   ├── test_retrieval_device.py          7.9 KB
│   │   ├── test_stress.py                    17.3 KB
│   │   ├── test_structured.py                8.6 KB
│   │   ├── test_tiers_roles.py               16.1 KB
│   │   └── test_ui_smoke.py                  4.2 KB
│   └── app.py                                7.6 KB
├── docs                                      2 files
│   ├── project_tree.md                       11.2 KB
│   └── troubleshooting_log.md                35.9 KB
├── engine                                    5 files
│   ├── __init__.py                           2.6 KB
│   ├── hardware.py                           7.1 KB
│   ├── llama_engine.py                       15.3 KB
│   ├── model_manager.py                      9.0 KB
│   └── stub_backend.py                       4.0 KB
├── server                                    8 files
│   ├── static                                5 files
│   │   ├── vendor                            2 files
│   │   │   ├── markdown-it.min.js            122.3 KB
│   │   │   └── mermaid.min.js                3.4 MB
│   │   ├── index.html                        48.7 KB
│   │   ├── logo.ico                          42.5 KB
│   │   └── logo.png                          714.7 KB
│   ├── __init__.py                           0 B
│   ├── app.py                                7.8 KB
│   └── orchestration.py                      13.1 KB
├── training                                  11 files
│   ├── sample-lab-docs                       4 files
│   │   ├── firewall-config.txt               2.5 KB
│   │   ├── iam-policy.json                   2.1 KB
│   │   ├── large-policy-document.txt         13.9 KB
│   │   └── vulnerable-app.py                 4.1 KB
│   ├── 00-workshop-guide.md                  21.9 KB
│   ├── 01-lab-exercises.md                   11.4 KB
│   ├── 02-cheat-sheet.md                     7.2 KB
│   ├── 03-syllabus.md                        12.0 KB
│   ├── 04-desktop-guide.md                   4.0 KB
│   ├── 05-setup-guide.md                     13.4 KB
│   └── README.md                             2.0 KB
├── .env.example                              604 B
├── .gitattributes                            52 B
├── .gitignore                                203 B
├── README.md                                 6.9 KB
├── requirements-dev.txt                      36 B
├── requirements-lock.txt                     3.0 KB
├── requirements.txt                          999 B
├── setup.ps1                                 14.7 KB
└── setup.sh                                  6.3 KB
```

## Per-directory summary

| Path | Files | Python files | Python lines | Size |
|---|---:|---:|---:|---:|
| `.env.example` | 1 | 0 | 0 | 604 B |
| `.gitattributes` | 1 | 0 | 0 | 52 B |
| `.gitignore` | 1 | 0 | 0 | 203 B |
| `core/` | 40 | 28 | 3,447 | 266.9 KB |
| `desktop/` | 23 | 16 | 4,071 | 152.1 KB |
| `docs/` | 2 | 0 | 0 | 47.0 KB |
| `engine/` | 5 | 5 | 1,109 | 38.0 KB |
| `README.md` | 1 | 0 | 0 | 6.9 KB |
| `requirements-dev.txt` | 1 | 0 | 0 | 36 B |
| `requirements-lock.txt` | 1 | 0 | 0 | 3.0 KB |
| `requirements.txt` | 1 | 0 | 0 | 999 B |
| `server/` | 8 | 3 | 545 | 4.3 MB |
| `setup.ps1` | 1 | 0 | 0 | 14.7 KB |
| `setup.sh` | 1 | 0 | 0 | 6.3 KB |
| `training/` | 11 | 1 | 131 | 94.6 KB |

_Regenerate after structural changes; the walk skips version-control, virtual-environment and cache directories, so the listing reflects source, data and assets only._
