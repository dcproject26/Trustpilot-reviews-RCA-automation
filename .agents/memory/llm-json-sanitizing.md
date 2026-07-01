---
name: LLM JSON sanitizing
description: Why _strip_fences in claude.py must be JSON-string-aware, not regex
---

# LLM JSON sanitizing (server/services/claude.py `_strip_fences`)

Rule: when cleaning LLM JSON output (stripping `//` / `/* */` comments and trailing
commas), the sanitizer MUST track string state and only edit OUTSIDE string literals.
Do NOT use naive regex (`re.sub(r'//[^\n]*','',t)` etc.).

**Why:** RCA payloads contain URLs (`https://...`) and free text with slashes/commas
inside string values. Regex comment-stripping truncates a URL at `//` and can drop
commas inside strings, silently corrupting valid JSON. Caught in code review.

**How to apply:** Keep the char-by-char state-machine helpers
(`_strip_json_comments`, `_strip_trailing_commas`) that respect `"` and `\` escapes.
If extending cleanup, add cases to those scanners — never fall back to regex.
