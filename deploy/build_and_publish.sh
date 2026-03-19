#!/usr/bin/env bash
#
# Build and publish logicore to PyPI (or TestPyPI).
#
# Usage:
#   ./deploy/build_and_publish.sh              # Build + upload to PyPI
#   ./deploy/build_and_publish.sh --test       # Build + upload to TestPyPI
#   ./deploy/build_and_publish.sh --dry-run    # Build only, no upload
#

set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
TEST_PYPI=false
DRY_RUN=false

for arg in "$@"; do
    case $arg in
        --test) TEST_PYPI=true ;;
        --dry-run) DRY_RUN=true ;;
        *) echo "Unknown arg: $arg"; exit 1 ;;
    esac
done

echo "========================================"
echo "  Logicore Build & Publish Script"
echo "========================================"
echo ""

# --- Step 1: Version Check ---
INIT_VERSION=$(grep -oP '__version__\s*=\s*"\K[^"]+' "$PROJECT_ROOT/logicore/__init__.py")
PYPROJECT_VERSION=$(grep -oP 'version\s*=\s*"\K[^"]+' "$PROJECT_ROOT/pyproject.toml")

echo "[1/5] Version Check"
echo "  __init__.py:    $INIT_VERSION"
echo "  pyproject.toml: $PYPROJECT_VERSION"

if [ "$INIT_VERSION" != "$PYPROJECT_VERSION" ]; then
    echo "  ERROR: Version mismatch! Update both files." >&2
    exit 1
fi
echo "  ✓ Versions match."
echo ""

# --- Step 2: Clean ---
echo "[2/5] Cleaning old build artifacts..."
cd "$PROJECT_ROOT"
rm -rf dist/ build/ *.egg-info
echo "  ✓ Clean complete."
echo ""

# --- Step 3: Build ---
echo "[3/5] Building package..."
python -m build
echo "  ✓ Build complete."
echo ""

# --- Step 4: Validate ---
echo "[4/5] Validating with twine..."
twine check dist/*
echo "  ✓ Validation passed."
echo ""

# --- Step 5: Upload ---
if [ "$DRY_RUN" = true ]; then
    echo "[5/5] DRY RUN — Skipping upload."
    echo "  Build artifacts in: dist/"
elif [ "$TEST_PYPI" = true ]; then
    echo "[5/5] Uploading to TestPyPI..."
    twine upload --repository testpypi dist/*
    echo ""
    echo "  ✓ Published to TestPyPI!"
    echo "  Install: pip install --index-url https://test.pypi.org/simple/ --extra-index-url https://pypi.org/simple/ logicore"
    echo "  View:    https://test.pypi.org/project/logicore/"
else
    echo "[5/5] Uploading to PyPI..."
    twine upload dist/*
    echo ""
    echo "  ✓ Published to PyPI!"
    echo "  Install: pip install logicore"
    echo "  View:    https://pypi.org/project/logicore/"
fi

echo ""
echo "========================================"
echo "  Done! Version: $INIT_VERSION"
echo "========================================"
