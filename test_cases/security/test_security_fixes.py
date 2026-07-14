"""
Comprehensive Security Test Suite for Logicore

Verifies that known security vulnerabilities have been remediated.
Each test validates a specific security property using source code analysis,
import inspection, and behavioral checks.

Run with: pytest test_cases/security/test_security_fixes.py -v
"""

import ast
import inspect
import os
import re
import textwrap
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[2]
LOGICORE_DIR = REPO_ROOT / "logicore"


def _read_source(filename: str) -> str:
    """Read the source code of a module given its filename relative to logicore/."""
    path = LOGICORE_DIR / filename
    return path.read_text(encoding="utf-8")


def _ast_parse(filename: str) -> ast.Module:
    """Parse a module's source into an AST."""
    return ast.parse(_read_source(filename))


# ---------------------------------------------------------------------------
# 1. Temp file permissions (execution.py)
# ---------------------------------------------------------------------------

class TestTempFilePermissions:
    """
    Temp files created for code execution must be chmod 0o600 so only the
    owning user can read/write them. This prevents local information leaks.
    """

    def test_execution_tool_sets_chmod_600(self):
        """
        Verify that _run_python_code and CodeExecuteTool.run call
        os.chmod(tmp_path, 0o600) after creating temp files.
        """
        source = _read_source("tools/execution.py")
        assert "0o600" in source, (
            "execution.py must set temp file permissions to 0o600"
        )
        assert source.count("0o600") >= 2, (
            "Both ExecuteCommandTool and CodeExecuteTool must call os.chmod with 0o600"
        )

    def test_execution_tool_uses_mkstemp(self):
        """
        Verify that tempfile.mkstemp is used instead of tempfile.NamedTemporaryFile
        (which can have race conditions on Windows).
        """
        source = _read_source("tools/execution.py")
        assert "mkstemp" in source, (
            "execution.py must use tempfile.mkstemp for secure temp file creation"
        )

    def test_chmod_after_mkstemp(self):
        """
        os.chmod must be called immediately after mkstemp to prevent a TOCTOU race.
        Verify the ordering in source.
        """
        source = _read_source("tools/execution.py")
        mkstemp_pos = source.find("mkstemp")
        chmod_pos = source.find("0o600")
        assert mkstemp_pos < chmod_pos, (
            "os.chmod with 0o600 must appear after mkstemp"
        )

    def test_temp_file_cleanup_in_finally(self):
        """
        Temp files must be cleaned up in a finally block to avoid leaks on error.
        """
        source = _read_source("tools/execution.py")
        assert "finally:" in source and "os.remove" in source, (
            "Temp files must be removed in a finally block"
        )


# ---------------------------------------------------------------------------
# 2. SSRF protection in agent (base.py)
# ---------------------------------------------------------------------------

class TestSSRFProtection:
    """
    The agent's _extract_text_from_url must call _validate_url_safety before
    making any HTTP request to prevent Server-Side Request Forgery.
    """

    def test_extract_text_from_url_calls_validate(self):
        """
        Verify _extract_text_from_url calls _validate_url_safety before httpx.
        """
        # Check in input_enricher.py (extracted from base.py)
        source = _read_source("agent/input_enricher.py")
        assert "_validate_url_safety" in source, (
            "input_enricher.py must call _validate_url_safety for SSRF protection"
        )
        assert "_extract_text_from_url" in source, (
            "input_enricher.py must define _extract_text_from_url"
        )

    def test_validate_called_before_httpx(self):
        """
        The validate call must precede the httpx client usage.
        """
        source = _read_source("agent/input_enricher.py")
        validate_pos = source.find("_validate_url_safety")
        httpx_pos = source.find("httpx.Client")
        assert validate_pos < httpx_pos, (
            "_validate_url_safety must be called before httpx.Client"
        )

    def test_validate_result_blocks_unsafe_urls(self):
        """
        If _validate_url_safety returns False, the method must return None
        and never reach the HTTP request.
        """
        source = _read_source("agent/input_enricher.py")
        assert "if not is_safe:" in source or "if is_safe:" in source, (
            "input_enricher.py must check the return value of _validate_url_safety"
        )

    def test_web_module_has_validate_url_safety(self):
        """
        The web module must export _validate_url_safety with SSRF checks.
        """
        source = _read_source("tools/web.py")
        assert "_validate_url_safety" in source, (
            "web.py must define _validate_url_safety"
        )
        assert "_BLOCKED_IP_NETWORKS" in source, (
            "web.py must define blocked IP networks for SSRF protection"
        )

    def test_blocked_private_ip_ranges(self):
        """
        SSRF protection must block RFC 1918 private ranges, loopback,
        and link-local addresses.
        """
        source = _read_source("tools/web.py")
        for network in ["127.0.0.0/8", "10.0.0.0/8", "172.16.0.0/12",
                        "192.168.0.0/16", "169.254.0.0/16"]:
            assert network in source, (
                f"web.py must block {network} for SSRF protection"
            )


