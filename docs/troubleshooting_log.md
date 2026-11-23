# Fortis Deployment & Troubleshooting Log

- Platform: Windows 11 (10.0.26300), PowerShell 5.1.26100.9711
- Last verified: 2026-11-22

## BUG-01: Python Version Inconsistency & Lack of Prebuilt Wheels (setup.ps1)

- **Issue / Symptoms**:
  - The local default environment had Python 3.14 installed.
  - The original regular expression on line 21 of `setup.ps1` accepted `Python 3.(1[0-4]|[0-9])`, allowing Python 3.14 to pass the initial version check.
  - During the subsequent installation of `llama-cpp-python` and `chromadb`, the process aborted and triggered a local C++ compilation failure due to the lack of prebuilt Windows binary wheels for Python 3.14.
  - The prompt message on line 125 of the script stated that Python 3.13 has prebuilt wheels, which contradicted the overly permissive regex check at the beginning and proved misleading.
  - **Scope note (added 2026-11-20)**: this entry covers the wheel/version problem only. The `py -3.13 -m venv .venv` failure seen in the same session had a different, unrelated root cause — see **BUG-02**. Pinning the Python version alone does not fix it.

- **Root Cause**:
  - `setup.ps1` did not strictly cap the maximum compatible Python version and directly invoked the system default `python`/`py` commands, causing the unsupported higher version (3.14) to be used inadvertently.

- **Resolution**:
  - **Empirical Verification**: Tested `llama-cpp-python` and `chromadb` via `pip install --dry-run`, confirming that both Python 3.13 and 3.12 have full prebuilt wheel support (eliminating the need for local MSVC compilation).
  - **Version Pinning**: Officially pinned the project environment to **Python 3.13**.
  - **Script Modification**: Updated `setup.ps1` to cap the regular expression at `Python 3.(1[0-3])`, adjusted the command detection priority to invoke `py -3.13` / `py -3.12` first, and removed the invalid references to 3.14 from the prompt messages.
  - **Correction (2026-11-20)**: the original text of this entry claimed the environment was "recreated using `py -3.13 -m venv .venv`, completing the installation and offline self-tests without issue". That was not true at the time — venv creation was failing for the reason documented in BUG-02 and only succeeded after the interpreter installations were rebuilt.
  - **Status**: the version cap and the `py -3.13` / `py -3.12` preference are verified present in `setup.ps1`. The wheel-availability claim came from the earlier `pip install --dry-run` runs and was not re-tested in this pass.
  - **Superseded by R1 (2026-11-22)**: the window is now **3.11 - 3.14** (3.13 preferred). The 3.14 exclusion and the `(1[0-3])` cap described above no longer apply — the llama.cpp indexes now serve `py3-none` wheels that install and import on 3.14, while 3.10 has no matplotlib/torch `cp310` wheels. See **R1** for the measurements.

## BUG-02: `py -3.13 -m venv .venv` fails with "Unable to copy ... venvlauncher.exe" — WinError 2

