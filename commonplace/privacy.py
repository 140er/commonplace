"""Fast, deterministic flags for sensitive or instruction-like text.

These heuristics report signals only; callers retain the original text.
"""

import re

_TOKEN_EDGE = r"A-Za-z0-9_\-"
_PATTERNS = {
    "secret:aws_key": re.compile(r"(?<![A-Z0-9])(?:AKIA|ASIA)[A-Z0-9]{16}(?![A-Z0-9])"),
    "secret:github_token": re.compile(
        rf"(?<![{_TOKEN_EDGE}])(?:gh[pousr]_[A-Za-z0-9]{{36,255}}|github_pat_[A-Za-z0-9_]{{22,255}})(?![{_TOKEN_EDGE}])"
    ),
    "secret:openai_key": re.compile(
        rf"(?<![{_TOKEN_EDGE}])sk-(?!ant-)[A-Za-z0-9_-]{{20,}}(?![{_TOKEN_EDGE}])"
    ),
    "secret:anthropic_key": re.compile(
        rf"(?<![{_TOKEN_EDGE}])sk-ant-[A-Za-z0-9_-]{{20,}}(?![{_TOKEN_EDGE}])"
    ),
    "secret:cmmn_token": re.compile(
        rf"(?<![{_TOKEN_EDGE}])cmmn_[{_TOKEN_EDGE}]{{43}}(?![{_TOKEN_EDGE}])"
    ),
    "secret:slack_token": re.compile(
        rf"(?<![{_TOKEN_EDGE}])xox[baprs]-[A-Za-z0-9-]{{10,}}(?![{_TOKEN_EDGE}])"
    ),
    "secret:private_key": re.compile(r"-----BEGIN (?:[A-Z0-9]+ )*PRIVATE KEY-----"),
    # A JWT header is a base64url JSON object (normally starts with eyJ).
    "secret:jwt": re.compile(
        rf"(?<![{_TOKEN_EDGE}.])eyJ[{_TOKEN_EDGE}]{{5,}}\.[{_TOKEN_EDGE}]{{8,}}\.[{_TOKEN_EDGE}]{{8,}}(?![{_TOKEN_EDGE}.])"
    ),
    "secret:password_url": re.compile(r"\b[a-zA-Z][a-zA-Z0-9+.-]*://[^\s/:@]+:[^\s/@]+@[^\s/:@]+"),
    "injection:ignore_instructions": re.compile(
        r"\b(?:ignore\s+(?:all\s+)?previous\s+instructions|"
        r"negeer\s+(?:alle\s+)?(?:eerdere|vorige)\s+instructies)\b",
        re.IGNORECASE,
    ),
    "injection:tool_syntax": re.compile(
        r'<tool_call\s*>|["\']function_call["\']\s*:\s*\{|^[ \t]*assistant\s*:',
        re.IGNORECASE | re.MULTILINE,
    ),
    "injection:curl_pipe": re.compile(
        r"\b(?:curl|wget)\b[^\n|]{0,2048}\|[ \t]*(?:sh|bash)\b", re.IGNORECASE
    ),
    "injection:base64_blob": re.compile(
        r"(?<![A-Za-z0-9+/=])[A-Za-z0-9+/]{200,}={0,2}(?![A-Za-z0-9+/=])"
    ),
    "injection:hidden_unicode": re.compile(
        r"[\u200b-\u200f\u202a-\u202e\u2060\u2066-\u2069\ufeff\U000e0000-\U000e007f]"
    ),
    "injection:image_exfil": re.compile(r"!\[[^\]\n]*\]\([^\s)]+\?[^\s)]*\)"),
}

_ASSIGNMENT = re.compile(
    r"\b(?:api[_-]?key|password|secret|token)\s*=\s*[\"']?([A-Za-z0-9_+/=-]{16,})(?![A-Za-z0-9_+/=-])",
    re.IGNORECASE,
)
_STRONG_HEALTH = re.compile(
    r"\b(?:diagnosis|diagnose|medication|medicatie|symptoms?|symptomen|"
    r"huisarts|hospital|ziekenhuis|therapy|therapie|blood\s+pressure|bloeddruk|"
    r"prescription|depression|depressie|anxiety|angststoornis|bipolar|"
    r"schizophrenia|schizofrenie|ptsd|ptss|diabetes|cancer|kanker|"
    r"mental\s+health|geestelijke\s+gezondheid)\b",
    re.IGNORECASE,
)
_WEAK_HEALTH = re.compile(
    r"\b(?:doctor|arts|recept|patient|patiënt|patienten|medicine|medicijn)\b", re.IGNORECASE
)


# Cheap literal gates avoid running expensive regexes on unrelated prose.
_GATES = {
    "secret:aws_key": ("akia", "asia"),
    "secret:github_token": ("ghp_", "gho_", "ghu_", "ghs_", "ghr_", "github_pat_"),
    "secret:openai_key": ("sk-",),
    "secret:anthropic_key": ("sk-ant-",),
    "secret:cmmn_token": ("cmmn_",),
    "secret:slack_token": ("xox",),
    "secret:jwt": ("eyj",),
    "secret:password_url": ("://",),
    "injection:ignore_instructions": ("ignore", "negeer"),
    "injection:tool_syntax": ("tool_call", "function_call", "assistant"),
    "injection:curl_pipe": ("curl", "wget"),
}
_HEALTH_HINTS = (
    "diagnos",
    "medic",
    "symptom",
    "huisarts",
    "hospital",
    "ziekenhuis",
    "therap",
    "blood",
    "bloeddruk",
    "prescription",
    "depress",
    "anxiety",
    "angststoornis",
    "bipolar",
    "schiz",
    "ptsd",
    "ptss",
    "diabetes",
    "cancer",
    "kanker",
    "mental",
    "geestelijke",
    "doctor",
    "arts",
    "recept",
    "patient",
    "patiënt",
)


def scan(text: str) -> list[str]:
    """Return sorted, unique flags without changing or transmitting text."""
    lowered = text.lower()
    flags = {
        flag
        for flag, pattern in _PATTERNS.items()
        if (flag not in _GATES or any(hint in lowered for hint in _GATES[flag]))
        and pattern.search(text)
    }
    for match in _ASSIGNMENT.finditer(text):
        value = match.group(1)
        # Require variety; hexadecimal secrets need only letters and digits.
        # Length alone would flag repeated characters and common placeholders.
        classes = sum(
            bool(re.search(pattern, value))
            for pattern in (r"[a-z]", r"[A-Z]", r"[0-9]", r"[_+/=-]")
        )
        varied_hex = (
            re.fullmatch(r"[0-9a-fA-F]+", value) is not None
            and re.search(r"[0-9]", value) is not None
            and re.search(r"[a-fA-F]", value) is not None
            and len(set(value.lower())) >= 8
        )
        if (classes >= 3 and len(set(value)) >= 8) or varied_hex:
            flags.add("secret:generic_assignment")
            break
    if (
        _STRONG_HEALTH.search(text)
        or len({m.group().casefold() for m in _WEAK_HEALTH.finditer(text)}) >= 2
    ):
        flags.add("health")
    return sorted(flags)


def has_secret(flags: list[str]) -> bool:
    return any(flag.startswith("secret:") for flag in flags)


def is_health(flags: list[str]) -> bool:
    return "health" in flags
