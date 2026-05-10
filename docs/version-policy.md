# Termin Version Policy

**Version:** 0.9.4-draft (synthesized 2026-05-09)
**Companion to:** [`RELEASE_PROCESS.md`](../RELEASE_PROCESS.md) (which covers
*how* to release; this doc covers *where versions live and why*).

The Termin family ships across five repositories with two independent
version tracks. Without a clear source-of-truth convention for each track,
version literals proliferate across files and the release script
inevitably misses some — leaving the released artifact internally
inconsistent. v0.9.3 shipped with exactly that drift: the package version
lived in 14+ places across the repos, `release.py` knew about 6 of them,
the rest fell out of sync silently. This policy fixes the structural
cause.

The rule, stated once: **every version string is either canonical or
imported. There is no third option.**

---

## 1. The Two Version Tracks

| Track | What it pins | Bumps when | Canonical home |
|---|---|---|---|
| **IR version** | The wire format / IR shape (compiled `.termin.pkg` content) | The IR shape changes (additive = patch, breaking = minor pre-v1.0) | `termin-core/termin_core/ir/types.py` → `AppSpec.ir_version` |
| **Package version** | The implementation release (`pip install termin-core==X.Y.Z`) | Every release ships at the family-aligned version | `<package>/<package_name>/__init__.py` → `__version__` |

**Coupling rules:**

- An IR bump implies a package bump (the new IR shape ships in a release).
- A package bump does NOT imply an IR bump (v0.9.3 was an internal API
  release: package bumped 0.9.2 → 0.9.3, IR stayed at 0.9.2).
- Pre-v1.0 family alignment: the four Python packages
  (`termin-core`, `termin-server`, `termin-compiler`, `termin-spectrum-provider`)
  + the npm bundle (`termin-spectrum-provider/package.json`) all ship at
  the same package version on every release. The conformance repo has
  no Python package; its tag matches the family version.

**Why two tracks:** an alt-runtime author wants to know "what IR shape
can I consume" (IR version) and "what release of `termin-core` am I
linking against" (package version) as two separate questions. Conflating
them either forces an IR bump on every release (noisy churn for runtime
implementers) or hides package changes behind an unchanged IR version
(silent breakage for consumers).

---

## 2. Source-of-Truth Conventions

### 2.1 Package version

**Canonical:** `<package>/<package_name>/__init__.py` declares
`__version__ = "X.Y.Z"` once. **`release.py` only bumps this file
per package.**

**Everywhere else that needs the package version imports it:**

```python
# termin_server/routes.py — runtime_version reflection
from termin_server import __version__
return {"runtime_version": __version__, ...}

# termin_server/providers/builtins/compute_default_cel.py — provider record
from termin_server import __version__
ProviderRecord(category=..., contract=..., product=..., version=__version__, ...)

# termin-server/tests/test_integration.py — test assertion
from termin_server import __version__
assert body.get("runtime_version") == __version__
```

**Build artifacts that can't import Python** (e.g. `pyproject.toml`,
`setup.py`, npm `package.json`) read the canonical value via build
tooling:

- `pyproject.toml` / `setup.py`: use setuptools dynamic version
  (`[tool.setuptools.dynamic] version = { attr = "termin_core.__version__" }`).
  This collapses the wheel's version metadata to the same source of
  truth.
- `package.json`: kept manually in sync. The npm ecosystem doesn't
  natively read from a Python source. `release.py` keeps `package.json`
  as one of its explicit bump targets — the only one that's not
  derived. (Future: a prebuild script that writes the version into
  `package.json` from `pyproject.toml`.)

**Anti-patterns to flag in code review:**

- `version="0.9.2"` as a kwarg literal anywhere in source code.
- `assert ir_version == "0.9.2"` in a test (use the imported value
  or read from a runtime endpoint).
- A new file declares its own `__version__` instead of importing.
- A copied-from-StackOverflow snippet that hardcodes a version.

### 2.2 IR version

**Canonical:** `termin-core/termin_core/ir/types.py` →
`AppSpec.ir_version: str = "X.Y.Z"`.

**One additional authoritative declaration:**
`termin-compiler/docs/termin-ir-schema.json` carries
`"const": "X.Y.Z"` in the `ir_version` property — this is the
JSON-Schema-side enforcement that any compiled `.termin.pkg` declares
the matching version. `release.py` bumps this in lockstep with
`types.py` (`bump_json_schema()`).

