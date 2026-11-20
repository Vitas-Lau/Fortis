# Fortis Deployment & Troubleshooting Log

- Platform: Windows 11 (10.0.26300), PowerShell 5.1.26100.9711
- Last verified: 2026-11-20

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

## Current environment state (verified 2026-11-20)

| Interpreter | `py` tag | `sysconfig.is_python_build()` |
|---|---|---|
| 3.14.10 | `-V:3.14 *` (default) | False |
| 3.13.17 | `-V:3.13` | False |
| 3.12.10 | `-V:3.12` | False |

`py --list` and `py -0p` both exit 0. Launcher options need a double dash: `py --list` (or `py -0p` / `py --list-paths`); the single-dash `py -list` is forwarded to the default interpreter and fails there with `Unknown option: -l`.

Project environment: `.venv` (3.13.17) holds the full requirements set — 136 packages, including fastapi 0.165.0, chromadb 1.5.9, torch 2.15.0, sentence-transformers 6.3.0, pymupdf 2.1, pywebview 6.2.1 and pytest 9.2.0. `llama_cpp_python` is still absent, so the engine-install step of `setup.ps1` is the next thing to run.

