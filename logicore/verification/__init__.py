"""
Verification module — generic artifact verification framework.

Provides a plug-in architecture for verifying any document/image type
created by agent tools before returning results to the user.

Quick start::

    from logicore.verification import VerificationOrchestrator, VerificationConfig

    config = VerificationConfig(enabled=True, auto_fix=True)
    orch = VerificationOrchestrator(config)

    # Detect artifacts from execution log
    artifacts = orch.detector.detect_from_execution_log(agent.execution_log)

    # Verify all artifacts
    results = await orch.verify_artifacts(artifacts, user_requirements="make a report")

    # Or verify a single file synchronously
    result = orch.verify_single("/path/to/output.pptx")
"""

from logicore.verification.config import VerificationConfig
from logicore.verification.result import Artifact, VerificationIssue, VerificationResult
from logicore.verification.detector import ArtifactDetector
from logicore.verification.base_verifier import BaseVerifier
from logicore.verification.orchestrator import VerificationOrchestrator
from logicore.verification.verifiers import VerifierRegistry, get_registry

__all__ = [
    "VerificationConfig",
    "Artifact",
    "VerificationIssue",
    "VerificationResult",
    "ArtifactDetector",
    "BaseVerifier",
    "VerificationOrchestrator",
    "VerifierRegistry",
    "get_registry",
]
