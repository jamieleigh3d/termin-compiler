# Termin Release Process

How to prepare and publish a new version of **termin-compiler** or **termin-conformance**.

---

## 1. Version Axes

Termin has three independent version numbers:

| Version | Where | What it means |
|---------|-------|---------------|
| **Compiler version** | `setup.py`, `termin/__init__.py` | The `pip install` version of the compiler package |
| **IR version** | `termin/ir.py`, `docs/termin-ir-schema.json` | The schema version of compiled output. A runtime checks this to know what it can consume. |
| **Manifest version** | `docs/termin-package-format.md` | The `.termin.pkg` envelope format. Rarely changes. |

The IR version is the most impactful. When it bumps, every IR dump, every `.termin.pkg`, every conformance fixture, and every test that asserts a version string must update.

---

## 2. When to Bump What

### Patch (e.g., 0.4.0 &rarr; 0.4.1)

Bug fix. No IR schema changes. No new fields. No breaking syntax.

- Bump compiler version only (`setup.py`, `termin/__init__.py`)
- No IR version change, no fixture regeneration
- Run tests, commit, push

### Minor (e.g., 0.4.0 &rarr; 0.5.0)

New IR fields (additive, backward-compatible). New DSL syntax. New runtime features. Old IR still loads in new runtime.

- Bump IR version in `termin/ir.py` and `docs/termin-ir-schema.json`
- Bump compiler version in `setup.py` and `termin/__init__.py`
- Regenerate all IR dumps from examples
- Rebuild all `.termin.pkg` fixtures
- Update JSON Schema with new fields
- Copy schema + fixtures to conformance repo
- Update version assertions in tests
- Update README references
- Add changelog entry to conformance README

### Major (e.g., 0.x &rarr; 1.0.0)

Breaking IR change. Old IR may not load in new runtime. Field renames, removals, semantic changes.

Same as minor, plus:
- Migration guide for existing `.termin` files
- Consider backward-compatibility shim in runtime
- Update conformance suite version check to reject old IR

---

## 3. Current Manual Process (What We've Been Doing)

This is the sequence of steps performed manually during recent releases (0.3.0 &rarr; 0.4.0):

### 3.1 Compiler repo (termin-compiler)

1. **Make the code changes** (new IR fields, grammar, parser, runtime)
2. **Bump `ir_version`** in `termin/ir.py` (e.g., `"0.3.0"` &rarr; `"0.4.0"`)
3. **Run tests** to catch assertion failures on the old version string:
   ```bash
   python -m pytest tests/ -v
   ```
4. **Fix version assertions** in `tests/test_runtime.py` (reflection endpoint check)
5. **Recompile all examples** to regenerate IR dumps and packages:
   ```bash
   # For each .termin in examples/:
   termin compile examples/X.termin -o fixtures/X.termin.pkg --emit-ir ir_dumps/X_ir.json
   ```
6. **Run tests again** &mdash; should be all green now
7. **Update `docs/termin-ir-schema.json`**:
   - `$id` URL
   - `const` value for `ir_version`
   - Add new field definitions
   - Validate JSON
8. **Update doc version headers** (`termin-runtime-implementers-guide.md`, etc.)
9. **Update `README.md`** if it references an IR version
10. **Commit and push**

### 3.2 Conformance repo (termin-conformance)

1. **Copy all IR dumps** from `termin/ir_dumps/` to `conformance/fixtures/ir/`
2. **Rebuild all `.termin.pkg`** files (already done by the compile step above &mdash;
   `termin compile` writes packages directly to the conformance fixtures dir)
3. **Copy `termin-ir-schema.json`** to `conformance/specs/`
4. **Update `tests/test_reflection.py`** &mdash; version assertion
5. **Update `README.md`**:
   - Current IR version
   - Changelog entry for the new version
6. **Run conformance suite**:
   ```bash
   python -m pytest tests/ -v
   ```
7. **Commit and push**

### 3.3 Error-Prone Steps

These are the steps where mistakes happen:

| Step | What goes wrong |
|------|----------------|
| IR dump regeneration | Forgetting an example, stale dumps from previous version |
| `.termin.pkg` rebuild | Now handled by `termin compile` &mdash; no manual ZIP construction |
| Version string grep | Missing one of 8+ files that contain version references |
| Schema update | Adding new fields but forgetting to validate JSON |
| Cross-repo copy | Wrong direction, stale files, forgotten files |
| Conformance README | Forgetting the changelog entry |

---

## 4. Streamlined Process (Target)

The north star is a single script that does all error-prone steps:

```bash
python util/release.py --ir-version 0.5.0 --compiler-version 0.5.0
```

This script would:

1. **Validate preconditions**
   - Working directory clean (no uncommitted changes)
   - All tests pass
   - Both repos present at expected paths
   - Current branch is `main`

2. **Bump version strings** across all files:
   - `termin/ir.py` &mdash; `ir_version`
   - `termin/__init__.py` &mdash; `__version__`
   - `setup.py` &mdash; `version`
   - `docs/termin-ir-schema.json` &mdash; `$id` and `const`
   - `README.md` &mdash; IR version reference
   - `docs/termin-runtime-implementers-guide.md` &mdash; version header

3. **Recompile all examples** using `termin compile`
   - Produces both `.termin.pkg` (to conformance fixtures) and IR JSON dumps
   - Uses the same compiler path users run &mdash; no manual ZIP construction
   - Handles seed data, checksums, manifests, revision tracking automatically

5. **Run compiler tests** &mdash; fail fast if anything breaks

6. **Sync to conformance repo**
   - Copy IR dumps to `fixtures/ir/`
   - Copy `.termin.pkg` to `fixtures/`
   - Copy `termin-ir-schema.json` to `specs/`
   - Update `tests/test_reflection.py` version assertion
   - Update `README.md` version + changelog stub

7. **Run conformance tests** &mdash; fail fast

8. **Display summary** &mdash; what changed, what to commit

The script does NOT commit or push. It makes all the file changes and tells you what to review. The human decides when to commit.

---

## 5. Advisory

> **This release process is for project maintainers only.**
>
> If you're a contributor or curious builder reviewing this repository &mdash; welcome! You don't need to run the release script. It exists to automate the tedious cross-repo synchronization that happens when the IR schema changes.
>
> **Pull requests that contain version bumps will be auto-rejected.** Version bumps are coordinated by maintainers after all changes for a release are merged. If your PR changes `ir_version`, `__version__`, or `termin-ir-schema.json`, please remove those changes and let the maintainers handle the version bump separately.
>
> If you're building a Termin runtime and need a specific IR version, check the conformance suite's `README.md` for the changelog and the `specs/` directory for the schema.