# ---------------------------------------------------------------------------
# 3. PowerShell command injection fix (notifications.py)
# ---------------------------------------------------------------------------

class TestPowerShellInjectionFix:
    """
    PowerShell scripts must use -EncodedCommand instead of -Command to
    prevent command injection through special characters.
    """

    def test_uses_encoded_command(self):
        """
        Verify _run_powershell_toast_script uses -EncodedCommand.
        """
        source = _read_source("cron/notifications.py")
        assert "-EncodedCommand" in source, (
            "notifications.py must use -EncodedCommand to prevent injection"
        )

    def test_no_raw_command_flag_in_toast_function(self):
        """
        Verify that -Command is not used for user-supplied data in the toast function.
        -EncodedCommand is the safe alternative.
        """
        source = _read_source("cron/notifications.py")
        func_start = source.find("def _run_powershell_toast_script")
        assert func_start != -1, "_run_powershell_toast_script must exist"
        func_end = source.find("\ndef ", func_start + 1)
        if func_end == -1:
            func_end = len(source)
        func_body = source[func_start:func_end]
        assert "-Command" not in func_body, (
            "_run_powershell_toast_script must not use -Command (use -EncodedCommand)"
        )

    def test_base64_encoding_before_execution(self):
        """
        Scripts must be base64-encoded before being passed to -EncodedCommand.
        """
        source = _read_source("cron/notifications.py")
        assert "base64" in source, (
            "notifications.py must use base64 encoding for PowerShell scripts"
        )
        assert "utf-16-le" in source, (
            "PowerShell scripts must be encoded as UTF-16LE before base64"
        )

    def test_html_escape_for_xml_payload(self):
        """
        User-controlled strings in XML payloads must be HTML-escaped.
        """
        source = _read_source("cron/notifications.py")
        assert "html.escape" in source, (
            "notifications.py must HTML-escape user strings in XML payloads"
        )


# ---------------------------------------------------------------------------
# 4. URL length validation (web.py)
# ---------------------------------------------------------------------------

class TestURLLengthValidation:
    """
    URL parameters must have a max_length constraint to prevent
    denial-of-service via extremely long URLs.
    """

    def test_url_fetch_params_has_max_length(self):
        """
        UrlFetchParams.url must have a max_length field constraint.
        """
        source = _read_source("tools/web.py")
        assert "max_length" in source, (
            "UrlFetchParams.url must have a max_length constraint"
        )

    def test_max_length_is_reasonable(self):
        """
        The max_length must be set to a reasonable value (e.g., 2048).
        """
        source = _read_source("tools/web.py")
        match = re.search(r"max_length\s*=\s*(\d+)", source)
        assert match, "max_length must be specified as a numeric value"
        value = int(match.group(1))
        assert 1024 <= value <= 8192, (
            f"max_length should be between 1024 and 8192, got {value}"
        )

    def test_url_fetch_tool_validates_before_request(self):
        """
        UrlFetchTool.run must call _validate_url_safety before fetching.
        """
        source = _read_source("tools/web.py")
        tool_start = source.find("class UrlFetchTool")
        assert tool_start != -1
        tool_end = source.find("\nclass ", tool_start + 1)
        if tool_end == -1:
            tool_end = len(source)
        tool_body = source[tool_start:tool_end]
        assert "_validate_url_safety" in tool_body, (
            "UrlFetchTool.run must call _validate_url_safety"
        )


# ---------------------------------------------------------------------------
# 5. Default host binding (settings.py)
# ---------------------------------------------------------------------------

