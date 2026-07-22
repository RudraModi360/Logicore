"""
Verification orchestrator.

Coordinates the full verification pipeline:
detect artifacts -> route to verifier -> run checks -> aggregate results.
"""

from __future__ import annotations

import asyncio
import logging
from typing import List, Optional

from logicore.verification.config import VerificationConfig
from logicore.verification.detector import ArtifactDetector
from logicore.verification.result import Artifact, VerificationResult
from logicore.verification.verifiers import VerifierRegistry
from logicore.verification.auto_fix import AutoFixEngine

logger = logging.getLogger(__name__)


class VerificationOrchestrator:
    """Coordinates artifact detection, verification, and result aggregation.

    Usage::

        config = VerificationConfig(enabled=True, auto_fix=True)
        orch = VerificationOrchestrator(config)

        artifacts = orch.detector.detect_from_execution_log(agent.execution_log)
        results = await orch.verify_artifacts(artifacts, user_requirements="make a PPT")
    """

    def __init__(self, config: Optional[VerificationConfig] = None) -> None:
        self.config = config or VerificationConfig()
        self.detector = ArtifactDetector()
        self.registry = VerifierRegistry()
        self.auto_fix = AutoFixEngine()

    async def verify_artifacts(
        self,
        artifacts: List[Artifact],
        *,
        user_requirements: Optional[str] = None,
        strict: Optional[bool] = None,
    ) -> List[VerificationResult]:
        """Verify a list of artifacts.

        Args:
            artifacts: Detected artifacts to verify.
            user_requirements: Original user request for context.
            strict: Override config.strict_mode for this run.

        Returns:
            List of VerificationResult, one per artifact.
        """
        if not artifacts:
            return []

        use_strict = strict if strict is not None else self.config.strict_mode
        results: List[VerificationResult] = []

        for artifact in artifacts:
            # Skip if too large.
            if self._should_skip(artifact):
                results.append(VerificationResult(
                    artifact_path=artifact.path,
                    artifact_type=artifact.artifact_type,
                    passed=True,
                    confidence=1.0,
                    skipped=True,
                    skip_reason=(
                        f"File size ({artifact.size_mb:.1f} MB) exceeds "
                        f"threshold ({self.config.skip_for_large_files_mb} MB)"
                    ),
                ))
                continue

            # Route to verifier.
            verifier = self.registry.get_verifier(artifact.artifact_type)

            # Run verification (with timeout).
            try:
                result = await asyncio.wait_for(
                    asyncio.get_event_loop().run_in_executor(
                        None,
                        verifier.verify,
                        artifact.path,
                        user_requirements,
                    ),
                    timeout=self.config.max_verification_time_ms / 1000,
                )
            except asyncio.TimeoutError:
                result = VerificationResult(
                    artifact_path=artifact.path,
                    artifact_type=artifact.artifact_type,
                    passed=False,
                    confidence=0.0,
                    skipped=True,
                    skip_reason=f"Verification timed out after {self.config.max_verification_time_ms}ms",
                )

            # Apply strict mode if needed.
            if use_strict and not result.passed:
                # In strict mode, warnings also count as failures.
                has_warnings = any(i.severity == "warning" for i in result.issues)
                if has_warnings:
                    result.passed = False

            # Apply auto-fix if enabled and result has issues.
            if self.config.auto_fix and not result.passed and not result.skipped:
                result = self.auto_fix.fix(result)

            results.append(result)

        return results

    def verify_single(
        self,
        artifact_path: str,
        *,
        user_requirements: Optional[str] = None,
        strict: Optional[bool] = None,
    ) -> VerificationResult:
        """Synchronous convenience: verify a single artifact by path.

        Useful for testing or one-off checks.
        """
        import os

        ext = os.path.splitext(artifact_path)[1].lstrip(".").lower()
        size = 0
        if os.path.isfile(artifact_path):
            try:
                size = os.path.getsize(artifact_path)
            except OSError:
                pass

        artifact = Artifact(path=artifact_path, artifact_type=ext, size_bytes=size)
        verifier = self.registry.get_verifier(ext)

        if self._should_skip(artifact):
            return VerificationResult(
                artifact_path=artifact.path,
                artifact_type=artifact.artifact_type,
                passed=True,
                confidence=1.0,
                skipped=True,
                skip_reason=(
                    f"File size ({artifact.size_mb:.1f} MB) exceeds "
                    f"threshold ({self.config.skip_for_large_files_mb} MB)"
                ),
            )

        result = verifier.verify(artifact.path, user_requirements)

        use_strict = strict if strict is not None else self.config.strict_mode
        if use_strict and not result.passed:
            has_warnings = any(i.severity == "warning" for i in result.issues)
            if has_warnings:
                result.passed = False

        # Apply auto-fix if enabled and result has issues.
        if self.config.auto_fix and not result.passed and not result.skipped:
            result = self.auto_fix.fix(result)

        return result

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _should_skip(self, artifact: Artifact) -> bool:
        """Check if verification should be skipped for this artifact."""
        if not self.config.enabled:
            return True
        if self.config.skip_for_large_files_mb > 0 and artifact.size_mb > self.config.skip_for_large_files_mb:
            return True
        return False
