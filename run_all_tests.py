"""
Logicore v1.0.3 - Production Validation Test Runner

Single command to run all tests and generate a comprehensive report.
Usage:
    python run_all_tests.py                    # Run ALL tests (tests/ + test_cases/)
    python run_all_tests.py --unit             # Run only unit tests
    python run_all_tests.py --validation       # Run only validation tests
    python run_all_tests.py --security         # Run security fix tests
    python run_all_tests.py --dead-code        # Run dead code detection
    python run_all_tests.py --readiness        # Run production readiness tests
    python run_all_tests.py --post-fix         # Run post-fix validation
    python run_all_tests.py --integration      # Run integration tests
    python run_all_tests.py --report           # Generate HTML report
    python run_all_tests.py --verbose          # Verbose output
"""

import subprocess
import sys
import os
from datetime import datetime
from pathlib import Path


TEST_SUITES = {
    "unit": {
        "name": "Unit Tests",
        "path": "tests/unit",
        "description": "Fast, isolated tests (no I/O)",
        "tests": ["test_tool_registry.py", "test_web_tools.py", "test_prompts.py"],
    },
    "validation": {
        "name": "Production Validation",
        "path": "tests/validation",
        "description": "Import health, config, agent creation, tool availability",
        "tests": ["test_import_health.py", "test_config_health.py",
                  "test_agent_creation.py", "test_tool_availability.py"],
    },
    "security": {
        "name": "Security Tests",
        "path": "test_cases/security",
        "description": "Command injection, path traversal, SSRF, XSS, eval/exec removal",
        "tests": ["test_security_fixes.py"],
    },
    "dead-code": {
        "name": "Dead Code Detection",
        "path": "test_cases/dead_code",
        "description": "Heuristic duplicate function/class/import detection",
        "tests": ["test_duplicate_detection.py"],
    },
    "readiness": {
        "name": "Production Readiness",
        "path": "test_cases/production_readiness",
        "description": "Imports, tool registry, agent creation, config, token estimator",
        "tests": ["test_readiness.py"],
    },
    "post-fix": {
        "name": "Post-Fix Validation",
        "path": "test_cases/validation",
        "description": "Verifies all security fixes were applied correctly",
        "tests": ["test_fixes_applied.py"],
    },
    "integration": {
        "name": "Integration Tests",
        "path": "test_cases/integration",
        "description": "End-to-end tool execution, agent chat, security validation",
        "tests": ["test_integration.py"],
    },
}


def print_summary():
    """Print test suite summary."""
    print("\n" + "=" * 60)
    print("TEST SUITE SUMMARY")
    print("=" * 60)
    for suite_id, suite in TEST_SUITES.items():
        print(f"\n  {suite['name']} ({suite_id})")
        print(f"    {suite['description']}")
        print(f"    Path: {suite['path']}")
        for test in suite["tests"]:
            print(f"      - {test}")
    print("\n" + "=" * 60)


def resolve_test_paths(test_type, project_root):
    """Resolve which directories/files to run based on test_type."""
    paths = []

    if test_type == "all":
        # Run everything: tests/ and test_cases/
        for suite_id, suite in TEST_SUITES.items():
            full_path = project_root / suite["path"]
            if full_path.exists():
                paths.append(full_path)
        return paths

    # Map CLI flags to suite IDs
    suite_map = {
        "unit": ["unit"],
        "validation": ["validation"],
        "security": ["security"],
        "dead-code": ["dead-code"],
        "readiness": ["readiness"],
        "post-fix": ["post-fix"],
        "integration": ["integration"],
    }

    for sid in suite_map.get(test_type, []):
        if sid in TEST_SUITES:
            full_path = project_root / TEST_SUITES[sid]["path"]
            if full_path.exists():
                paths.append(full_path)

    return paths


def run_tests(test_type="all", verbose=False, generate_report=False):
    """Run pytest with specified options."""

    project_root = Path(__file__).parent

    # Resolve test paths
    test_paths = resolve_test_paths(test_type, project_root)
    if not test_paths:
        print(f"\nNo test paths found for type: {test_type}")
        return 1

    # Build pytest command
    cmd = [sys.executable, "-m", "pytest"]
    for p in test_paths:
        cmd.append(str(p))

    # Output options
    cmd.append("-v")
    cmd.append("--tb=short")
    cmd.append("--color=yes")

    # Report generation
    html_report = None
    junit_report = None
    if generate_report:
        report_dir = project_root / "test_reports"
        report_dir.mkdir(exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        html_report = report_dir / f"report_{timestamp}.html"
        junit_report = report_dir / f"report_{timestamp}.xml"
        cmd.extend([f"--html={html_report}", "--self-contained-html"])
        cmd.extend([f"--junitxml={junit_report}"])

    print(f"\n{'=' * 60}")
    print(f"Logicore v1.0.3 - Production Validation")
    print(f"{'=' * 60}")
    print(f"Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"Test Type: {test_type.upper()}")
    print(f"Paths: {', '.join(str(p) for p in test_paths)}")
    print(f"{'=' * 60}\n")

    print_summary()

    print(f"\nRunning: pytest {test_type} tests...\n")

    result = subprocess.run(cmd, cwd=str(project_root))

    print(f"\n{'=' * 60}")
    print(f"TEST RUN COMPLETE")
    print(f"{'=' * 60}")
    print(f"Exit Code: {result.returncode}")
    print(f"Status: {'PASSED' if result.returncode == 0 else 'FAILED'}")
    if generate_report and html_report:
        print(f"HTML Report: {html_report}")
        print(f"JUnit Report: {junit_report}")
    print(f"{'=' * 60}\n")

    return result.returncode


def main():
    """Main entry point."""
    import argparse

    parser = argparse.ArgumentParser(
        description="Logicore v1.0.3 Test Runner",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python run_all_tests.py                 # Run ALL tests
  python run_all_tests.py --unit          # Run only unit tests
  python run_all_tests.py --security      # Run security fix tests
  python run_all_tests.py --dead-code     # Run dead code detection
  python run_all_tests.py --readiness     # Run production readiness tests
  python run_all_tests.py --post-fix      # Run post-fix validation
  python run_all_tests.py --integration   # Run integration tests
  python run_all_tests.py --report        # Generate HTML report
        """,
    )
    parser.add_argument("--unit", action="store_true", help="Run only unit tests")
    parser.add_argument("--integration", action="store_true", help="Run only integration tests")
    parser.add_argument("--validation", action="store_true", help="Run only validation tests")
    parser.add_argument("--security", action="store_true", help="Run security fix tests")
    parser.add_argument("--dead-code", action="store_true", help="Run dead code detection tests")
    parser.add_argument("--readiness", action="store_true", help="Run production readiness tests")
    parser.add_argument("--post-fix", action="store_true", help="Run post-fix validation tests")
    parser.add_argument("--report", action="store_true", help="Generate HTML and JUnit reports")
    parser.add_argument("--verbose", action="store_true", help="Verbose output")

    args = parser.parse_args()

    # Determine test type (first matching flag wins)
    if args.unit:
        test_type = "unit"
    elif args.integration:
        test_type = "integration"
    elif args.validation:
        test_type = "validation"
    elif args.security:
        test_type = "security"
    elif args.dead_code:
        test_type = "dead-code"
    elif args.readiness:
        test_type = "readiness"
    elif args.post_fix:
        test_type = "post-fix"
    else:
        test_type = "all"

    exit_code = run_tests(
        test_type=test_type,
        verbose=args.verbose,
        generate_report=args.report,
    )

    sys.exit(exit_code)


if __name__ == "__main__":
    main()