class TestDefaultHostBinding:
    """
    The server must default to 127.0.0.1 (localhost) binding, not 0.0.0.0,
    to prevent accidental exposure to the network.
    """

    def test_host_defaults_to_localhost(self):
        """
        AgentrySettings.HOST must default to 127.0.0.1.
        """
        from logicore.config.settings import AgentrySettings
        settings = AgentrySettings()
        assert settings.HOST == "127.0.0.1", (
            f"HOST must default to 127.0.0.1, got {settings.HOST}"
        )

    def test_no_wildcard_binding_in_source(self):
        """
        The settings source must not use 0.0.0.0 as a default value.
        """
        source = _read_source("config/settings.py")
        # Check that 0.0.0.0 is not used as a default for HOST
        lines = source.split("\n")
        for i, line in enumerate(lines):
            stripped = line.strip()
            if "HOST" in stripped and "0.0.0.0" in stripped:
                # Check it's not in a comment or blocked pattern
                if not stripped.startswith("#") and "BLOCKED" not in stripped:
                    pytest.fail(
                        f"Line {i+1}: HOST must not default to 0.0.0.0"
                    )

    def test_settings_import_has_safe_host(self):
        """
        Importing settings must result in a safe default host binding.
        """
        from logicore.config.settings import settings
        assert settings.HOST in ("127.0.0.1", "localhost"), (
            f"Default HOST must be localhost/127.0.0.1, got {settings.HOST}"
        )


# ---------------------------------------------------------------------------
# 6. Model name validation (tracker.py)
# ---------------------------------------------------------------------------

class TestModelNameValidation:
    """
    Model names passed to subprocess calls must be validated against a regex
    to prevent command injection via malicious model name strings.
    """

    def test_ollama_subprocess_uses_regex_validation(self):
        """
        The _fetch_ollama method must validate model names with a regex
        before passing them to subprocess.run.
        """
        source = _read_source("telemetry/tracker.py")
        assert "re.match" in source or "re.compile" in source, (
            "tracker.py must use regex for model name validation"
        )
        # Look for regex validation of model names before subprocess call
        assert re.search(r"re\.match\(", source), (
            "tracker.py must validate model names with re.match()"
        )

    def test_regex_constrains_model_name_characters(self):
        """
        The regex must only allow safe characters (alphanumeric, dots, hyphens,
        colons, underscores) to prevent shell injection.
        """
        source = _read_source("telemetry/tracker.py")
        # Find regex patterns that look like model name validation
        patterns = re.findall(r"re\.match\(\s*r['\"]([^'\"]+)['\"]", source)
        model_patterns = [p for p in patterns if "a-z" in p or "a-zA-Z" in p]
        assert len(model_patterns) >= 1, (
            "tracker.py must have at least one regex pattern for model name validation"
        )
        for pattern in model_patterns:
            assert "^" in pattern, (
                f"Regex pattern {pattern} should be anchored with ^"
            )

    def test_subprocess_run_with_model_name(self):
        """
        subprocess.run calls must use list arguments (not shell=True) and
        model names must be validated before use.
        """
        source = _read_source("telemetry/tracker.py")
        # Verify subprocess calls use list, not shell=True
        assert "shell=True" not in source, (
            "tracker.py must not use shell=True in subprocess calls"
        )


# ---------------------------------------------------------------------------
# 7. Marker service path sanitization (marker_service.py)
# ---------------------------------------------------------------------------

class TestMarkerServicePathSanitization:
    """
    Filenames used in path construction must be sanitized to prevent
    path traversal attacks via malicious filenames.
    """

    def test_stem_sanitized_for_path_construction(self):
        """
        The filename stem must be sanitized to only allow safe characters
        before being used in path construction.
        """
        source = _read_source("services/marker_service.py")
        assert "isalnum" in source or "alphanumeric" in source.lower(), (
            "marker_service.py must sanitize filename stems"
        )

    def test_path_traversal_protection(self):
        """
        The code must prevent path traversal by sanitizing or rejecting
        filenames containing '../' or absolute paths.
        """
        source = _read_source("services/marker_service.py")
        # Check that path traversal is prevented
        has_sanitization = (
            "isalnum" in source
            or "os.path.basename" in source
            or "sanitize" in source.lower()
        )
        assert has_sanitization, (
            "marker_service.py must sanitize filenames to prevent path traversal"
        )

    def test_output_dir_is_resolved(self):
        """
        The output directory must use os.path.abspath to prevent relative
        path confusion.
        """
        source = _read_source("services/marker_service.py")
        assert "os.path.abspath" in source, (
            "marker_service.py must use os.path.abspath for OUTPUT_DIR"
        )


# ---------------------------------------------------------------------------
# 8. No hardcoded secrets
# ---------------------------------------------------------------------------

