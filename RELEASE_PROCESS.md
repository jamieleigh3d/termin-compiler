# Termin Release Process

How to prepare and publish a new version of **termin-compiler** and **termin-conformance**.

---

## 1. Version Axes

Termin has three independent version numbers:

| Version | Where | What it means |
|---------|-------|---------------|
| **Compiler version** | `setup.py`, `termin/__init__.py` | The `pip install` version of the compiler package |
| **IR version** | `termin/ir.py`, `docs/termin-ir-schema.json` | The schema version of compiled output. A runtime checks this to know what it can consume. |
| **Manifest version** | `docs/termin-package-format.md` | The `.termin.pkg` envelope format. Rarely changes. |

The IR version is the most impactful. When it bumps, every `.termin.pkg`, every conformance fixture, and every test that asserts a version string must update.

---

## 2. When to Bump What

### Patch (e.g., 0.9.1 &rarr; 0.9.2)

**Pre-v1.0:** any backwards-compatible change is a patch &mdash; including
**additive IR fields, new DSL syntax, and new runtime features.** The
contract is "old `.termin` files still compile, old `.termin.pkg`
artifacts still load, conformant runtimes still pass." Bug fixes,
hardening, and documentation are also patches.

Anything that breaks one of those three is a minor (see below).

A v0.X.Y patch typically:

- Bumps compiler + server + core + spectrum-provider versions
  (`setup.py` / `pyproject.toml` / `__init__.py` in each)
- Bumps the IR version in `termin-core/termin_core/ir/types.py`
  and `termin-compiler/docs/termin-ir-schema.json` if any IR fields
  were added (additive only)
- Regenerates `.termin.pkg` fixtures into the conformance repo if
  the IR or compiler changed
- Adds a CHANGELOG entry in every affected repo
- Runs the full test matrix across all four test repos (compiler,
  server, core, conformance)

Use `release.py` (see step-by-step below) for additive-IR patches;
straight bug-fix patches with no IR work can skip the fixture
regeneration step.

### Minor (e.g., 0.9.x &rarr; 0.10.0)

**Pre-v1.0:** breaking changes &mdash; field renames, field removals,
semantic changes, grammar tightening that rejects previously-valid
sources, runtime behavior changes that break conformant
implementations.

- Use `release.py`
- Migration notes in CHANGELOG explaining what existing
  `.termin` files / runtimes / deploy configs need to update
- Update conformance suite version check to reject old IR if
  the change is incompatible at the schema level

### Major (e.g., 0.x &rarr; 1.0.0)

The 1.0 commitment. After 1.0 the patch/minor/major distinction
follows strict semver:

- patch = backwards-compatible bug fixes only
- minor = backwards-compatible additions
- major = anything breaking

Pre-1.0 we use the looser "additive = patch, breaking = minor"
convention because the schema is still actively evolving and we
want the version arc to reflect what *broke*, not what *grew*.

---

## 3. Release Process (Step by Step)

### Prerequisites

- All feature work is done, committed, and tested on the feature branch
- Both repos (`termin-compiler` and `termin-conformance`) are present as siblings:
  ```
  ClaudeWorkspace/
    termin/                 # termin-compiler
    termin-conformance/     # termin-conformance
  ```

### Step 1: Version bumps during development

Bump version strings early in the release cycle (while on the feature branch), NOT at release time. The release script verifies they're correct but shouldn't be the first time versions change.

Files that need version strings:
- `termin/ir.py` &mdash; `ir_version`
- `termin/__init__.py` &mdash; `__version__`
- `setup.py` &mdash; `version`
- `termin_runtime/routes.py` &mdash; `runtime_version` in registry endpoint
- `docs/termin-ir-schema.json` &mdash; `$id` URL and `const` value
- `conformance/tests/test_reflection.py` &mdash; version assertion
- `conformance/tests/test_ir_v050.py` &mdash; version assertion (if present)
- `conformance/tests/test_ir_schema_validation.py` &mdash; version assertion

### Step 2: Manual updates (human judgment required)

