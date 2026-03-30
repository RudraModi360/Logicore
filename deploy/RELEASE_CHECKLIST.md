# 📦 Logicore — Release Checklist

Use this checklist every time you publish a new version.

---

## Pre-Release

- [ ] **1. Update version** in two places:
  - `pyproject.toml` → `version = "X.Y.Z"`
  - `logicore/__init__.py` → `__version__ = "X.Y.Z"`
- [ ] **2. Update `deploy/CHANGELOG.md`** with release notes
- [ ] **3. Run tests**: `pytest tests/`
- [ ] **4. Clean old builds**: `Remove-Item -Recurse -Force dist/, build/, *.egg-info`

## Build

- [ ] **5. Build the package**:
  ```powershell
  python -m build
  ```
- [ ] **6. Validate the build**:
  ```powershell
  twine check dist/*
  ```

## Test (Optional but Recommended)

- [ ] **7. Upload to TestPyPI**:
  ```powershell
  twine upload --repository testpypi dist/*
  ```
- [ ] **8. Install from TestPyPI and verify**:
  ```powershell
  pip install --index-url https://test.pypi.org/simple/ --extra-index-url https://pypi.org/simple/ logicore
  python -c "import logicore; print(logicore.__version__)"
  ```

## Publish

- [ ] **9. Upload to PyPI**:
  ```powershell
  twine upload dist/*
  ```
- [ ] **10. Create git tag and push**:
  ```powershell
  git tag v<VERSION>
  git push origin v<VERSION>
  ```

## Post-Release (SEO)

- [ ] **11. Follow `deploy/seo_checklist.md`** for Google indexing
- [ ] **12. Verify PyPI page** renders README correctly: https://pypi.org/project/logicore/
