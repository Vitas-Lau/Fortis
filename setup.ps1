# Fortis Desktop setup script (Windows)
# Usage:  right-click -> Run with PowerShell, or:  powershell -ExecutionPolicy Bypass -File setup.ps1
$ErrorActionPreference = "Stop"

function Invoke-Native {
    param([scriptblock]$Command)
    $prev = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try { & $Command } finally { $ErrorActionPreference = $prev }
}

Write-Host ""
Write-Host "  Fortis - Cybersecurity Advisor : setup" -ForegroundColor Cyan
Write-Host "  ----------------------------------------" -ForegroundColor DarkGray

# 1. Python check (Lock to verified versions 3.10 - 3.13; avoid 3.14+)
#    The executable and its launcher args are kept in separate variables:
#    PowerShell's call operator (&) never splits a string on spaces, so a
#    combined "py -3.13" token would be looked up as one command name.
$pyExe = $null
$pyArgs = @()
# Priority 1: Check if py launcher has Python 3.13 or 3.12 explicitly
foreach ($ver in @("-3.13", "-3.12")) {
    try {
        $v = & py $ver --version 2>$null
        if ($v -match "Python 3\.(1[2-3])\.") { $pyExe = "py"; $pyArgs = @($ver); break }
    } catch {}
}
# Priority 2: Fallback to general python/py commands if they match 3.10-3.13
if (-not $pyExe) {
    foreach ($c in @("python", "py")) {
        try {
            $v = & $c --version 2>$null
            if ($v -match "Python 3\.(1[0-3])\.") { $pyExe = $c; $pyArgs = @(); break }
        } catch {}
    }
}
if (-not $pyExe) {
    Write-Host ""
    Write-Host "  [X] Compatible Python (3.10 - 3.13) not found." -ForegroundColor Red
    Write-Host "      Python 3.14+ is currently incompatible due to missing prebuilt wheels." -ForegroundColor Yellow
    Write-Host "      Please install Python 3.13 from https://www.python.org/downloads/" -ForegroundColor Yellow
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
    & $venvPy -m pip install --disable-pip-version-check -r requirements-dev.txt
    if ($LASTEXITCODE -ne 0) { Write-Host "  [X] Dependency install failed - check internet connection" -ForegroundColor Red; Read-Host "  Press Enter to exit"; exit 1 }
    Write-Host "  [ok] Dependencies installed"
} else {
    Write-Host "  [ok] Dependencies present"
}

# 4. llama-cpp-python: prebuilt CUDA wheel if GPU, else prebuilt CPU wheel
#    (never builds from source - that needs MSVC/CMake which we can't assume)
$marker = Join-Path $PSScriptRoot ".llama-cuda-ok"
$haveLlama = Invoke-Native { & $venvPy -c "import llama_cpp" 2>$null }
if ($LASTEXITCODE -ne 0) {
    $hasNvidia = $false
    try {
        $smi = Get-Command nvidia-smi -ErrorAction SilentlyContinue
        if ($smi) { & nvidia-smi -L | Out-Null; if ($LASTEXITCODE -eq 0) { $hasNvidia = $true } }
    } catch {}

    Write-Host "  [..] Installing the local AI engine (llama.cpp, ~200 MB)..."
    $ok = $false

    function Test-LlamaImport {
        Invoke-Native { & $venvPy -c "import llama_cpp" 2>$null } | Out-Null
        return ($LASTEXITCODE -eq 0)
    }

    if (Test-Path "$venvPy") {
        # NB: a bare `2>$null` on a native command turns pip's stderr warning
        # ("Skipping llama_cpp_python as it is not installed.") into a
        # NativeCommandError, which $ErrorActionPreference = "Stop" makes fatal.
        # Invoke-Native relaxes the preference for the duration of the call.
        Invoke-Native { & $venvPy -m pip uninstall --disable-pip-version-check --yes llama_cpp_python 2>$null | Out-Null }
        $LASTEXITCODE = 0
    }

    if ($hasNvidia) {
        Write-Host "       NVIDIA GPU detected - trying the CUDA wheel first..."
        & $venvPy -m pip install --disable-pip-version-check --quiet --only-binary :all: llama-cpp-python --extra-index-url https://abetlen.github.io/llama-cpp-python/whl/cu124
        if ((Test-LlamaImport)) { $ok = $true }
        if ($ok) {
            New-Item -ItemType File -Path $marker -Force | Out-Null
            Write-Host "  [ok] Engine installed (GPU-accelerated)"
        } else {
            Write-Host "       CUDA wheel failed to install or import - cleaning up and trying the CPU wheel..."
            Invoke-Native { & $venvPy -m pip uninstall --disable-pip-version-check --yes llama_cpp_python 2>$null | Out-Null }
            $LASTEXITCODE = 0
        }
    }
    if (-not $ok) {
        Write-Host "       Trying the prebuilt CPU wheel (llama.cpp CPU index)..."
        & $venvPy -m pip install --disable-pip-version-check --quiet --only-binary :all: llama-cpp-python --extra-index-url https://abetlen.github.io/llama-cpp-python/whl/cpu
        if ((Test-LlamaImport)) { $ok = $true }
        if ($ok) {
            Write-Host "  [ok] Engine installed (CPU)"
        } else {
            Write-Host "       CPU wheel unavailable - cleaning up and trying PyPI (may build from source)..."
            Invoke-Native { & $venvPy -m pip uninstall --disable-pip-version-check --yes llama_cpp_python 2>$null | Out-Null }
            $LASTEXITCODE = 0
        }
    }
    if (-not $ok) {
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
            & $venvPy -m pip install --disable-pip-version-check --quiet llama-cpp-python
            if ((Test-LlamaImport)) { $ok = $true }
        }
        if ($ok) {
            Write-Host "  [ok] Engine installed (CPU, built from source)"
        } else {
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
    if (-not (Test-Path $marker)) { New-Item -ItemType File -Path $marker -Force | Out-Null }
}

# 5. offline self-test (fast, no model download)
Write-Host "  [..] Running offline sanity tests..."
# The LLM backend is pinned inside desktop/tests/conftest.py (the report gate
# in core/report_intent.py blocks exports whenever the engine reports "stub",
# and the tests mock core.llm.chat instead of loading a model).
$env:EMBEDDING_BACKEND = "hash-stub"
# Keep the full pytest output on disk: the console only gets the tail, and a
# truncated traceback hides the real exception.
$testLog = Join-Path $PSScriptRoot ".pytest-self-test.log"
Invoke-Native { & $venvPy -m pytest desktop\tests\ -q --no-header 2>&1 } | Tee-Object -Variable testOut | Select-Object -Last 20
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