**Tests that need to assert on IR version: read it from the runtime,
don't pin it as a literal.**

```python
# Good — derives from the source of truth at test time
from termin_core.ir.types import AppSpec
assert spec.ir_version == AppSpec.__dataclass_fields__["ir_version"].default

# Better — assert via reflection (covers the wire shape too)
data = client.get("/runtime/info").json()
assert data["ir_version"] == data["expected_ir_version"]  # if exposed

# Bad — hardcoded literal that release.py has to remember
assert spec.ir_version == "0.9.2"
```

The exception: the conformance suite's
`tests/test_reflection.py` legitimately pins the IR version because
its job is to verify the runtime *reports* the right version through
its public reflection endpoint. That assertion stays a literal and
`release.py` bumps it. There should be **one** such test per repo,
not many.

### 2.3 Provider-record `version=` kwarg

**Convention:** the `version=` kwarg on `ProviderRecord` (and
equivalents in the spectrum-provider registration) tracks **the
package version of the providing package.** It tells consumers
"which release of the providers am I getting" and matches what the
package's `__init__.py` declares.

**Implementation:** import `__version__` from the providing package,
pass it as the kwarg. Never hardcode.

```python
# termin-server/termin_server/providers/builtins/compute_default_cel.py
from termin_server import __version__

def register(...):
    return ProviderRecord(..., version=__version__, ...)
```

```python
# termin-spectrum-provider/termin_spectrum/registration.py
from termin_spectrum import __version__

def register_spectrum(...):
    return ProviderRecord(..., version=__version__, ...)
```

This is the largest source of v0.9.3 drift: 10 builtin provider files
in `termin-server` plus `registration.py` in `termin-spectrum-provider`
all hardcoded their version, none were bumped. Importing `__version__`
makes the next release's bump automatic.

### 2.4 Specs and design docs

`docs/*.md` and `specs/*.md` carry their own version on a third,
*independent* track that has nothing to do with package or IR version:

```markdown
**Version:** 0.9.2-draft (synthesized 2026-05-04)
```

A spec version bumps when the spec content changes, not when a
release ships. Multiple specs sit at different versions because they
evolved at different paces (e.g. `migration-contract.md` at 0.9.0,
`conversation-field-contract.md` at 0.9.2). **`release.py` does NOT
touch these.** They're maintained manually by the author when the
spec is revised.

The `(synthesized YYYY-MM-DD)` parenthetical is the date the spec
text was last revised — useful for spotting stale docs at a glance.

---

## 3. Hygiene Checklist Before Tagging

Before any `git tag v0.X.Y` runs, the canonical-source rule must hold.
A 30-second grep verifies it:

```bash
# Find every literal "0.X.Y" version string across the family.
# Should return ONLY the canonical sources for that track:
#
#   Package version (X.Y.Z = current release):
#     termin-*/<package>/__init__.py            # one per package
#     pyproject.toml / setup.py                 # via dynamic version (or literal)
#     termin-spectrum-provider/package.json     # explicit, no-import path
#
#   IR version (X.Y.Z = current IR):
#     termin-core/termin_core/ir/types.py       # one canonical declaration
#     termin-compiler/docs/termin-ir-schema.json  # JSON Schema const
#     termin-conformance/tests/test_reflection.py  # the one runtime-reports test
#
# Anything else that returns is drift.

grep -rEn '"0\.X\.Y"' termin-* --include="*.py" --include="*.toml" \
    --include="*.json" --include="*.md" \
    | grep -v __pycache__ | grep -v fixtures/ | grep -v specs/ \
    | grep -v CHANGELOG | grep -v docs/.*tech-design \
    | grep -v docs/.*roadmap | grep -v docs/.*-brd
```

If anything besides the canonical sources comes back, fix it via
import (don't bump the literal — that's perpetuating the bug).
The grep itself can be wrapped in a `release.py --check-versions`
preflight subcommand so the policy enforces itself; that's an
implementation detail, the convention is the rule.

---

## 4. What `release.py` Owns

`release.py` is responsible for bumping **only the canonical sources**:

**`--compiler-version X.Y.Z` bumps:**

- `termin-core/termin_core/__init__.py::__version__`
- `termin-server/termin_server/__init__.py::__version__`
- `termin-compiler/termin/__init__.py::__version__`
- `termin-spectrum-provider/termin_spectrum/__init__.py::__version__`
- `termin-spectrum-provider/package.json::version` (the only one not
  derived from a Python source)

