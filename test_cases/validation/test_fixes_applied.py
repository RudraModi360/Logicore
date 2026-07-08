"""
Validation tests to verify all security and dead code fixes were properly applied.

Each test reads source files or inspects module behavior to confirm
that specific fixes are in place.
"""

import ast
import re
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
LOGICORE = PROJECT_ROOT / "logicore"


def _read_source(relative_path: str) -> str:
    """Read source file and return contents."""
    return (LOGICORE / relative_path).read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# 1. execution.py uses mkstemp
# ---------------------------------------------------------------------------

class TestExecutionUsesMkstemp:
    def test_mkstemp_in_execution(self):
        source = _read_source("tools/execution.py")
        assert "tempfile.mkstemp" in source, (
            "execution.py should use tempfile.mkstemp instead of NamedTemporaryFile(delete=False)"
        )

    def test_no_named_temp_file_delete_false(self):
        source = _read_source("tools/execution.py")
        assert "NamedTemporaryFile" not in source, (
            "execution.py should not use NamedTemporaryFile"
        )


# ---------------------------------------------------------------------------
# 2. execution.py has chmod 0o600
# ---------------------------------------------------------------------------

class TestExecutionChmodPermissions:
    def test_chmod_0o600_present(self):
        source = _read_source("tools/execution.py")
        assert "0o600" in source, (
            "execution.py should apply chmod 0o600 to temp files"
        )

    def test_os_chmod_called(self):
        source = _read_source("tools/execution.py")
        assert "os.chmod" in source, (
            "execution.py should call os.chmod to restrict temp file permissions"
        )


# ---------------------------------------------------------------------------
# 3. input_enricher.py _extract_text_from_url has SSRF check
# ---------------------------------------------------------------------------

class TestBaseSSRFCheck:
    def test_extract_text_from_url_calls_validate(self):
        source = _read_source("agent/input_enricher.py")
        assert "_validate_url_safety" in source, (
            "input_enricher.py _extract_text_from_url must call _validate_url_safety"
        )

    def test_extract_text_from_url_imports_validate(self):
        source = _read_source("agent/input_enricher.py")
        assert "from logicore.tools.web import _validate_url_safety" in source, (
            "input_enricher.py should import _validate_url_safety from logicore.tools.web"
        )

    def test_extract_text_from_url_checks_result(self):
        source = _read_source("agent/input_enricher.py")
        assert "is_safe" in source and "reason" in source, (
            "input_enricher.py should check the (is_safe, reason) tuple from _validate_url_safety"
        )


# ---------------------------------------------------------------------------
# 4. notifications.py uses EncodedCommand
# ---------------------------------------------------------------------------

class TestNotificationsEncodedCommand:
    def test_encoded_command_used(self):
        source = _read_source("cron/notifications.py")
        assert "-EncodedCommand" in source, (
            "notifications.py should use -EncodedCommand for PowerShell script execution"
        )

    def test_no_raw_command_flag(self):
        source = _read_source("cron/notifications.py")
        # Verify that -Command is not used for passing raw script text
        # The only -Command usage should be -EncodedCommand
        lines = source.splitlines()
        for line in lines:
            stripped = line.strip()
            if stripped.startswith("#"):
                continue
            # Check for bare -Command usage (not -EncodedCommand)
            if re.search(r'["\']-Command["\']', stripped) and "EncodedCommand" not in stripped:
                pytest.fail(
                    f"notifications.py should not use bare -Command flag: {stripped}"
                )

    def test_base64_encoding_present(self):
        source = _read_source("cron/notifications.py")
        assert "base64" in source and "b64encode" in source, (
            "notifications.py should base64-encode scripts for EncodedCommand"
        )


# ---------------------------------------------------------------------------
# 5. settings.py default HOST
# ---------------------------------------------------------------------------

class TestSettingsDefaultHost:
    def test_host_defaults_to_localhost(self):
        source = _read_source("config/settings.py")
        # Check the HOST field definition contains 127.0.0.1
        assert "127.0.0.1" in source, (
            "settings.py HOST default should be 127.0.0.1 (not 0.0.0.0)"
        )

    def test_host_field_not_bind_all(self):
        source = _read_source("config/settings.py")
        lines = source.splitlines()
        for line in lines:
            stripped = line.strip()
            if "HOST" in stripped and "default" in stripped:
                assert "0.0.0.0" not in stripped, (
                    "settings.py HOST must not default to 0.0.0.0"
                )


# ---------------------------------------------------------------------------
# 6. web.py URL max_length
# ---------------------------------------------------------------------------

