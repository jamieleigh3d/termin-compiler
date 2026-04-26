"""One-shot migration helper: v0.8 top-level identity lines → v0.9 Identity: block.

Walks each input file, finds runs of v0.8 identity lines:
    Users authenticate with <X>
    Scopes are <list>
    A "<role>" has <list>            (zero or more)
    An "<role>" has <list>           (zero or more)
    An "anonymous" has <list>        (zero or more)
    Anonymous has <list>             (zero or more)

…and replaces them with a v0.9 Identity: block:
    Identity:
      Scopes are <list>
      <each role line, indented; An "anonymous" → Anonymous>

Drops the `Users authenticate with X` line entirely (provider lives in
deploy config in v0.9). Preserves any indent the v0.8 lines had so the
helper works for both bare-string DSL test fixtures (column 0) and
nested-string fixtures (with leading whitespace).

Usage:
    python util/migrate_v09_identity.py <file> [<file> ...]

This script is idempotent — running it twice on a migrated file is a no-op.
"""

import re
import sys
from pathlib import Path

_AUTHENTICATE_RE = re.compile(r"^(?P<indent>\s*)Users authenticate with .+$")
_IDENTITY_LINE_RE = re.compile(
    r'^(?P<indent>\s*)(?:Scopes are |A "|An "|Anonymous has )'
)


def migrate_text(text: str) -> str:
    lines = text.splitlines(keepends=False)
    out = []
    i = 0
    n = len(lines)
    while i < n:
        line = lines[i]
        m = _AUTHENTICATE_RE.match(line)
        if not m:
            out.append(line)
            i += 1
            continue
        indent = m.group("indent")
        # Collect subsequent identity-related lines that share the same indent.
        j = i + 1
        captured: list[str] = []
        while j < n:
            nxt = lines[j]
            if not nxt.strip():
                # Blank line — peek ahead. If the next non-blank is still
                # identity-related at the same indent, keep collecting;
                # else stop here.
                k = j + 1
                while k < n and not lines[k].strip():
                    k += 1
                if k < n and _IDENTITY_LINE_RE.match(lines[k]) and lines[k].startswith(indent):
                    j = k
                    continue
                break
            im = _IDENTITY_LINE_RE.match(nxt)
            if not im or im.group("indent") != indent:
                break
            captured.append(nxt[len(indent):])
            j += 1
        # Emit the v0.9 block.
        out.append(f"{indent}Identity:")
        for content in captured:
            content = re.sub(r'^An "anonymous" has ', "Anonymous has ", content)
            out.append(f"{indent}  {content}")
        i = j

    result = "\n".join(out)
    if text.endswith("\n"):
        result += "\n"
    return result


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)
    changed = 0
    for fp in sys.argv[1:]:
        p = Path(fp)
        txt = p.read_text(encoding="utf-8")
        new = migrate_text(txt)
        if new != txt:
            p.write_text(new, encoding="utf-8")
            print(f"migrated: {fp}")
            changed += 1
    print(f"{changed} file(s) modified")


if __name__ == "__main__":
    main()
