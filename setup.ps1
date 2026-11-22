# Fortis Desktop setup script (Windows)
# Usage:  right-click -> Run with PowerShell, or:  powershell -ExecutionPolicy Bypass -File setup.ps1
$ErrorActionPreference = "Stop"

function Invoke-Native {
    param([scriptblock]$Command)
    $prev = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try { & $Command } finally { $ErrorActionPreference = $prev }
}

# Cached GPU probe: called once for the interpreter choice and again for the
# llama.cpp wheel choice, but nvidia-smi is only invoked the first time.
$script:NvidiaProbed = $false
$script:HasNvidiaValue = $false
function Test-NvidiaGpu {
    if ($script:NvidiaProbed) { return $script:HasNvidiaValue }
    $script:NvidiaProbed = $true
    try {
        $smi = Get-Command nvidia-smi -ErrorAction SilentlyContinue
        if ($smi) {
            Invoke-Native { & nvidia-smi -L 2>$null } | Out-Null
            if ($LASTEXITCODE -eq 0) { $script:HasNvidiaValue = $true }
        }
    } catch {}
    return $script:HasNvidiaValue
}

Write-Host ""
Write-Host "  Fortis - Cybersecurity Advisor : setup" -ForegroundColor Cyan
Write-Host "  ----------------------------------------" -ForegroundColor DarkGray

# 1. Python check.
#    Window: 3.11 - 3.14, verified on 3.13. The bounds are wheel-driven:
#      * 3.10 and older - matplotlib 3.11.x / torch 2.15 publish cp311+ wheels
#        only, so requirements.txt would have to be compiled from source.
#      * 3.11 - 3.14 - the llama.cpp index serves py3-none wheels (0.3.36 /
#        0.3.37 today), so the AI engine installs without a toolchain. The
#        old "3.14 has no wheel" claim no longer holds: `pip install --dry-run`
#        resolves the full requirements set on 3.14.10 (128 packages).
#    Preference order starts at 3.13: that is the interpreter the offline test
#    suite and requirements-lock.txt were verified against.
#    CUDA is independent of the Python version. The cu124 wheels carry no
#    per-version tag; they need the CUDA 12 runtime DLLs on PATH, which an
#    NVIDIA driver alone does not provide.
#    The executable and its launcher args are kept in separate variables:
#    PowerShell's call operator (&) never splits a string on spaces, so a
#    combined "py -3.13" token would be looked up as one command name.
$pyExe = $null
$pyArgs = @()
$pyOrder = @("-3.13", "-3.12", "-3.14", "-3.11")
foreach ($ver in $pyOrder) {
    try {
        $v = & py $ver --version 2>$null
        if ($v -match "Python 3\.(1[1-4])\.") { $pyExe = "py"; $pyArgs = @($ver); break }
    } catch {}
}
# Fallback to the generic launchers when the py launcher has no matching tag
if (-not $pyExe) {
    foreach ($c in @("python", "py")) {
        try {
            $v = & $c --version 2>$null
            if ($v -match "Python 3\.(1[1-4])\.") { $pyExe = $c; $pyArgs = @(); break }
        } catch {}
    }
}
if (-not $pyExe) {
    Write-Host ""
    Write-Host "  [X] Compatible Python (3.11 - 3.14) not found." -ForegroundColor Red
    Write-Host "      Python 3.10 or older: matplotlib 3.11 / torch 2.15 have no cp310 wheels." -ForegroundColor Yellow
    Write-Host "      Please install Python 3.13 from https://www.python.org/downloads/" -ForegroundColor Yellow
    Write-Host "      (3.13 is the version this project is tested on; 3.11-3.14 all install.)" -ForegroundColor Yellow
    Write-Host "      IMPORTANT: tick 'Add python.exe to PATH' in the installer." -ForegroundColor Yellow
    Read-Host "  Press Enter to exit"
    exit 1
}
Write-Host "  [ok] $(& $pyExe @pyArgs --version)"

