#!/usr/bin/env bash
# Fortis Desktop setup (macOS / Linux)
# Usage:  bash setup.sh
set -euo pipefail

echo ""
echo "  Fortis - Cybersecurity Advisor : setup"
echo "  ----------------------------------------"

# 1. Python check (3.11 - 3.14, verified on 3.13)
#    * 3.10 and older: matplotlib 3.11.x / torch 2.15 ship cp311+ wheels only,
#      so requirements.txt would have to be compiled from source.
#    * 3.11 - 3.14: the llama.cpp index serves py3-none wheels (0.3.36/0.3.37),
#      so the AI engine installs without a toolchain - including on 3.14.
#    Anything outside the window is rejected here rather than five minutes
#    later inside pip, with a message that blames the network.
PY=""
FOUND=""
for c in python3.13 python3.12 python3.14 python3.11 python3 python; do
    command -v "$c" >/dev/null 2>&1 || continue
    v="$("$c" --version 2>&1 | grep -oE 'Python 3\.[0-9]+' | grep -oE '3\.[0-9]+' || true)"
    if [ -n "$v" ]; then FOUND="$FOUND $c=$v"; fi
    case "$v" in
        3.11|3.12|3.13|3.14) PY="$c"; break ;;
    esac
done
if [ -z "$PY" ]; then
    echo "  [X] Compatible Python (3.11 - 3.14) not found.${FOUND:+ Found:$FOUND}"
    echo "      Python 3.10 or older: no matplotlib / torch wheels for it."
    echo "      Install python3.13 (https://www.python.org/downloads/) or via brew:"
    echo "        brew install python@3.13"
    exit 1
fi
echo "  [ok] $("$PY" --version)"

# 2. venv
cd "$(dirname "$0")"
if [ ! -x ".venv/bin/python" ]; then
    echo "  [..] Creating virtual environment (one-time)..."
    "$PY" -m venv .venv
fi
echo "  [ok] Virtual environment ready"
VPY=".venv/bin/python"

# 3. dependencies
if ! "$VPY" -c "import fastapi, chromadb, fitz, docx, pptx, openpyxl, reportlab, huggingface_hub, uvicorn, multipart, rank_bm25, matplotlib, webview, pytest" >/dev/null 2>&1; then
    echo "  [..] Installing dependencies (first run only, ~5 min)..."
    "$VPY" -m pip install --disable-pip-version-check -r requirements-dev.txt || {
        echo "  [X] Dependency install failed - check internet connection"
        exit 1
    }
    echo "  [ok] Dependencies installed"
else
    echo "  [ok] Dependencies present"
fi

# 4. llama-cpp-python
#    Prebuilt wheels only, from the llama.cpp wheel indexes (PyPI has no
#    wheels for this package - just the sdist). macOS on Apple Silicon and
#    Linux get wheels from the CPU index; NVIDIA machines try CUDA first.
#    CUDA is not tied to the Python version: the cu124 wheels are tagged
#    py3-none, so they install on any supported interpreter and only need the
#    CUDA 12 runtime libraries to load.
MARKER="$(pwd)/.llama-cuda-ok"
# True only when the installed wheel was actually built with GPU offload.
# `import llama_cpp` is not a CUDA test: the CPU wheel imports fine on a GPU.
llama_has_cuda() {
    "$VPY" -c "import llama_cpp, sys; sys.exit(0 if getattr(llama_cpp, 'llama_supports_gpu_offload', lambda: False)() else 1)" >/dev/null 2>&1
}
has_nvidia() {
    command -v nvidia-smi >/dev/null 2>&1 && nvidia-smi -L >/dev/null 2>&1
}
if ! "$VPY" -c "import llama_cpp" >/dev/null 2>&1; then
    echo "  [..] Installing the local AI engine (llama.cpp)..."
    OK=0
    "$VPY" -m pip uninstall --disable-pip-version-check --yes llama_cpp_python >/dev/null 2>&1 || true
    if command -v nvidia-smi >/dev/null 2>&1 && nvidia-smi -L >/dev/null 2>&1; then
        echo "       NVIDIA GPU detected - trying the CUDA wheel first..."
        if "$VPY" -m pip install --disable-pip-version-check --quiet --only-binary :all: \
            llama-cpp-python --extra-index-url https://abetlen.github.io/llama-cpp-python/whl/cu124 \
            && "$VPY" -c "import llama_cpp" >/dev/null 2>&1; then
            OK=1
        else
            echo "       CUDA wheel failed to install or import - cleaning up and trying the CPU wheel..."
            "$VPY" -m pip uninstall --disable-pip-version-check --yes llama_cpp_python >/dev/null 2>&1 || true
        fi
    fi
    if [ "$OK" = "0" ]; then
        echo "       Trying the prebuilt CPU wheel (llama.cpp CPU index)..."
        if "$VPY" -m pip install --disable-pip-version-check --quiet --only-binary :all: \
            llama-cpp-python --extra-index-url https://abetlen.github.io/llama-cpp-python/whl/cpu \
            && "$VPY" -c "import llama_cpp" >/dev/null 2>&1; then
            OK=1
        else
            echo "       CPU wheel unavailable - cleaning up..."
            "$VPY" -m pip uninstall --disable-pip-version-check --yes llama_cpp_python >/dev/null 2>&1 || true
        fi
    fi
    if [ "$OK" = "0" ]; then
        echo "  [X] No prebuilt llama-cpp-python wheel for this system."
        echo "      Options:"
        echo "        1. Install Visual Studio Build Tools / build-essential + CMake and rerun (builds from source)."
        echo "        2. Check https://abetlen.github.io/llama-cpp-python/whl/ for available wheels."
        exit 1
    fi
else
    echo "  [ok] AI engine present"
fi

# 4b. One authoritative CUDA record, written for both paths above. The marker
#     means "a wheel with GPU offload is installed and verified"; writing it
#     whenever an engine happens to exist is what made CPU-only installs
#     advertise CUDA. A stale marker is removed instead of trusted.
if has_nvidia && llama_has_cuda; then
    touch "$MARKER"
    echo "  [ok] GPU offload verified (llama_supports_gpu_offload)"
else
    if [ -f "$MARKER" ]; then
        rm -f "$MARKER"
        echo "       Removed a stale .llama-cuda-ok: the installed wheel has no GPU offload."
    fi
    if has_nvidia; then
        echo "       NVIDIA GPU present, but this wheel is CPU-only - the app will run on CPU."
        echo "       (The cu124 wheels need the CUDA 12 runtime libs (libcublas/libcudart); a driver alone is not enough.)"
    fi
fi

# 5. offline self-test
#    --basetemp keeps pytest out of a shared /tmp that may be mounted noexec or
#    ACL-damaged, which otherwise fails every tmp_path fixture at setup.
echo "  [..] Running offline sanity tests..."
EMBEDDING_BACKEND=hash-stub "$VPY" -m pytest desktop/tests/ -q --no-header --basetemp "$(pwd)/.pytest-tmp" | tee .pytest-self-test.log | tail -n 15 || {
    echo "  [X] Self-test failed - full output: $(pwd)/.pytest-self-test.log"
    exit 1
}
echo "  [ok] Self-tests passed"

echo ""
echo "  Setup complete. Starting Fortis..."
echo "  (First chat message downloads the AI model - about 3.4 GB, one time.)"
echo ""
exec "$VPY" desktop/app.py
