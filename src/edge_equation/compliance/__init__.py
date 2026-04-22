"""
Compliance: public-mode sanitization, mandatory disclaimer injection, and
a compliance_test() guard that fails any output containing forbidden
gambling language or un-sanitized edge / Kelly leaks.

Phase 1 brand rule: X posts are analytical-only. Zero betting jargon, no
Kelly, no edge percentages, no "pick of the day" language. The sanitizer
strips those fields from card dicts; the disclaimer helper tacks the
mandatory brand line onto every payload; the checker verifies the final
string before it ships.

Usage:
    from edge_equation.compliance import (
        PublicModeSanitizer, DISCLAIMER_TEXT, inject_disclaimer,
        compliance_test,
    )
"""
from edge_equation.compliance.disclaimer import (
    DISCLAIMER_TEXT,
    inject_disclaimer,
)
from edge_equation.compliance.sanitizer import PublicModeSanitizer
from edge_equation.compliance.checker import (
    ComplianceReport,
    compliance_test,
    FORBIDDEN_TERMS,
    LEDGER_FOOTER_RE,
)


__all__ = [
    "PublicModeSanitizer",
    "DISCLAIMER_TEXT",
    "inject_disclaimer",
    "ComplianceReport",
    "compliance_test",
    "FORBIDDEN_TERMS",
    "LEDGER_FOOTER_RE",
]