- **Issue / Symptoms**:
  - `py -3.13 -m venv .venv` (and `py -3.12 -m venv .venv`) aborted with:

    ```
    Unable to copy 'C:\Users\lauyu\AppData\Local\Programs\Python\Python313\venvlauncher.exe' to '<project>\.venv\Scripts\python.exe'
    Unable to copy 'C:\Users\lauyu\AppData\Local\Programs\Python\Python313\venvwlauncher.exe' to '<project>\.venv\Scripts\pythonw.exe'
    Error: [WinError 2] 系統找不到指定的檔案。
    ```

  - A half-built `.venv` was left behind (`Include\`, `Lib\`, `pyvenv.cfg`, `activate.*` present — no `python.exe`).
  - The failure was reproduced identically in `%LOCALAPPDATA%\Temp`, ruling out the target path, the Synology Drive folder and free disk space. Python 3.14 on the same machine was unaffected, ruling out a machine-wide or endpoint-security cause.

- **Root Cause**:
  - Both interpreter directories (`...\Programs\Python\Python313` and `...\Python312`) had a **full CPython source tree unpacked into them** on top of the official installation: `Grammar\`, `Android\`, `iOS\`, `PCbuild\`, `Objects\`, `Parser\`, `Python\`, `Modules\`, `configure`, `Makefile.pre.in`, `.devcontainer\`, etc. The source-tree files were dated 2026-10-26 against installer files of 2026-10-21, and no `configure` output existed (`Makefile`, `pyconfig.h` absent), so nothing had been built there — a source tarball was simply extracted on top of the installation.
  - Provenance: `Downloads\Python-3.13.18.tar.xz` / `.tgz` and `Python-3.12.16.tar.xz`; the sdist contains `Python-3.13.18/Modules/Setup`, while its `Lib/venv/scripts/nt/` holds only `activate.bat` / `deactivate.bat` (no launchers).
  - `sysconfig.is_python_build()` returns `True` purely because `Modules\Setup` (or `Modules\Setup.local`) exists under the interpreter base directory (`Lib\sysconfig\__init__.py`).
  - `venv` branches on that flag (`Lib\venv\__init__.py`, `setup_python`): when it is `True` the launcher is looked up in the interpreter root, otherwise in `<base>\Lib\venv\scripts\nt\`. A python.org installation only ships `venvlauncher.exe` / `venvwlauncher.exe` under `Lib\venv\scripts\nt\`, so the copy **source** did not exist → `FileNotFoundError [WinError 2]`.
  - The copy failure is only logged as a warning; venv then runs `_setup_pip` → `.venv\Scripts\python.exe -m ensurepip`, which cannot be launched → `Error: [WinError 2]`, exit code 1 (`Lib\venv\__main__.py`).

- **Diagnosis commands**:
  - `<base>\python.exe -c "import sysconfig; print(sysconfig.is_python_build())"` → `True` for 3.12 and 3.13, `False` for 3.14.
  - `dir <base>\venvlauncher.exe` → not found; `dir <base>\Lib\venv\scripts\nt\venvlauncher.exe` → present (255,200 bytes).

- **Resolution**:
  - **Interim**: renamed `<base>\Modules` → `<base>\_src_junk_Modules` in both installs, which flips `is_python_build()` to `False` and lets venv use `Lib\venv\scripts\nt\`. Verified: `py -3.13 -m venv` and `py -3.12 -m venv` both exit 0, `pip --version` works, `import ssl, sqlite3` works.
  - **Permanent**: the contaminated interpreter directories were removed and Python 3.13.17 and 3.12.10 reinstalled from the official installers. Neither base interpreter held third-party packages (only `pip`), so nothing was lost. Verified afterwards: 3.12.10 / 3.13.17 / 3.14.10, `is_python_build()` = `False` for all three.
  - **Rule**: never extract a CPython source tarball into an interpreter installation directory — the stdlib then treats the installation as a build tree.

## BUG-03: `setup.ps1` aborts at the Python check — "無法辨識 'py -3.13' 詞彙"

- **Issue / Symptoms**:

  ```
  & : 無法辨識 'py -3.13' 詞彙是否為 Cmdlet、函數、指令檔或可執行程式的名稱。
  位於 ...\setup.ps1:43 字元:24
  + Write-Host "  [ok] $(& $py --version)"
  ```

  `FullyQualifiedErrorId : CommandNotFoundException`, raised before any dependency was installed.

- **Root Cause**:
  - The BUG-01 patch stored `$py = "py -3.13"` — one string holding both the command **and** its argument.
  - PowerShell's call operator `&` accepts exactly **one** command token and never splits a string on spaces, so `& $py --version` resolved a command literally named `py -3.13` instead of `py` with argument `-3.13`.
  - `$ErrorActionPreference = "Stop"` (line 3) promoted the non-terminating `CommandNotFoundException` into a terminating error, so the script died at the version banner. The same defect existed at the venv creation call (`& $py -m venv .venv`) and in the llama-cpp failure message.
  - This was a regression introduced by the BUG-01 patch itself, not an environment problem: running `py -3.13 -m venv .venv` by hand in the same shell succeeded.

- **Resolution**:
  - The command and its arguments are now stored separately, and the argument array is splatted at every call site:

    ```powershell
    $pyExe = $null
    $pyArgs = @()                                   # fallback branch keeps @()
    ...
    if ($v -match "Python 3\.(1[2-3])\.") { $pyExe = "py"; $pyArgs = @($ver); break }
    ...
    Write-Host "  [ok] $(& $pyExe @pyArgs --version)"
    & $pyExe @pyArgs -m venv .venv
    ```

  - All three call sites updated (version banner, venv creation, wheel-failure message); nothing else in the script was changed.

- **Verification (2026-11-20)**:
  - `[System.Management.Automation.Language.Parser]::ParseFile(...)` → `PARSE OK`; `grep` for a bare `$py` reference → none left.
  - Lines 1–57 of the script executed in the project directory → `[ok] Python 3.13.17` / `[ok] Virtual environment exists`, exit 0.
  - The same block executed in an empty temp directory (venv-creation branch) → created a `.venv` whose interpreter reports `3.13.17`, exit 0.
  - Splat forms exercised directly: `$pyArgs = @()` → `Python 3.11.16` with no error; `$pyArgs = @("-3.13")` → `Python 3.13.17`.

## BUG-04: `setup.ps1` dies at the llama.cpp step — `NativeCommandError` from a pip `WARNING`

- **Issue / Symptoms**:
  - After the dependency install completed (`[ok] Dependencies installed`), the script printed `[..] Installing the local AI engine (llama.cpp, ~200 MB)...` and then aborted with:

    ```
    python.exe : WARNING: Skipping llama_cpp_python as it is not installed.
    位於 ...\setup.ps1:91 字元:9
    +  & $venvPy -m pip uninstall --disable-pip-version-check --yes ...
        + CategoryInfo          : NotSpecified: (WARNING: Skippi... not installed.:String) [], RemoteException
        + FullyQualifiedErrorId : NativeCommandError
    ```

  - Python and pip were healthy: `pip uninstall` exits 0 and only reports that the package is absent — nothing to clean up, nothing broken.

- **Root Cause**:
  - Windows PowerShell 5.1 materialises a native program's stderr as `ErrorRecord`s **when stderr is redirected** (`2>$null`, `2>&1`). Unredirected stderr is passed straight to the console and never becomes a PowerShell error. With `$ErrorActionPreference = "Stop"` at the top of the script those records are terminating, so the statement throws `RemoteException` / `NativeCommandError` and the script stops.
  - The offending statement was the first-run clean-up `& $venvPy -m pip uninstall ... 2>$null | Out-Null`. In a fresh environment the guard `Test-Path "$venvPy"` is true but the package does not exist, so pip writes `WARNING: Skipping llama_cpp_python as it is not installed.` to stderr while still exiting 0.
  - Proof that the **redirect**, not stderr itself, is the trigger: step 3 runs `pip install -r requirements-dev.txt` **without** redirection and emitted many `WARNING: Cache entry deserialization failed, entry ignored` lines on stderr, yet completed with `[ok] Dependencies installed`.
  - Controlled matrix (PowerShell 5.1.26100.9711, `$ErrorActionPreference = "Stop"`, command writes one line to stderr and exits 0):

    | form | outcome |
    |---|---|
    | `cmd \| Out-Null` (no redirect) | survived, stderr shown on console |
    | `cmd 2>$null \| Out-Null` | **threw `System.Management.Automation.RemoteException`** |
    | `Invoke-Native { cmd 2>$null \| Out-Null }` | survived, silent |

- **Resolution**:
  - Wrapped all three `pip uninstall ... 2>$null` clean-ups (initial clean-up, CUDA→CPU fallback, CPU→PyPI fallback) in the script's existing `Invoke-Native` helper, which sets `$ErrorActionPreference = "Continue"` for the duration of the call; the `2>$null` then only suppresses output.
  - `$LASTEXITCODE = 0` after each call is kept, so a genuine failure is still handled by the surrounding logic rather than masked.
  - The two remaining bare `2>$null` calls (`py -3.13 --version` probing) are intentionally left alone: they sit inside `try { } catch { }`, so a `NativeCommandError` there is already handled by design.

- **Verification (2026-11-20)**:
  - `[System.Management.Automation.Language.Parser]::ParseFile(...)` → `PARSE OK`.
  - Harness run in the project directory with the real `.venv` interpreter and the real package name (`pip show llama_cpp_python` → not found):
    - control (old bare form) → `THREW -> System.Management.Automation.RemoteException`, reproducing the reported failure exactly;
    - patched form (`Invoke-Native { ... }`) → `survived, exit=0`.
  - The full script was **not** re-run in this pass: the next step downloads the ~200 MB llama.cpp wheel and the script ends by launching the GUI app (and blocks on `Read-Host` if no wheel matches). Verification was scoped to the failing block.

## BUG-05: self-test fails on the report tests — the stub-data gate, not a missing model

- **Issue / Symptoms**:
  - `setup.ps1` step 5 ended with `10 failed, 229 passed`. All ten failures were report paths: `test_chat_history::test_report_turns_are_persisted_too`; the four `test_chat_report_export::test_stream_report_request_writes_file_to_report_dir[...]` cases; `...::test_stream_report_request_format_selection`; `...::test_chat_report_request_writes_file`; `...::test_report_slash_command_generates_and_downloads`; `...::test_stream_report_without_documents_is_friendly_error`; `test_integration::test_report_generation_invalid_format`; `...::test_report_generation_engagement_not_found`.
  - Recovered failure text (only readable once the full pytest output was captured):

    ```
    assert "Security report generated" in '⚠️ No model is loaded, so this report would contain
    placeholder (stub) data and must not be delivered to a client. ...'
    assert resp.status_code == 200        # got 409
    assert resp.status_code == 400        # got 200, or 409 instead of 404
    ```

- **Rejected hypothesis**: the self-test was assumed to need `FORTIS_LLM_BACKEND=stub` because the ~3.4 GB model had not been downloaded. Adding that variable does not fix anything — it reproduces the ten failures **deterministically** (measured: `10 failed, 229 passed`).

- **Root Cause**:
  - `core/report_intent.py::is_stub_backend()` returns True whenever the engine reports `stub`, and every report entry point then refuses to export (`chat_stub_gate_reply`, HTTP 409 from `/report/generate`, a chat reply instead of a file).
  - `engine._resolve()` follows `FORTIS_LLM_BACKEND` (default `auto`), and `auto` resolves to `llama` **only when a model file is selected** — `%APPDATA%\Fortis\models\active_model.json`, or a `.gguf` in the models dir.
  - Therefore the suite's result depended on ambient machine state: no selection → stub → the ten report tests fail; any selection → llama → 239 pass. That is why the same command alternated between red and green.
  - The selection came from the suite itself: `desktop/tests/test_tiers_roles.py` calls `engine.model_manager.set_selection()` with a throwaway temp GGUF, which writes the **real** profile state file. The first run of a session (no state file yet) failed; every later run inherited a selection and passed.
  - Not a packaging or model-download problem: `llama_cpp` 0.3.36 was installed at 14:18:57, immediately before the failing 14:19 run, and the suite still passes 239/239 with `llama_cpp` made unimportable — the tests monkeypatch `core.llm.chat`, so no weights are ever loaded.

- **Resolution**:
  - `desktop/tests/conftest.py` now pins the environment before the app is imported: `FORTIS_LLM_BACKEND=llama` (mocked chat; the report gate must stay open) and `FORTIS_MODELS_DIR` into the per-run temp directory (tests can no longer overwrite the user's real `active_model.json`).
  - `setup.ps1` keeps only `EMBEDDING_BACKEND=hash-stub`; the backend is owned by conftest, so the self-test no longer depends on the shell environment or on whether a model is downloaded.
  - `setup.ps1` now writes the **complete** pytest output to `.pytest-self-test.log` (UTF-8, git-ignored) and shows the last 20 lines — the truncated tail is what made this failure unreadable the first time.
  - The stale `%APPDATA%\Fortis\models\active_model.json` left behind by earlier runs (pointing at `%TEMP%\fortis-test-select-*\...gguf`) was deleted; while it existed, `engine.chat()` raised `ValueError: Failed to load model from file: ...` for the live app.

- **Verification (2026-11-20)**:

  | condition | result |
  |---|---|
  | pytest run the way the script does it (PowerShell, `EMBEDDING_BACKEND=hash-stub`) | 239 passed, exit 0, log written |
  | `FORTIS_LLM_BACKEND=stub` in the shell (the rejected patch) | 239 passed |
  | `llama_cpp` made unimportable | 239 passed |
  | `active_model.json` deleted (no model selected) | 239 passed |

  - After all four runs `%APPDATA%\Fortis\models\active_model.json` is **absent**: the suite no longer touches the real profile.
  - `Parser::ParseFile(setup.ps1)` → `PARSE OK`.

## Review pass 2026-11-22 — four reported defects

Second review of the same scripts, four reports. Two reproduce as described,
one reproduces with a different root cause than reported, and one does not
reproduce at all.

Three numbering conventions collide, so each row names all of them: the
**audit list** numbers these four items **BUG-04 - BUG-07**, the **review
request** that was handed over renumbered them **BUG-01 - BUG-04**, and the
sections below use **R1 - R4** because this document already spends
BUG-01..BUG-05 on earlier entries. Cite the audit ID when cross-referencing:
`BUG-06` and `BUG-07` exist only under that numbering. In the sections below,
a bare BUG-0x reference means the entry above; audit IDs are always written
as `(audit BUG-0x)`.

| Report | Audit list | Filed in the review request | Outcome |
|---|---|---|---|
| R1 | BUG-04 | BUG-01 — Python version window vs. wheels | Already fixed in 876a6c9; residual defects found and fixed |
| R2 | BUG-05 | BUG-02 — marker written unconditionally on second run | **Reproduced on this machine**; fixed in both scripts |
| R3 | BUG-06 | BUG-03 — pip calls abort the script | Abort not reproducible; different real defect found and fixed |
| R4 | BUG-07 | BUG-04 — `matplotlib>=3.11.2` breaks resolution | Not reproducible; the real constraint is the Python floor |

### R1 (audit BUG-04): the version window was wrong in both directions, and the stated reason was stale

- **What was reported**: "line 21's regex accepts 3.0-3.14 while line 125 says
  only 3.13 has a wheel".
- **State of the code**: line 21 is `$pyArgs = @()`; the regexes are
  `Python 3\.(1[2-3])\.` (launcher probe) and `Python 3\.(1[0-3])\.` (generic
  fallback). The permissive-regex defect was fixed in 876a6c9, so that part of
  the report is reading a pre-fix version of the file.
- **Residual defect 1 (floor too low)**: the window admitted 3.10, but
  `requirements.txt` pins `matplotlib>=3.11.2`, whose PyPI release publishes
  cp311 / cp312 / cp313 / cp314 / cp315 win_amd64 wheels and **no cp310**
  (53 files in the release). torch 2.15.0 has the same cp311+ floor. On 3.10
  the dependency step therefore drops to a source build and fails at
  "check internet connection", which is a misleading message for a wheel
  problem.
- **Residual defect 2 (ceiling too low, reason false)**: the script claimed
  "3.14+ is incompatible due to missing prebuilt wheels". That is no longer
  true. `llama-cpp-python`'s indexes serve `py3-none-win_amd64` wheels with no
  `data-requires-python` constraint: cpu up to 0.3.36, cu124 up to 0.3.37.
  Measured on this machine:

  ```
  py -3.14 -m venv v314
  v314\Scripts\pip install --only-binary :all: llama-cpp-python \
      --extra-index-url https://abetlen.github.io/llama-cpp-python/whl/cpu
  -> llama_cpp 0.3.36 imports (gpu_offload False)

  v314\Scripts\pip install --dry-run --only-binary :all: --report r.json -r requirements-dev.txt
  -> exit 0, 128 packages resolved (matplotlib 3.11.2, torch 2.15.0, chromadb 1.5.9, pymupdf 2.1)
  ```
- **Caveat that the earlier entry also got wrong**: 3.13 is *not* the only
  version with wheels, and it is *not* CUDA-less. The cu124 index carries a
  `llama_cpp_python-0.3.37-py3-none-win_amd64.whl` that pip installs on 3.13;
  the wheel simply links against the CUDA 12 runtime that it does not bundle
  (see R3). CUDA availability is a runtime question, not a Python-version one.
- **Fix**:
  - `setup.ps1`: window 3.11-3.14, preference order `3.13, 3.12, 3.14, 3.11`,
    with the floor and the py3-none wheel facts recorded in the comment.
  - `setup.sh`: window 3.11-3.14; rejects anything else up front and prints the
    interpreters it actually found (`python3=X.Y ...`), instead of accepting
    any "Python 3" and failing later inside pip.
  - Both messages now name the reason for each bound instead of asserting a
    wheel absence that has stopped being true.
- **Verification**: `[Parser]::ParseFile(setup.ps1)` → `PARSE OK`;
  `bash -n setup.sh` → ok; a full run of the script prints `[ok] Python
  3.13.17` (3.13 is now chosen first).

### R2 (audit BUG-05): `.llama-cuda-ok` was written for any installed engine (confirmed live)

- **What was reported**: the "engine already present" branch writes the marker
  unconditionally, so a CPU wheel is recorded as CUDA-capable.
- **Reproduction on this machine** (RTX 4080, 16.0 GB VRAM, `llama_cpp` 0.3.36
  installed, `llama_supports_gpu_offload()` → `False`):

  ```
  llama import exit code: 0
    [ok] AI engine present
  marker created: True
  ```
  and then, with the marker in place:

  ```
  detected vram: 16.0   has_nvidia: True
  llama_supports_gpu_offload(): False
  can_use_cuda(): True            # engine/hardware.py: vram > 0 and marker exists
  _auto_gpu_layers(): 999         # offload requested from a CPU-only build
  ```
  The consequence is not a crash: a CPU build ignores `n_gpu_layers`, so the
  model silently runs on CPU while the UI, `/api/model/status`
  (`backend_wheel: "cuda"`) and the VRAM-based partial-offload arithmetic all
  assume layers live on the GPU.
- **Second, unreported path with the same outcome**: `setup.sh` gated the
  marker on `ctypes.CDLL('libcuda.so.1')`, which succeeds whenever the NVIDIA
  driver is installed - CPU wheel included. So the install branch could write
  the marker on Linux for the same wrong reason.
- **Root cause**: the marker was written by *presence* tests (`import
  llama_cpp`, `[ -f "$MARKER" ]`, `CDLL`), never by a *capability* test.
- **Fix**: one authoritative block after the install logic in both scripts,
  driven by `llama_cpp.llama_supports_gpu_offload()` plus an `nvidia-smi`
  check. The marker is created only when both hold, and a stale marker is
  deleted when they do not. The import probe no longer decides anything about
  CUDA.
- **Verification** (`setup.ps1` block extracted verbatim, run against a fake
  interpreter; `setup.sh` block extracted verbatim, run under
  `set -euo pipefail`):

  | case | expected | result |
  |---|---|---|
  | GPU + CUDA wheel | marker written | marker = YES |
  | GPU + CPU wheel | no marker, warning | marker = NO, warning printed |
  | GPU + CPU wheel, stale marker present | marker removed | marker = NO |
  | no nvidia-smi + CUDA wheel | no marker | marker = NO |
  | no nvidia-smi + stale marker | marker removed | marker = NO |
  | full `setup.ps1` run on this machine | no marker | marker = NO |

### R3 (audit BUG-06): pip exit codes were ignored; the import probe cannot stand in for them

- **What was reported**: `pip install` / `pip uninstall` are not wrapped in
  `Invoke-Native`, so stderr from a failing CUDA wheel aborts the script.
- **Not reproducible as stated**: all three `pip uninstall ... 2>$null` calls
  were already wrapped in 876a6c9/e907f96. The install calls have no
  redirection at all, and the `NativeCommandError` matrix in the BUG-04 entry
  above showed that *unredirected* stderr does
  not become an `ErrorRecord`, so `$ErrorActionPreference = "Stop"` does not
  fire. A failing pip install continues to the fallback.
- **The real defect**: the install exit codes were never inspected - `$ok` was
  set from `import llama_cpp`. That probe is satisfied by any importable
  wheel, including one left behind by a failed uninstall, so the CUDA branch
  could report success after pip had failed. It also depends on the *next*
  person not adding `2>&1` to those calls, which would immediately restore the
  `NativeCommandError` abort described in BUG-04 above.
- **Fix**: every `pip install` is wrapped in `Invoke-Native`, its
  `$LASTEXITCODE` is captured (`$cudaExit`, `$cpuExit`) and used together with
  the import probe. Failures now print the exit code; the source-build attempt
  checks its own exit code too.
- **What the CUDA path actually does on this machine** (fresh 3.13 venv, no
  MSVC, no CUDA toolkit):

  ```
  [..] Installing the local AI engine (llama.cpp, ~200 MB)...
       NVIDIA GPU detected - trying the CUDA wheel first...
       The CUDA wheel installed but llama.dll will not load - cleaning up.
       Cause: the CUDA 12 runtime (cublas64_12.dll / cudart64_12.dll) is not on PATH.
       An NVIDIA driver is not enough - install the CUDA 12 runtime, or keep the CPU wheel.
       Trying the prebuilt CPU wheel (llama.cpp CPU index)...
  [ok] Engine installed (CPU)
       NVIDIA GPU present, but this wheel is CPU-only - the app will run on CPU.
  FINAL: marker=False
  ```
  The wheel that triggered it: `llama_cpp_python-0.3.37-py3-none-win_amd64.whl`
  from the cu124 index (`ggml-cuda.dll` present, no `cudart`/`cublas`
  bundled), failing with `RuntimeError: Failed to load shared library
  ...\llama.dll: Could not find module ... or one of its dependencies`.
  `Test-CudaRuntime` now distinguishes this case from "no wheel for this
  version", which is what the message used to imply.

### R4 (audit BUG-07): `matplotlib>=3.11.2` is the current release, not an impossible pin

- **What was reported**: the floor is "extremely high" and unpinned packages
  cause resolution failures.
- **Measured**: 3.11.2 *is* the latest matplotlib; the release ships
  cp311-cp315 wheels. On the tested interpreter the full set resolves:

  ```
  .venv\Scripts\pip install --dry-run --ignore-installed --only-binary :all: -r requirements-dev.txt
  -> exit 0, 132 packages, matplotlib 3.11.2 / torch 2.15.0 / chromadb 1.5.9 / pymupdf 2.1
  ```
  No dependency conflict, no downgrade, nothing built from source.
- **Where resolution does break**: Python 3.10, for the reason in R1 (no cp310
  wheels). That is a Python-window bug, not a matplotlib-pin bug, and lowering
  the pin would have masked it.
- **Fix**: the floor stays; `requirements.txt` now states the 3.11-3.14 window
  and why the matplotlib pin implies it. Reproductibility is handled
  separately: `requirements-lock.txt` freezes the 135-package set from the
  verified venv (`pip install --dry-run -r requirements-lock.txt` → exit 0).
  `llama-cpp-python` is excluded there on purpose - PyPI publishes only an
  sdist for it, so a lock entry would send every user into a source build;
  the setup scripts install the prebuilt wheel instead.

### Self-test: pytest was writing into a directory it could not read

- **Symptom**: `pytest desktop/tests/` failed with `22 errors, 217 passed`;
  every error was `ERROR at setup`, and the traceback bottomed out at
  `PermissionError: [WinError 5]` on
  `C:\Users\VITASL\AppData\Local\Temp\pytest-of-VITASL`.
- **Cause**: that directory carries an ACL that denies the current user
  (`icacls` cannot read it either, and `rm -rf` / `Remove-Item` fail; it needs
  elevation to take ownership). pytest could not create its `tmp_path` root,
  so every fixture using it errored before the test body ran - which reads
  like a code failure, not an environment one.
- **Fix**: `setup.ps1` and `setup.sh` pass `--basetemp` pointing at
  `.pytest-tmp/` inside the repo (git-ignored), so the self-test does not
  depend on `%TEMP%`. Verified: `239 passed` with `--basetemp`, exit 0.
- **Not fixed**: `.pytest_cache/` in the repo has the same broken ACL and
  pytest now warns `could not create cache path ... [WinError 5]`. The warning
  is non-fatal and the suite still passes; removing the directory requires an
  elevated shell.

### What changed in this pass

| File | Change |
|---|---|
| `setup.ps1` | 3.11-3.14 window (3.13 preferred); `Test-NvidiaGpu` cached probe; `Test-CudaRuntime`; single post-install marker verification with stale-marker removal; all pip calls wrapped with exit-code checks; `--basetemp` for the self-test |
| `setup.sh` | same window with an up-front reject that lists found interpreters; `llama_has_cuda` / `has_nvidia` helpers; single marker verification; `--basetemp` |
| `requirements.txt` | documents the 3.11-3.14 window and the matplotlib floor; points at the lock |
| `requirements-lock.txt` | new: 135 verified pins from the 3.13.17 venv |
| `README.md` | verified-environment table and the CUDA 12 runtime requirement |
| `.gitignore` | `.pytest-tmp/` |

## CUDA 12 installed but never detected — stale process PATH, plus detection hardening (2026-11-23)

- **Issue / Symptoms**:
  - `setup.ps1` reported the CUDA wheel could not load, then fell back to CPU:

    ```
    NVIDIA GPU detected - trying the CUDA wheel first...
    The CUDA wheel installed but llama.dll will not load - cleaning up.
    Cause: the CUDA 12 runtime (cublas64_12.dll / cudart64_12.dll) is not on PATH.
    An NVIDIA driver is not enough - install the CUDA 12 runtime, or keep the CPU wheel.
    ```

  - CUDA 12.0 **was** installed at `C:\Program Files\NVIDIA GPU Computing Toolkit\CUDA\v12.0`, with both DLLs present in its `bin`, `CUDA_PATH` pointing at it, and `...\v12.0\bin` present in the machine PATH. The CUDA wheel failed with `RuntimeError: Failed to load shared library ... llama.dll: Could not find module ... (or one of its dependencies)`.

- **Root Cause**:
  - The PowerShell that ran `setup.ps1` had been started **before** the toolkit finished installing (CUDA 13.5 at 14:05, CUDA 12.0 at 14:11). Windows copies the environment block when a process is created and never updates a running one, so that shell — and every child process, including `setup.ps1` and its `python.exe` — carried the **pre-install** PATH.
  - `Test-CudaRuntime` scans only `$env:PATH`, so it correctly answered "not on PATH" *for that process* while the machine PATH was fine; the follow-up advice ("install the CUDA 12 runtime") was therefore misleading — it was already installed.
  - CUDA 13.5 cannot help the cu124 wheel: it links `cublas64_12.dll`, and 13.x ships `cublas64_13.dll`. Only the 12.x toolkit satisfies it.

- **Resolution**:
  - `engine/llama_engine.py`: new `_add_cuda_dll_dir()` registers `%CUDA_PATH%\bin` (falling back to the newest `CUDA\v12*\bin`) with `os.add_dll_directory()` and pre-loads `cudart64_12`, `cublasLt64_12`, `cublas64_12` by absolute path before `import llama_cpp`; the returned handles are kept for the process lifetime. GPU offload no longer depends on PATH at all.
  - `setup.ps1::Test-CudaRuntime`: **both** `cublas64_12.dll` and `cudart64_12.dll` must resolve in the same directory (previously either one was enough — a half-installed runtime produced a false positive); PATH entries are now `.Trim().Trim('"')`-ed and checked with `Test-Path -LiteralPath -PathType Container`, so quoted, empty, stale or file entries cannot raise under `$ErrorActionPreference = "Stop"`.
  - Comment/message alignment: the cached GPU probe is documented as being called only by the llama.cpp step; the wheel-failure hint now reads `3.11 - 3.14, verified on 3.13`, matching the interpreter check at the top of the script.
  - `setup.sh`: the self-test now tees the complete pytest output to `.pytest-self-test.log` and prints the last 15 lines, matching `setup.ps1` across platforms.
  - Repository hygiene: `docs/fix(setup) lock python version to 3.13 and resolve BUG-01.txt` (37 KB raw console transcript) was removed — its findings live here as BUG-01…BUG-04.

- **Verification (2026-11-23)**:

  | check | result |
  |---|---|
  | `Test-CudaRuntime` with a plain PATH (no CUDA) | `False` |
  | quoted CUDA dir `"C:\...\v12.0\bin"` on PATH | `True` (quotes stripped) |
  | empty / missing / file PATH entries under `EAP = "Stop"` | `False`, no exception |
  | directory containing only `cudart64_12.dll` | `False` (both required) |
  | directory containing both DLLs | `True` |
  | `engine.llama_engine._backend_wheel()` with the stale PATH (no CUDA entry) | `cuda`, `llama_supports_gpu_offload()` → `True` |
  | `Parser::ParseFile(setup.ps1)` / `bash -n setup.sh` | `PARSE OK` / syntax OK |
  | full suite | `239 passed` |

## Dual-mode smoke test (2026-11-23)

Screenshots taken by the project owner, stored in `..\note\雙模式並截圖存證\`:

- `螢幕擷取畫面 2026-11-23 150642.png` — native pywebview window (no browser chrome): desktop mode, empty state, footer reads *"Using built-in stub responses (no model loaded)"*.
- `螢幕擷取畫面 2026-11-23 150735.png` — the same UI in a browser at `127.0.0.1:8757`: server mode serves the identical app.

Headless pipeline proof (stub backend, isolated temp data dir, no model needed):

| step | result |
|---|---|
| `python -m server.app --port 8761` → `GET /health` | 200 |
| `POST /engagements` | `demo-client-campus-network-review` |
| `POST /upload` (markdown policy) | 200, `chunks_indexed: 1` |
| `POST /report/generate` `{format: pdf, allow_stub: true}` | 200, `download_url: /report/download/...pdf` |
| generated file | 45,217 bytes, magic `%PDF-` |
| `GET /report/download/<file>` | 200, 45,217 bytes |

Both screenshots are in the **no-model stub state**: a model-loaded (GGUF) conversation and a PDF exported from the running GUI are not yet captured and remain the outstanding evidence item.

## Current environment state (verified 2026-11-20)

| Interpreter | `py` tag | `sysconfig.is_python_build()` |
|---|---|---|
| 3.14.10 | `-V:3.14 *` (default) | False |
| 3.13.17 | `-V:3.13` | False |
| 3.12.10 | `-V:3.12` | False |

`py --list` and `py -0p` both exit 0. Launcher options need a double dash: `py --list` (or `py -0p` / `py --list-paths`); the single-dash `py -list` is forwarded to the default interpreter and fails there with `Unknown option: -l`.

Project environment: `.venv` (3.13.17) holds the full requirements set — 136 packages, including fastapi 0.165.0, chromadb 1.5.9, torch 2.15.0, sentence-transformers 6.3.0, pymupdf 2.1, pywebview 6.2.1, matplotlib 3.11.2 and pytest 9.2.0 — plus `llama_cpp_python` 0.3.36. The offline self-test runs `239 passed` in ~15 s (since the 2026-11-22 pass: `--basetemp .pytest-tmp`, and the venv's pytest is no longer pointed at `%TEMP%`).

Hardware as re-checked 2026-11-22:

| Item | State |
|---|---|
| GPU | NVIDIA GeForce RTX 4080, 16.0 GB VRAM (`nvidia-smi -L` exit 0) |
| `llama_supports_gpu_offload()` | `False` — the installed 0.3.36 wheel is a CPU build (`ggml-cpu.dll`; no `ggml-cuda.dll`) |
| CUDA 12 runtime | absent: no `cublas64_12.dll` / `cudart64_12.dll` on PATH, no CUDA toolkit directory |
| MSVC / CMake | absent (`vswhere.exe` not found, no `cmake`) — the source build path cannot run here |
| `.llama-cuda-ok` | absent, and now stays absent until a wheel reports GPU offload |

Consequences: `can_use_cuda()` is `False`, `_auto_gpu_layers()` is `0`, `backend_wheel` is `cpu`. That is the correct state for this machine — GPU offload needs the CUDA 12 runtime, not a different Python.

Re-checked 2026-11-23 on this laptop, after the CUDA toolkit install and the cu124 wheel:

| Item | State |
|---|---|
| GPU | NVIDIA GeForce GTX 1660 Ti with Max-Q Design, 6.0 GB VRAM, driver 621.14, compute 7.5 (no tensor cores) |
| CUDA toolkits | v13.5 (installed 14:05) and **v12.0** (14:11); `CUDA_PATH` → `...\NVIDIA GPU Computing Toolkit\CUDA\v12.0` |
| CUDA 12 runtime | `cublas64_12.dll`, `cublasLt64_12.dll`, `cudart64_12.dll` present in `v12.0\bin` |
| Machine PATH | contains `...\v12.0\bin`; a shell started **before** 14:11 does not — the stale-PATH effect documented above |
| Installed wheel | `llama_cpp_python` 0.3.37 from the cu124 index (`ggml-cuda.dll` present) |
| `llama_supports_gpu_offload()` | `True`, with or without CUDA on PATH (`_add_cuda_dll_dir()`), GPU 0 enumerated |
| `.llama-cuda-ok` | present |

The 2026-11-22 table above is kept as recorded (state before the toolkit install, CPU wheel). Its RTX 4080 row does not match this laptop's `nvidia-smi` output (GTX 1660 Ti Max-Q, 6 GB).