# 2. venv
Set-Location $PSScriptRoot
if (-not (Test-Path ".venv\Scripts\python.exe")) {
    Write-Host "  [..] Creating virtual environment (one-time)..."
    & $pyExe @pyArgs -m venv .venv
    if ($LASTEXITCODE -ne 0) { Write-Host "  [X] venv creation failed" -ForegroundColor Red; Read-Host "  Press Enter to exit"; exit 1 }
} else {
    Write-Host "  [ok] Virtual environment exists"
}
$venvPy = ".\.venv\Scripts\python.exe"

# 3. dependencies (skipped when already present)
$probe = Invoke-Native { & $venvPy -c "import fastapi, chromadb, fitz, docx, pptx, openpyxl, reportlab, huggingface_hub, uvicorn, multipart, rank_bm25, matplotlib, webview, pytest" 2>$null }
if ($LASTEXITCODE -ne 0) {
    Write-Host "  [..] Installing dependencies (first run only, ~5 min)..."
    Invoke-Native { & $venvPy -m pip install --disable-pip-version-check -r requirements-dev.txt }
    if ($LASTEXITCODE -ne 0) { Write-Host "  [X] Dependency install failed - check internet connection and the Python version" -ForegroundColor Red; Read-Host "  Press Enter to exit"; exit 1 }
    Write-Host "  [ok] Dependencies installed"
} else {
    Write-Host "  [ok] Dependencies present"
}

# 4. llama-cpp-python: prebuilt CUDA wheel if GPU, else prebuilt CPU wheel
#    A source build is a last resort and runs only when an MSVC toolchain is
#    actually present (CMake + nmake are still required on top of it).
$marker = Join-Path $PSScriptRoot ".llama-cuda-ok"
$hasNvidia = Test-NvidiaGpu

function Test-LlamaImport {
    Invoke-Native { & $venvPy -c "import llama_cpp" 2>$null } | Out-Null
    return ($LASTEXITCODE -eq 0)
}

function Test-LlamaCuda {
    # A CUDA claim needs three things at once: a visible driver, an importable
    # llama_cpp, and a wheel compiled with GPU offload. `import llama_cpp` is
    # NOT sufficient - the CPU wheel imports happily on a GPU machine, so an
    # import-only probe records CUDA capability the engine cannot deliver.
    if (-not $hasNvidia) { return $false }
    Invoke-Native { & $venvPy -c "import llama_cpp, sys; sys.exit(0 if getattr(llama_cpp, 'llama_supports_gpu_offload', lambda: False)() else 1)" 2>$null } | Out-Null
    return ($LASTEXITCODE -eq 0)
}

function Test-CudaRuntime {
    # The cu124 wheels link against the CUDA 12 runtime but do not bundle it
    # (only ggml-cuda.dll ships in the wheel). When cublas64_12.dll /
    # cudart64_12.dll are absent, llama.dll fails to load and the import error
    # says "Could not find module ... or one of its dependencies", which reads
    # like a broken wheel rather than a missing runtime.
    foreach ($dir in ($env:PATH -split ';')) {
        if (-not $dir) { continue }
        foreach ($dll in @("cublas64_12.dll", "cudart64_12.dll")) {
            if (Test-Path (Join-Path $dir $dll)) { return $true }
        }
    }
    return $false
}

