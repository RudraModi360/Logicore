"""
Fix implementations for different artifact types.
"""

from logicore.verification.auto_fix.fixes.image_fixes import register_fixes as register_image_fixes
from logicore.verification.auto_fix.fixes.pptx_fixes import register_fixes as register_pptx_fixes
from logicore.verification.auto_fix.fixes.docx_fixes import register_fixes as register_docx_fixes
from logicore.verification.auto_fix.fixes.xlsx_fixes import register_fixes as register_xlsx_fixes
from logicore.verification.auto_fix.fixes.html_fixes import register_fixes as register_html_fixes

__all__ = [
    "register_image_fixes",
    "register_pptx_fixes",
    "register_docx_fixes",
    "register_xlsx_fixes",
    "register_html_fixes",
]