class TestNoHardcodedSecrets:
    """
    Scan all .py files for hardcoded passwords, API keys, tokens,
    and other sensitive data that should be loaded from environment variables.
    """

    SECRET_PATTERNS = [
        (r'(?i)password\s*=\s*["\'][^"\']+["\']',
         "Hardcoded password"),
        (r'(?i)api_key\s*=\s*["\'][^"\']{8,}["\']',
         "Hardcoded API key"),
        (r'(?i)(secret|token)\s*=\s*["\'][^"\']{8,}["\']',
         "Hardcoded secret/token"),
        (r'(?i)bearer\s+[A-Za-z0-9\-._~+/]+=*',
         "Hardcoded bearer token"),
        (r'(?i)(?:aws_secret_access_key|aws_secret)\s*=\s*["\'][^"\']+["\']',
         "Hardcoded AWS secret"),
    ]

    EXCLUDE_DIRS = {"__pycache__", "node_modules", ".git", "test_cases", "examples", "example"}
    EXCLUDE_FILES = {"conftest.py", "test_*.py", "*_test.py"}

    def test_no_hardcoded_passwords_in_source(self):
        """
        No .py file in logicore/ should contain hardcoded passwords.
        """
        violations = []
        for root, dirs, files in os.walk(LOGICORE_DIR):
            dirs[:] = [d for d in dirs if d not in self.EXCLUDE_DIRS]
            for fname in files:
                if not fname.endswith(".py"):
                    continue
                filepath = Path(root) / fname
                try:
                    content = filepath.read_text(encoding="utf-8")
                except Exception:
                    continue
                # Track docstring state to skip lines inside docstrings
                in_docstring = False
                triple_quote_char = None
                for line_num, line in enumerate(content.splitlines(), 1):
                    stripped = line.strip()
                    # Track docstring state
                    triple_count = stripped.count('"""') + stripped.count("'''")
                    if triple_count > 0:
                        if not in_docstring:
                            in_docstring = True
                            # Check if it's a single-line docstring
                            if triple_count == 1:
                                # Opens a multiline docstring
                                triple_quote_char = '"""' if '"""' in stripped else "'''"
                            elif triple_count == 2:
                                # Opens and closes on same line
                                in_docstring = False
                        else:
                            # Closing a docstring
                            in_docstring = False
                            triple_quote_char = None
                    elif in_docstring:
                        continue

                    # Skip comments and blank lines
                    if stripped.startswith("#") or not stripped:
                        continue
                    for pattern, desc in self.SECRET_PATTERNS:
                        if re.search(pattern, line):
                            # Exclude lines that read from env
                            if "os.getenv" in line or "os.environ" in line:
                                continue
                            if "ENV" in line or "env" in line:
                                continue
                            # Exclude placeholder/example values
                            if 'not-needed' in line or 'placeholder' in line.lower():
                                continue
                            violations.append(
                                f"{filepath.relative_to(REPO_ROOT)}:{line_num}: {desc}"
                            )

        if violations:
            msg = "Hardcoded secrets found:\n" + "\n".join(violations)
            pytest.fail(msg)

    def test_no_private_keys_in_source(self):
        """
        No .py file should contain PEM-encoded private keys.
        """
        for root, dirs, files in os.walk(LOGICORE_DIR):
            dirs[:] = [d for d in dirs if d not in self.EXCLUDE_DIRS]
            for fname in files:
                if not fname.endswith(".py"):
                    continue
                filepath = Path(root) / fname
                try:
                    content = filepath.read_text(encoding="utf-8")
                except Exception:
                    continue
                assert "BEGIN PRIVATE KEY" not in content, (
                    f"{filepath.relative_to(REPO_ROOT)} contains a private key"
                )
                assert "BEGIN RSA PRIVATE KEY" not in content, (
                    f"{filepath.relative_to(REPO_ROOT)} contains an RSA private key"
                )


# ---------------------------------------------------------------------------
# 9. No unsafe eval/exec
# ---------------------------------------------------------------------------