if (-not (Test-LlamaImport)) {
    Write-Host "  [..] Installing the local AI engine (llama.cpp, ~200 MB)..."

    if (Test-Path $venvPy) {
        # NB: a bare `2>$null` on a native command turns pip's stderr warning
        # ("Skipping llama_cpp_python as it is not installed.") into a
        # NativeCommandError, which $ErrorActionPreference = "Stop" makes fatal.
        # Invoke-Native relaxes the preference for the duration of the call.
        Invoke-Native { & $venvPy -m pip uninstall --disable-pip-version-check --yes llama_cpp_python 2>$null | Out-Null }
        $LASTEXITCODE = 0
    }

    $installed = $false
    if ($hasNvidia) {
        Write-Host "       NVIDIA GPU detected - trying the CUDA wheel first..."
        Invoke-Native { & $venvPy -m pip install --disable-pip-version-check --quiet --only-binary :all: llama-cpp-python --extra-index-url https://abetlen.github.io/llama-cpp-python/whl/cu124 }
        $cudaExit = $LASTEXITCODE
        # Both checks matter: the exit code says pip did something, the import
        # says the package is actually usable. The import probe cannot be
        # trusted on its own either, because a leftover wheel still imports.
        if ($cudaExit -eq 0 -and (Test-LlamaImport)) {
            $installed = $true
            # Don't claim CUDA from the index name: ask the wheel.
            if (Test-LlamaCuda) {
                Write-Host "  [ok] Engine installed (CUDA wheel, GPU offload verified)"
            } else {
                Write-Host "  [ok] Engine installed (imports, but reports no GPU offload)"
            }
        } else {
            if ($cudaExit -eq 0) {
                # pip succeeded but the native library will not load. That is a
                # missing CUDA runtime far more often than a broken wheel.
                Write-Host "       The CUDA wheel installed but llama.dll will not load - cleaning up."
                if (-not (Test-CudaRuntime)) {
                    Write-Host "       Cause: the CUDA 12 runtime (cublas64_12.dll / cudart64_12.dll) is not on PATH." -ForegroundColor Yellow
                    Write-Host "       An NVIDIA driver is not enough - install the CUDA 12 runtime, or keep the CPU wheel." -ForegroundColor Yellow
                }
            } else {
                Write-Host "       CUDA wheel install failed (pip exit $cudaExit) - cleaning up and trying the CPU wheel..."
            }
            Invoke-Native { & $venvPy -m pip uninstall --disable-pip-version-check --yes llama_cpp_python 2>$null | Out-Null }
            $LASTEXITCODE = 0
        }
    }
    if (-not $installed) {
        Write-Host "       Trying the prebuilt CPU wheel (llama.cpp CPU index)..."
        Invoke-Native { & $venvPy -m pip install --disable-pip-version-check --quiet --only-binary :all: llama-cpp-python --extra-index-url https://abetlen.github.io/llama-cpp-python/whl/cpu }
        $cpuExit = $LASTEXITCODE
        if ($cpuExit -eq 0 -and (Test-LlamaImport)) {
            $installed = $true
            Write-Host "  [ok] Engine installed (CPU)"
        } else {
            Write-Host "       CPU wheel unavailable (pip exit $cpuExit) - cleaning up and trying PyPI (may build from source)..."
            Invoke-Native { & $venvPy -m pip uninstall --disable-pip-version-check --yes llama_cpp_python 2>$null | Out-Null }
            $LASTEXITCODE = 0
        }
    }
    if (-not $installed) {
        $hasMsvc = $false
        try {
            $vswhere = "${env:ProgramFiles(x86)}\Microsoft Visual Studio\Installer\vswhere.exe"
            if (Test-Path $vswhere) {
                $vs = Invoke-Native { & $vswhere -latest -products * -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 -property installationPath 2>$null }
                if ($vs) { $hasMsvc = $true }
            }
            if (-not $hasMsvc -and (Get-Command cl.exe -ErrorAction SilentlyContinue)) { $hasMsvc = $true }
        } catch {}
        if ($hasMsvc) {
            Write-Host "       No prebuilt wheel - compiling from source with the detected MSVC toolchain (needs CMake+nmake)..."
            Invoke-Native { & $venvPy -m pip install --disable-pip-version-check --quiet llama-cpp-python }
            if ($LASTEXITCODE -eq 0 -and (Test-LlamaImport)) {
                $installed = $true
                Write-Host "  [ok] Engine installed (CPU, built from source)"
            }
        }
        if (-not $installed) {
            Write-Host "  [X] No prebuilt llama-cpp-python wheel for $(& $pyExe @pyArgs --version) on this system." -ForegroundColor Red
            Write-Host "      Options:" -ForegroundColor Yellow
            Write-Host "        1. Switch to a verified Python version (3.12 or 3.13) and rerun setup." -ForegroundColor Yellow
            Write-Host "        2. Install Visual Studio Build Tools (Desktop development with C++) and rerun." -ForegroundColor Yellow
            Write-Host "        3. Manually download a matching wheel from community collections (e.g. Hugging Face llama-cpp-wheels)." -ForegroundColor Yellow
            Read-Host "  Press Enter to exit"
            exit 1
        }
    }
} else {
    Write-Host "  [ok] AI engine present"
}

# 4b. One authoritative CUDA record, written for every path above.
#     The marker means "a wheel with GPU offload is installed and verified",
#     nothing else. Deciding it from `import llama_cpp`, or writing it just
#     because a previous run found an engine, is what made CPU machines - and
#     CPU wheels on GPU machines - advertise CUDA they do not have.
if (Test-LlamaCuda) {
    if (-not (Test-Path $marker)) { New-Item -ItemType File -Path $marker -Force | Out-Null }
    Write-Host "  [ok] GPU offload verified (llama_supports_gpu_offload)"
} else {
    if (Test-Path $marker) {
        Remove-Item $marker -Force
        Write-Host "       Removed a stale .llama-cuda-ok: the installed wheel has no GPU offload." -ForegroundColor Yellow
    }
    if ($hasNvidia) {
        Write-Host "       NVIDIA GPU present, but this wheel is CPU-only - the app will run on CPU." -ForegroundColor Yellow
        if (-not (Test-CudaRuntime)) {
            Write-Host "       For GPU offload: install the CUDA 12 runtime, then:" -ForegroundColor Yellow
            Write-Host "         .venv\Scripts\pip uninstall -y llama-cpp-python   and rerun setup" -ForegroundColor Yellow
        }
    }
}

# 5. offline self-test (fast, no model download)
Write-Host "  [..] Running offline sanity tests..."
# The LLM backend is pinned inside desktop/tests/conftest.py (the report gate
# in core/report_intent.py blocks exports whenever the engine reports "stub",
# and the tests mock core.llm.chat instead of loading a model).
$env:EMBEDDING_BACKEND = "hash-stub"
# Keep the full pytest output on disk: the console only gets the tail, and a
# truncated traceback hides the real exception. The base temp dir is pinned
# inside the repo rather than left to %TEMP%: a locked-down or ACL-damaged
# pytest-of-<user> folder turns every tmp_path fixture into a PermissionError
# that looks like a code failure (22 setup errors, no test actually run).
$testLog = Join-Path $PSScriptRoot ".pytest-self-test.log"
$testTmp = Join-Path $PSScriptRoot ".pytest-tmp"
Invoke-Native { & $venvPy -m pytest desktop\tests\ -q --no-header --basetemp $testTmp 2>&1 } | Tee-Object -Variable testOut | Select-Object -Last 20
$testOut | Out-File -FilePath $testLog -Encoding utf8
if ($LASTEXITCODE -ne 0) {
    Write-Host "  [X] Self-test failed - full output: $testLog" -ForegroundColor Red
    Read-Host "  Press Enter to exit"
    exit 1
}
Remove-Item Env:EMBEDDING_BACKEND -ErrorAction SilentlyContinue
Write-Host "  [ok] Self-tests passed (full log: $testLog)"

Write-Host ""
Write-Host "  Setup complete. Starting Fortis..." -ForegroundColor Green
Write-Host "  (First chat message downloads the AI model - about 3.4 GB, one time.)"
Write-Host ""
& $venvPy desktop\app.py