These cannot be automated &mdash; they require writing:

1. **`CHANGELOG.md`** in compiler repo &mdash; full release notes
2. **`README.md`** in conformance repo &mdash; IR version, changelog entry
3. **`README.md`** in compiler repo &mdash; update stale version references
4. **`.gitignore`** in all repos &mdash; add any new patterns (`.venv`, `.coverage`, etc.)

### Step 3: Run release.py

From the compiler repo root:

```bash
python util/release.py --ir-version X.Y.Z --compiler-version X.Y.Z
```

This script:
1. Verifies version strings are bumped in all files
2. Compiles all 13 examples (produces `.termin.pkg`)
3. Copies packages, JSON schema, and deploy configs to conformance repo
4. Runs tests in both repos

The script does NOT commit or push. Review the changes before committing.

To preview without modifying files:
```bash
python util/release.py --ir-version X.Y.Z --dry-run
```

To skip tests (if you already ran them):
```bash
python util/release.py --ir-version X.Y.Z --compiler-version X.Y.Z --skip-tests
```

### Step 4: Commit both repos

```bash
# Compiler repo
cd termin/
git add -A
git commit -m "v0.X.0 release: changelog, fixtures"

# Conformance repo
cd ../termin-conformance/
git add -A
git commit -m "Update conformance suite for v0.X.0"
```

### Step 5: Merge to main

Both repos should be on feature branches during development. Merge with fast-forward only (linear history):

```bash
# Compiler
cd termin/
git checkout main
git merge --ff-only feature/vX.Y

# Conformance
cd ../termin-conformance/
git checkout main
git merge --ff-only feature/vX.Y
```

### Step 6: Verify clean status

```bash
# Both repos: main branch, clean working directory
git branch --show-current   # main
git status --short           # empty
```

### Step 7: Tag and push

```bash
# Compiler
cd termin/
git tag -a vX.Y.Z -m "vX.Y.Z — <theme>"
git push origin main --tags

# Conformance
cd ../termin-conformance/
git tag -a vX.Y.Z -m "vX.Y.Z — Conformance suite for IR X.Y.Z"
git push origin main --tags
```

### Step 8: Clean up

```bash
# Delete feature branches (local + remote)
git branch -D feature/vX.Y
git push origin --delete feature/vX.Y

# Rebase messages branch onto new main
git checkout messages
git rebase main
git push origin messages --force-with-lease
git checkout main
```

### Step 9: Post-release

- Verify the downstream runtime's hourly cron picks up the new tag
- Update any open threads on the messages branch with release status

---

## 4. What release.py Automates vs. What It Doesn't

### Automated by release.py

| Step | What it does |
|------|-------------|
| Version bump verification | Checks all files have the target version |
| Example compilation | `termin compile` on all 13 examples → `.termin.pkg` |
| Conformance fixture sync | Copies `.termin.pkg`, schema, and deploy configs to conformance repo |
| Test execution | Runs pytest in both repos |

### NOT automated (human required)

| Step | Why |
|------|-----|
| CHANGELOG.md | Requires writing release notes |
| Conformance README.md | Requires changelog prose |
| Git operations (commit, merge, tag, push) | Intentional &mdash; human reviews before committing |
| Messages branch rebase | Depends on release timing |
| .gitignore updates | Ad hoc as new patterns emerge |

---

## 5. Advisory

> **This release process is for project maintainers only.**
>
> If you're a contributor or curious builder reviewing this repository &mdash; welcome! You don't need to run the release script. It exists to automate the tedious cross-repo synchronization that happens when the IR schema changes.
>
> **Pull requests that contain version bumps will be auto-rejected.** Version bumps are coordinated by maintainers after all changes for a release are merged. If your PR changes `ir_version`, `__version__`, or `termin-ir-schema.json`, please remove those changes and let the maintainers handle the version bump separately.
>
> If you're building a Termin runtime and need a specific IR version, check the conformance suite's `README.md` for the changelog and the `specs/` directory for the schema.