class TestNoUnsafeEvalExec:
    """
    The main codebase must not use eval() or exec() on user-controlled input,
    as this leads to arbitrary code execution vulnerabilities.
    """

    EXCLUDE_DIRS = {"__pycache__", "node_modules", ".git", "test_cases", "examples", "example"}
    SAFE_EVAL_MODULES = set()  # Modules where ast.literal_eval is acceptable

    def test_no_bare_eval_calls(self):
        """
        No .py file in logicore/ should contain bare eval() calls
        that could execute arbitrary code.
        """
        violations = []
        for root, dirs, files in os.walk(LOGICORE_DIR):
            dirs[:] = [d for d in dirs if d not in self.EXCLUDE_DIRS]
            for fname in files:
                if not fname.endswith(".py"):
                    continue
                # Skip test files
                if fname.startswith("test_"):
                    continue
                filepath = Path(root) / fname
                try:
                    content = filepath.read_text(encoding="utf-8")
                except Exception:
                    continue
                # Parse AST for precise detection
                try:
                    tree = ast.parse(content)
                except SyntaxError:
                    continue
                for node in ast.walk(tree):
                    if isinstance(node, ast.Call):
                        func = node.func
                        if isinstance(func, ast.Name) and func.id == "eval":
                            violations.append(
                                f"{filepath.relative_to(REPO_ROOT)}:{node.lineno}"
                            )
        if violations:
            msg = "Unsafe eval() calls found:\n" + "\n".join(violations)
            pytest.fail(msg)

    def test_no_bare_exec_calls(self):
        """
        No .py file in logicore/ should contain bare exec() calls.
        """
        violations = []
        for root, dirs, files in os.walk(LOGICORE_DIR):
            dirs[:] = [d for d in dirs if d not in self.EXCLUDE_DIRS]
            for fname in files:
                if not fname.endswith(".py"):
                    continue
                if fname.startswith("test_"):
                    continue
                filepath = Path(root) / fname
                try:
                    content = filepath.read_text(encoding="utf-8")
                except Exception:
                    continue
                try:
                    tree = ast.parse(content)
                except SyntaxError:
                    continue
                for node in ast.walk(tree):
                    if isinstance(node, ast.Call):
                        func = node.func
                        if isinstance(func, ast.Name) and func.id == "exec":
                            violations.append(
                                f"{filepath.relative_to(REPO_ROOT)}:{node.lineno}"
                            )
        if violations:
            msg = "Unsafe exec() calls found:\n" + "\n".join(violations)
            pytest.fail(msg)

    def test_no_compile_eval_pattern(self):
        """
        Detect the compile() + eval() pattern that bypasses basic string checks.
        """
        for root, dirs, files in os.walk(LOGICORE_DIR):
            dirs[:] = [d for d in dirs if d not in self.EXCLUDE_DIRS]
            for fname in files:
                if not fname.endswith(".py") or fname.startswith("test_"):
                    continue
                filepath = Path(root) / fname
                try:
                    content = filepath.read_text(encoding="utf-8")
                except Exception:
                    continue
                if "compile(" in content and "eval(" in content:
                    tree = ast.parse(content)
                    has_compile = any(
                        isinstance(n, ast.Call)
                        and isinstance(n.func, ast.Name)
                        and n.func.id == "compile"
                        for n in ast.walk(tree)
                    )
                    has_eval = any(
                        isinstance(n, ast.Call)
                        and isinstance(n.func, ast.Name)
                        and n.func.id == "eval"
                        for n in ast.walk(tree)
                    )
                    if has_compile and has_eval:
                        pytest.fail(
                            f"{filepath.relative_to(REPO_ROOT)} uses compile() + eval() pattern"
                        )


# ---------------------------------------------------------------------------
# 10. .env not in git
# ---------------------------------------------------------------------------

class TestEnvNotInGit:
    """
    The .env file must be listed in .gitignore to prevent accidental
    commit of secrets and API keys.
    """

    def test_env_in_gitignore(self):
        """
        .env must appear in .gitignore.
        """
        gitignore_path = REPO_ROOT / ".gitignore"
        assert gitignore_path.exists(), ".gitignore must exist"
        content = gitignore_path.read_text(encoding="utf-8")
        lines = [line.strip() for line in content.splitlines()]
        # Check for .env entry (not commented out)
        env_entries = [
            line for line in lines
            if line and not line.startswith("#") and ".env" in line
        ]
        assert env_entries, (
            ".env must be listed in .gitignore (not commented out)"
        )

    def test_env_local_in_gitignore(self):
        """
        .env.local and .env.*.local variants should also be gitignored.
        """
        gitignore_path = REPO_ROOT / ".gitignore"
        content = gitignore_path.read_text(encoding="utf-8")
        assert ".env.local" in content, (
            ".env.local should be in .gitignore"
        )

    def test_env_file_not_in_repository(self):
        """
        .env file should not be tracked by git (if it exists locally).
        """
        env_path = REPO_ROOT / ".env"
        if env_path.exists():
            # Check if git is tracking it
            import subprocess
            result = subprocess.run(
                ["git", "ls-files", "--error-unmatch", ".env"],
                cwd=str(REPO_ROOT),
                capture_output=True,
                text=True,
            )
            assert result.returncode != 0, (
                ".env is tracked by git - remove it from tracking with "
                "`git rm --cached .env`"
            )


