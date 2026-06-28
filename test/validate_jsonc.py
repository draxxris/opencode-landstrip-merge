#!/usr/bin/env python3
"""Validate that a file parses as JSONC: tolerates // and /* */ comments and
trailing commas, while respecting strings (so a // inside "https://..." is NOT
treated as a comment). Exits 0 if valid, raises (nonzero) otherwise."""
import json
import re
import sys

t = open(sys.argv[1]).read()
out = []
i, n = 0, len(t)
while i < n:
    c = t[i]
    if c == '"':  # copy string literals verbatim (incl. any // inside them)
        out.append(c)
        i += 1
        while i < n:
            out.append(t[i])
            if t[i] == '\\' and i + 1 < n:
                out.append(t[i + 1])
                i += 2
                continue
            if t[i] == '"':
                i += 1
                break
            i += 1
        continue
    if c == '/' and i + 1 < n and t[i + 1] == '/':  # line comment
        while i < n and t[i] != '\n':
            i += 1
        continue
    if c == '/' and i + 1 < n and t[i + 1] == '*':  # block comment
        i += 2
        while i + 1 < n and not (t[i] == '*' and t[i + 1] == '/'):
            i += 1
        i += 2
        continue
    out.append(c)
    i += 1

clean = re.sub(r',(\s*[}\]])', r'\1', ''.join(out))  # tolerate trailing commas
json.loads(clean)  # raises on invalid JSON