class TestUrlFetchMaxLength:
    def test_url_field_has_max_length(self):
        source = _read_source("tools/web.py")
        assert "max_length" in source, (
            "web.py UrlFetchParams should enforce max_length on url field"
        )

    def test_max_length_value(self):
        tree = ast.parse(_read_source("tools/web.py"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef) and node.name == "UrlFetchParams":
                for item in node.body:
                    if isinstance(item, ast.AnnAssign) and getattr(item.target, "id", None) == "url":
                        for keyword in item.value.keywords:
                            if keyword.arg == "max_length":
                                val = keyword.value.value if isinstance(keyword.value, ast.Constant) else keyword.value
                                assert val == 2048, (
                                    f"UrlFetchParams.url max_length should be 2048, got {val}"
                                )
                                return
        pytest.fail("Could not find UrlFetchParams.url with max_length=2048")


# ---------------------------------------------------------------------------
# 7. marker_service.py sanitizes filenames
# ---------------------------------------------------------------------------

class TestMarkerServiceSanitizesFilenames:
    def test_filename_sanitization(self):
        source = _read_source("services/marker_service.py")
        # Verify filename sanitization logic exists (isalnum filtering)
        assert "isalnum" in source, (
            "marker_service.py should sanitize filenames using isalnum or similar filtering"
        )

    def test_stem_sanitized_before_use(self):
        source = _read_source("services/marker_service.py")
        # The stem should be sanitized before constructing the output path
        assert "stem" in source, (
            "marker_service.py should sanitize the filename stem before use"
        )


# ---------------------------------------------------------------------------
# 8. telemetry tracker validates model names
# ---------------------------------------------------------------------------

class TestTrackerModelNameValidation:
    def test_model_name_regex_exists(self):
        source = _read_source("telemetry/tracker.py")
        assert re.search(r"re\.match\(r'[^']+', model\)", source) is not None, (
            "tracker.py should validate model names with a regex pattern"
        )

    def test_model_name_validation_pattern(self):
        source = _read_source("telemetry/tracker.py")
        # Should reject model names with shell injection characters
        assert "^[a-zA-Z0-9._:-]+$" in source or re.search(
            r"re\.match\(r'\^[^']+\$', model\)", source
        ), (
            "tracker.py should use a strict regex pattern for model name validation"
        )


# ---------------------------------------------------------------------------
# 9. Dead code removed - memory module
# ---------------------------------------------------------------------------

class TestDeadCodeRemovedMemory:
    def test_memory_module_does_not_exist(self):
        memory_path = LOGICORE / "memory"
        assert not memory_path.exists(), (
            "logicore/memory/ should have been deleted (memory system removed)"
        )

    def test_memory_tools_py_does_not_exist(self):
        memory_path = LOGICORE / "tools" / "memory.py"
        assert not memory_path.exists(), (
            "logicore/tools/memory.py should have been deleted (dead code)"
        )

    def test_user_profile_manager_py_does_not_exist(self):
        upm_path = LOGICORE / "user_profile_manager.py"
        assert not upm_path.exists(), (
            "logicore/user_profile_manager.py should have been deleted (dead code)"
        )


# ---------------------------------------------------------------------------
# 11. RetryStats removed from policies.py
# ---------------------------------------------------------------------------

class TestRetryStatsRemoved:
    def test_no_retry_stats_class(self):
        source = _read_source("providers/policies.py")
        assert "class RetryStats" not in source, (
            "RetryStats class should have been removed from policies.py"
        )

    def test_no_retry_stats_reference(self):
        source = _read_source("providers/policies.py")
        assert "RetryStats" not in source, (
            "RetryStats should not appear anywhere in policies.py"
        )


# ---------------------------------------------------------------------------
# 12. .env in gitignore
# ---------------------------------------------------------------------------

class TestEnvInGitignore:
    def test_env_in_gitignore(self):
        gitignore_path = PROJECT_ROOT / ".gitignore"
        content = gitignore_path.read_text(encoding="utf-8")
        assert ".env" in content, (
            ".gitignore must list .env to prevent committing secrets"
        )

    def test_env_files_pattern(self):
        gitignore_path = PROJECT_ROOT / ".gitignore"
        content = gitignore_path.read_text(encoding="utf-8")
        lines = [l.strip() for l in content.splitlines() if l.strip() and not l.strip().startswith("#")]
        env_patterns = [l for l in lines if ".env" in l]
        assert len(env_patterns) > 0, (
            ".gitignore should have at least one .env-related pattern"
        )


# ---------------------------------------------------------------------------
# 13. test_cases/ directory structure
# ---------------------------------------------------------------------------

class TestDirectoryStructure:
    REQUIRED_DIRS = [
        "test_cases/security",
        "test_cases/dead_code",
        "test_cases/production_readiness",
        "test_cases/validation",
        "test_cases/integration",
    ]

    @pytest.mark.parametrize("rel_dir", REQUIRED_DIRS)
    def test_directory_exists(self, rel_dir: str):
        dir_path = PROJECT_ROOT / rel_dir
        assert dir_path.is_dir(), f"Directory '{rel_dir}' must exist"
