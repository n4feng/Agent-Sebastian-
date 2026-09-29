"""Local deterministic screening. Every cloud request also requires human review."""
import base64
import math
import re
from collections import Counter
from dataclasses import dataclass
from urllib.parse import unquote

SECRET_PATTERNS = [
    ("private key", r"-----BEGIN (?:[A-Z0-9 ]+ )?PRIVATE KEY-----"),
    ("provider API key", r"\bsk-(?:proj-|ant-[a-z0-9]+-)?[A-Za-z0-9_-]{16,}"),
    ("GitHub token", r"\b(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,})"),
    ("AWS access key", r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b"),
    ("Google API key", r"\bAIza[A-Za-z0-9_-]{30,}"),
    ("Slack token", r"\bxox[baprs]-[A-Za-z0-9-]{15,}"),
    ("credential assignment", r"(?i)(?:api[_ -]?key|password|passwd|secret|access[_ -]?token)\s*[=:]\s*[\"']?[^\s\"',;}]{8,}"),
    ("authorization header", r"(?i)\b(?:bearer|basic)\s+[A-Za-z0-9+/_.=-]{12,}"),
    ("session cookie", r"(?i)(?:sessionid|session_token|auth_token)\s*=\s*[^;\s]{12,}"),
    ("JSON web token", r"\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+"),
]


@dataclass(frozen=True)
class Finding:
    decision: str
    reasons: tuple[str, ...]


def inspect(value, *, known_secrets=(), restricted=False, confidential=False):
    variants = [value, unquote(value)]
    # Bounded decoding catches common accidental encodings, not arbitrary obfuscation.
    for token in re.findall(r"[A-Za-z0-9+/=_-]{24,}", value)[:100]:
        try:
            decoded = base64.b64decode(token + "=" * (-len(token) % 4), altchars=b"-_").decode("utf-8")
            if decoded.isprintable():
                variants.append(decoded)
        except (ValueError, UnicodeError):
            pass
    joined = "\n".join(variants)
    reasons = [name for name, pattern in SECRET_PATTERNS if re.search(pattern, joined)]
    if any(key and len(key) >= 8 and key in joined for key in known_secrets):
        reasons.append("configured credential")
    if restricted or "[NO_EXPORT]" in joined:
        reasons.append("source prohibits export")
    if reasons:
        return Finding("block", tuple(sorted(set(reasons))))
    risks = []
    if confidential or re.search(r"(?i)\b(confidential|nda|non[- ]disclosure|trade secret|proprietary|internal only)\b", joined):
        risks.append("confidential or NDA material")
    if re.search(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}", joined):
        risks.append("personal/contact information")
    for token in re.findall(r"\b[A-Za-z0-9+/=_-]{32,}\b", value):
        probabilities = [n / len(token) for n in Counter(token).values()]
        if -sum(p * math.log2(p) for p in probabilities) > 4.5:
            risks.append("possible encoded secret or opaque identifier")
            break
    return Finding("review", tuple(sorted(set(risks))) or ("unclassified text requires human export review",))
