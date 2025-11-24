#!/usr/bin/env python3
"""Test improved code detection logic."""

import re


def _detect_code_patterns(text: str) -> bool:
    """Detect code snippets in message (multi-line code blocks, not single words).

    This looks for actual programming code patterns:
    - Code fence markers (```, ~~~)
    - Consistent indentation (4+ spaces/tabs)
    - Programming language syntax (function, class, def, const, import, etc.)

    Requires at least 2-3 lines to avoid false positives on abbreviations like "EVM", "API".
    """
    if not text:
        return False

    lines = text.split("\n")

    # 1. Code fence markers (markdown code blocks)
    if re.search(r"```|~~~", text):
        return True

    # 2. Consistent indentation pattern (4+ spaces or tabs, at least 3 lines)
    indented_lines = [line for line in lines if re.match(r"^(    |\t)", line)]
    if len(indented_lines) >= 3:
        return True

    # 3. Programming syntax patterns (must have at least 2 lines + syntax keyword)
    if len(lines) >= 2:
        # Common programming keywords across languages
        programming_keywords = [
            r"\bfunction\s+\w+\s*\(",  # function declarations
            r"\bclass\s+\w+",  # class definitions
            r"\bdef\s+\w+\s*\(",  # Python functions
            r"\b(const|let|var)\s+\w+\s*=",  # JS/TS variables
            r"\bimport\s+\w+",  # imports
            r"\bfrom\s+\w+\s+import",  # Python imports
            r"\bpub\s+fn\s+\w+",  # Rust functions
            r"\bfunc\s+\w+\s*\(",  # Go functions
            r"\breturn\s+[^;]+;",  # return statements
            r"=>\s*\{",  # arrow functions
            r"\{\s*$.*\}\s*$",  # code blocks (multiline)
        ]

        text_lower = text.lower()
        for pattern in programming_keywords:
            if re.search(pattern, text, re.IGNORECASE | re.MULTILINE):
                return True

    return False


# Test cases
test_cases = [
    # Should NOT trigger (false positives we want to avoid)
    ("EVM", False, "Single word abbreviation"),
    ("API", False, "Single word abbreviation"),
    ("There's only one address on EVM chains: 0x1234", False, "EVM in sentence"),
    ("Check the token contract", False, "Single line with 'token'"),
    ("OTP: 123456", False, "OTP code in one line"),
    # Should trigger (actual code)
    ("```python\nprint('hello')\n```", True, "Code fence"),
    ("~~~\nconst x = 5;\n~~~", True, "Code fence with tildes"),
    (
        """function getData() {
    return fetch('/api')
}""",
        True,
        "JavaScript function",
    ),
    (
        """def process_message(msg):
    if msg.text:
        return msg.text
    return None""",
        True,
        "Python function with indentation",
    ),
    (
        """    line1
    line2
    line3
    line4""",
        True,
        "Consistent indentation (4+ lines)",
    ),
    (
        "const API_KEY = 'abc123';\nconst BASE_URL = 'https://api.example.com';",
        True,
        "Multiple const declarations",
    ),
    (
        "import React from 'react';\nimport { useState } from 'react';",
        True,
        "Import statements",
    ),
    ("class User {\n  constructor(name) {}\n}", True, "Class definition"),
]

print("Testing improved code detection logic:\n")
passed = 0
failed = 0

for text, expected, description in test_cases:
    result = _detect_code_patterns(text)
    status = "✅ PASS" if result == expected else "❌ FAIL"
    if result == expected:
        passed += 1
    else:
        failed += 1

    print(f"{status} - {description}")
    if result != expected:
        print(f"   Text: {repr(text[:50])}")
        print(f"   Expected: {expected}, Got: {result}")
    print()

print(f"\nResults: {passed} passed, {failed} failed out of {len(test_cases)} tests")