# ---------------------------------------------------------------------------
# Additional: SSRF URL scheme restriction
# ---------------------------------------------------------------------------

class TestSSRFSchemeRestriction:
    """
    URL fetching must only allow http/https schemes to prevent
    file://, ftp://, or other protocol-based attacks.
    """

    def test_only_http_https_allowed(self):
        """
        _validate_url_safety must restrict URLs to http and https schemes.
        """
        source = _read_source("tools/web.py")
        assert "_ALLOWED_SCHEMES" in source, (
            "web.py must define _ALLOWED_SCHEMES for scheme validation"
        )
        assert "'http'" in source or '"http"' in source
        assert "'https'" in source or '"https"' in source

    def test_blocked_cloud_metadata_endpoints(self):
        """
        Cloud metadata endpoints (169.254.169.254) must be explicitly blocked.
        """
        source = _read_source("tools/web.py")
        assert "169.254.169.254" in source, (
            "web.py must block AWS/GCP/Azure metadata endpoint"
        )
        assert "metadata.google" in source, (
            "web.py must block GCP metadata endpoint"
        )
        assert "metadata.azure" in source, (
            "web.py must block Azure metadata endpoint"
        )


# ---------------------------------------------------------------------------
# Additional: SQL injection check
# ---------------------------------------------------------------------------

class TestSQLInjection:
    """
    Database queries must use parameterized statements, not string formatting.
    """

    def test_no_string_format_sql(self):
        """
        Scan for SQL queries built with f-strings or .format().
        """
        sql_pattern = re.compile(
            r"""(?:execute|cursor\.execute)\s*\(\s*(?:f['"]|['"].*\.format\()""",
            re.IGNORECASE,
        )
        violations = []
        for root, dirs, files in os.walk(LOGICORE_DIR):
            dirs[:] = [d for d in dirs if d not in TestNoHardcodedSecrets.EXCLUDE_DIRS]
            for fname in files:
                if not fname.endswith(".py"):
                    continue
                filepath = Path(root) / fname
                try:
                    content = filepath.read_text(encoding="utf-8")
                except Exception:
                    continue
                for line_num, line in enumerate(content.splitlines(), 1):
                    if sql_pattern.search(line):
                        violations.append(
                            f"{filepath.relative_to(REPO_ROOT)}:{line_num}"
                        )
        if violations:
            msg = "Possible SQL injection via string formatting:\n" + "\n".join(violations)
            pytest.fail(msg)


# ---------------------------------------------------------------------------
# Additional: Subprocess shell=True check
# ---------------------------------------------------------------------------

class TestSubprocessSecurity:
    """
    subprocess calls must not use shell=True to prevent shell injection.
    """

    def test_no_shell_true_in_main_code(self):
        """
        No subprocess.run or subprocess.Popen call should use shell=True.
        """
        violations = []
        exclude = TestNoHardcodedSecrets.EXCLUDE_DIRS
        for root, dirs, files in os.walk(LOGICORE_DIR):
            dirs[:] = [d for d in dirs if d not in exclude]
            for fname in files:
                if not fname.endswith(".py") or fname.startswith("test_"):
                    continue
                filepath = Path(root) / fname
                try:
                    content = filepath.read_text(encoding="utf-8")
                except Exception:
                    continue
                try:
                    tree = ast.parse(content)
                except SyntaxError:
                    continue
                for node in ast.walk(tree):
                    if isinstance(node, ast.Call):
                        func = node.func
                        is_subprocess = False
                        if isinstance(func, ast.Attribute) and func.attr in ("run", "Popen"):
                            if isinstance(func.value, ast.Name) and func.value.id == "subprocess":
                                is_subprocess = True
                        if is_subprocess:
                            for kw in node.keywords:
                                if kw.arg == "shell" and isinstance(kw.value, ast.Constant):
                                    if kw.value.value is True:
                                        violations.append(
                                            f"{filepath.relative_to(REPO_ROOT)}:{node.lineno}"
                                        )
        if violations:
            msg = "subprocess with shell=True found:\n" + "\n".join(violations)
            pytest.fail(msg)