If `pyproject.toml` / `setup.py` use setuptools dynamic version
attributes, they need no bump. If they don't (legacy pattern), they
also bump — but the goal is to migrate everything to dynamic so the
list shrinks.

**`--ir-version X.Y.Z` bumps:**

- `termin-core/termin_core/ir/types.py::AppSpec.ir_version`
- `termin-compiler/docs/termin-ir-schema.json::ir_version.const`
  (via `bump_json_schema()`)
- `termin-compiler/README.md` (`IR vX.Y.Z` mention)
- `termin-compiler/docs/termin-runtime-implementers-guide.md`
  (`**Version:** X.Y.Z`)
- `termin-conformance/tests/test_reflection.py` (the one
  reflection-reports-this assertion)

**`release.py` does NOT bump:**

- Provider-record `version=` kwargs (they import).
- Test assertions on package version (they import).
- Test assertions on IR version (they read from the runtime).
- Spec / design doc versions (separate track, manually maintained).
- Fixture data (deploy-config `"version"` literals — those are
  test data testing what gets round-tripped, not the package
  version).
- BRD / roadmap historical version mentions.

The list above is finite and enumerable. If `release.py` ever needs a
new entry, the corresponding code is violating the import-from-canonical
rule and should be fixed instead.

---

## 5. Migration from the v0.9.x Drift State

As of 2026-05-09 (post-v0.9.3 ship, pre-v0.9.4), the family has
the following drift:

| Drift | Location | Fix |
|---|---|---|
| Spectrum `__version__` says 0.9.2 | `termin-spectrum-provider/termin_spectrum/__init__.py:33` | Bump to 0.9.3 manually (one-time); refactor to read from package metadata long-term |
| Spectrum `package.json` says 0.9.2 | `termin-spectrum-provider/package.json:3` | Bump to 0.9.3; add to `release.py` |
| Spectrum `registration.py` says 0.1.0 | `termin-spectrum-provider/termin_spectrum/registration.py:58` | Convert to `from termin_spectrum import __version__` |
| 10 server builtin provider versions say 0.9.2 | `termin-server/termin_server/providers/builtins/*.py` | Convert to `from termin_server import __version__` |
| `termin-server/__init__.py` has no `__version__` | `termin-server/termin_server/__init__.py` | Add `__version__ = "0.9.3"` |
| `runtime_version` reflection hardcodes 0.9.2 | `termin-server/termin_server/routes.py:943` | Convert to import + reference |
| `runtime_version` test pins | `termin-server/tests/test_integration.py:912`, `termin-compiler/tests/test_runtime.py:191` | Convert to import + assert on imported value |

This drift gets cleaned up as part of the v0.9.4 prep (untagged
commits under `## [Unreleased]` in each repo's CHANGELOG).

---

## 6. History (How We Got Here)

- **v0.9.0 (2026-04-30):** runtime extraction. Three packages emerge
  from the monolith. Each gets its own `setup.py` / `pyproject.toml`
  and its own `__init__.py::__version__`, all manually maintained.
  `release.py` bumps a curated list of files. No central convention
  yet about provider-record versions or test assertions.
- **v0.9.1 (2026-05-01):** patch release. The curated list works.
- **v0.9.2 (2026-05-05):** IR moves to 0.9.2 (additive: conversation
  field type). Both tracks bump together. The curated list grows by
  a few entries.
- **v0.9.3 (2026-05-07):** runtime extraction, internal API only —
  IR stays at 0.9.2, packages bump to 0.9.3. v0.9.3 ships with
  drift in 14+ files because the curated list was incomplete:
  10 builtin provider versions, spectrum's `__init__.py` /
  `package.json` / `registration.py`, the server's `runtime_version`,
  and several test pins. The drift is benign because the test pins
  also lagged (so they accidentally agree with the lagged source),
  but the released artifact is internally inconsistent. JL surfaces
  this 2026-05-08 as the "less fragile way?" question. This policy
  doc is the answer.
- **v0.9.4 prep (2026-05-09):** the import-from-canonical refactor
  lands as untagged commits ahead of the v0.9.4 release.

---

## See Also

- [`RELEASE_PROCESS.md`](../RELEASE_PROCESS.md) — the operational
  release runbook (uses this policy as input).
- [`util/release.py`](../util/release.py) — the bump script
  (its `VERSION_FILES` table should match §4 of this doc; if they
  diverge, the doc is the spec).
- `CONTRIBUTING.md` — DCO sign-off + general workflow.
