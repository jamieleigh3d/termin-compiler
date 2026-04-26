#!/usr/bin/env python3
"""Termin release preparation script.

Automates the error-prone steps of preparing a new release:
  - Bumps version strings across all files in both repos
  - Rebuilds all .termin.pkg fixtures for the conformance suite
  - Copies the IR JSON Schema to the conformance specs/ directory
  - Runs tests in both repos
  - Displays a summary of changes

Usage:
    python util/release.py --ir-version 0.5.0
    python util/release.py --ir-version 0.5.0 --compiler-version 0.5.0
    python util/release.py --compiler-version 0.2.0  # patch: no IR change
    python util/release.py --dry-run --ir-version 0.5.0  # preview only

This script does NOT commit or push. It makes file changes and tells you
what to review. You decide when to commit.
"""

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import zipfile
from datetime import date
from enum import Enum
from pathlib import Path

# ── Constants ──

COMPILER_ROOT = Path(__file__).parent.parent
CONFORMANCE_ROOT = COMPILER_ROOT.parent / "termin-conformance"

EXAMPLES_DIR = COMPILER_ROOT / "examples"
CONFORMANCE_PKG_DIR = CONFORMANCE_ROOT / "fixtures"

# v0.9: cascade-grammar test fixtures live in tests/fixtures/cascade/
# (compiler side) and conformance-side at fixtures-cascade/. Negative
# fixtures (whose names end in `_rejected.termin`) are NOT compiled —
# they are loaded as raw text by tests that assert the compiler
# rejects them. Only positive fixtures get .termin.pkg artifacts.
CASCADE_FIXTURES_DIR = COMPILER_ROOT / "tests" / "fixtures" / "cascade"
CONFORMANCE_CASCADE_PKG_DIR = CONFORMANCE_ROOT / "fixtures-cascade"
CONFORMANCE_SPECS_DIR = CONFORMANCE_ROOT / "specs"

# Files that contain version strings
VERSION_FILES = {
    "ir_version": [
        (COMPILER_ROOT / "termin" / "ir.py", r'ir_version: str = "[\d.]+"', 'ir_version: str = "{version}"'),
        (COMPILER_ROOT / "docs" / "termin-ir-schema.json", None, None),  # special handling
        (COMPILER_ROOT / "README.md", r'IR v[\d.]+', 'IR v{version}'),
        (COMPILER_ROOT / "docs" / "termin-runtime-implementers-guide.md", r'\*\*Version:\*\* [\d.]+', '**Version:** {version}'),
        (CONFORMANCE_ROOT / "tests" / "test_reflection.py", r'== "[\d.]+"', '== "{version}"'),
    ],
    "compiler_version": [
        (COMPILER_ROOT / "setup.py", r'version="[\d.]+"', 'version="{version}"'),
        (COMPILER_ROOT / "termin" / "__init__.py", r'__version__ = "[\d.]+"', '__version__ = "{version}"'),
    ],
}


# ── Helpers ──

class TerminEncoder(json.JSONEncoder):
    def default(self, obj):
        if isinstance(obj, Enum):
            return obj.name
        if isinstance(obj, (frozenset, set)):
            return [self.default(x) if isinstance(x, Enum) else x for x in obj]
        return super().default(obj)


def banner():
    print()
    print("=" * 72)
    print("  TERMIN RELEASE PREPARATION")
    print("=" * 72)
    print()
    print("  This script is for project maintainers only.")
    print()
    print("  If you're a contributor or curious builder reviewing this repo:")
    print("  you don't need to run this. It automates the tedious cross-repo")
    print("  sync that happens when the IR schema changes.")
    print()
    print("  Pull requests containing version bumps will be auto-rejected.")
    print("  Let maintainers handle the version bump after your changes merge.")
    print()
    print("=" * 72)
    print()


def run(cmd, cwd=None, check=True):
    """Run a shell command, return stdout."""
    result = subprocess.run(cmd, shell=True, cwd=cwd, capture_output=True, text=True)
    if check and result.returncode != 0:
        print(f"  FAILED: {cmd}")
        print(f"  {result.stderr.strip()}")
        sys.exit(1)
    return result.stdout.strip()


def bump_file(path: Path, pattern: str, replacement: str, version: str, dry_run: bool):
    """Replace a version pattern in a file."""
    if not path.exists():
        print(f"  SKIP (not found): {path}")
        return False
    content = path.read_text(encoding="utf-8")
    new_content = re.sub(pattern, replacement.format(version=version), content)
    if content == new_content:
        print(f"  SKIP (no change): {path.name}")
        return False
    if not dry_run:
        path.write_text(new_content, encoding="utf-8")
    print(f"  BUMP: {path.name}")
    return True


def bump_json_schema(path: Path, version: str, dry_run: bool):
    """Update the IR JSON Schema version references."""
    if not path.exists():
        print(f"  SKIP (not found): {path}")
        return False
    content = path.read_text(encoding="utf-8")
    # Update $id URL
    content = re.sub(r'ir/[\d.]+/appspec', f'ir/{version}/appspec', content)
    # Update const value
    content = re.sub(r'"const": "[\d.]+"', f'"const": "{version}"', content)
    if not dry_run:
        path.write_text(content, encoding="utf-8")
    print(f"  BUMP: {path.name}")
    return True


def compile_examples(dry_run: bool):
    """Compile all examples using `termin compile`, producing .termin.pkg and IR dumps.

    Uses the actual compiler CLI to build .termin.pkg files (the same tool
    users run). IR is embedded in each package — no separate ir_dumps step.
    This ensures the release artifacts are identical to what users produce.
    """
    # Note: TatSu grammar objects cause a cosmetic RecursionError during Python
    # GC at exit. This is a known TatSu issue and does not affect correctness.

    count = 0
    for fn in sorted(os.listdir(EXAMPLES_DIR)):
        if not fn.endswith(".termin"):
            continue
        name = fn.replace(".termin", "")
        src = EXAMPLES_DIR / fn
        pkg_out = CONFORMANCE_PKG_DIR / f"{name}.termin.pkg"

        # Build seed arg if companion seed file exists
        seed_path = EXAMPLES_DIR / f"{name}_seed.json"
        seed_args = ["--seed", str(seed_path)] if seed_path.exists() else []

        if dry_run:
            print(f"  COMPILE: {name}.termin -> {name}.termin.pkg")
            count += 1
            continue

        try:
            cmd = [
                sys.executable, "-m", "termin.cli", "compile",
                str(src),
                "-o", str(pkg_out),
            ] + seed_args

            result = subprocess.run(cmd, cwd=COMPILER_ROOT, capture_output=True, text=True)
            if result.returncode != 0:
                print(f"  FAIL: {name} — {result.stderr.strip()}")
                continue
            print(f"  COMPILE: {name}.termin -> {name}.termin.pkg")
            count += 1
        except Exception as e:
            print(f"  FAIL ({type(e).__name__}): {name} — {e}")

    return count


def compile_cascade_fixtures(dry_run: bool):
    """v0.9: compile positive cascade-grammar fixtures into the
    conformance repo's fixtures-cascade/ directory.

    Negative fixtures (those ending in `_rejected.termin`) are NOT
    compiled — they're consumed as raw .termin text by tests that
    assert the compiler rejects them. The negative fixtures stay in
    the compiler-side tests/fixtures/cascade/ tree only; conformance
    runtimes don't need access to them because they're testing the
    compiler, not the runtime.
    """
    if not CASCADE_FIXTURES_DIR.exists():
        return 0

    if not dry_run and CONFORMANCE_ROOT.exists():
        CONFORMANCE_CASCADE_PKG_DIR.mkdir(parents=True, exist_ok=True)

    count = 0
    for fn in sorted(os.listdir(CASCADE_FIXTURES_DIR)):
        if not fn.endswith(".termin"):
            continue
        if fn.endswith("_rejected.termin"):
            continue  # negative fixtures aren't deployable
        name = fn.replace(".termin", "")
        src = CASCADE_FIXTURES_DIR / fn
        pkg_out = CONFORMANCE_CASCADE_PKG_DIR / f"{name}.termin.pkg"

        if dry_run:
            print(f"  COMPILE (cascade): {fn} -> {name}.termin.pkg")
            count += 1
            continue

        if not CONFORMANCE_ROOT.exists():
            continue  # no conformance repo to write into

        try:
            cmd = [
                sys.executable, "-m", "termin.cli", "compile",
                str(src),
                "-o", str(pkg_out),
            ]
            result = subprocess.run(
                cmd, cwd=COMPILER_ROOT, capture_output=True, text=True)
            if result.returncode != 0:
                print(f"  FAIL (cascade): {name} — {result.stderr.strip()}")
                continue
            print(f"  COMPILE (cascade): {fn} -> {name}.termin.pkg")
            count += 1
        except Exception as e:
            print(f"  FAIL ({type(e).__name__}): {name} — {e}")

    return count


def copy_to_conformance(dry_run: bool):
    """Copy schema to conformance repo.

    The .termin.pkg files are already written directly to the conformance
    fixtures dir by compile_examples(). This function copies the IR JSON
    Schema spec to specs/.
    """
    import shutil
    count = 0

    # Schema -> specs/
    schema_src = COMPILER_ROOT / "docs" / "termin-ir-schema.json"
    schema_dst = CONFORMANCE_SPECS_DIR / "termin-ir-schema.json"
    if schema_src.exists():
        if not dry_run:
            shutil.copy2(schema_src, schema_dst)
        print(f"  COPY: {schema_dst.relative_to(CONFORMANCE_ROOT)}")
        count += 1

    # Deploy configs for apps with external channels or AI providers
    for f in COMPILER_ROOT.glob("*.deploy.json"):
        dest = CONFORMANCE_PKG_DIR / f.name
        if not dry_run:
            shutil.copy2(f, dest)
        print(f"  COPY: fixtures/{f.name}")
        count += 1

    return count


def run_tests(repo_path: Path, label: str):
    """Run pytest in a repo, return pass/fail.

    Streams output line-by-line via Popen instead of using capture_output.
    On Windows + Miniconda Python 3.11, subprocess.run(..., capture_output=
    True) hangs at end-of-test-run: pytest completes (CPU time matches a
    clean direct invocation) but the parent never sees the subprocess
    exit and stdout stays buffered. Streaming with `-u` (unbuffered)
    avoids this — each line flushes through immediately, and the parent
    sees EOF cleanly when pytest exits. Tracked as v0.8.2 backlog;
    fixed here.
    """
    print(f"\n  Running {label} tests...")
    cmd = [sys.executable, "-u", "-m", "pytest", "tests/", "--tb=short", "-q"]
    proc = subprocess.Popen(
        cmd, cwd=repo_path,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, bufsize=1,
    )
    last_lines: list[str] = []
    try:
        for line in proc.stdout:
            line = line.rstrip()
            # Keep a tail buffer for summary extraction.
            last_lines.append(line)
            if len(last_lines) > 20:
                last_lines.pop(0)
            # Echo progress dots / failures so the user sees activity.
            if "passed" in line or "failed" in line or "error" in line.lower():
                print(f"  {line}")
    finally:
        proc.wait()
    return proc.returncode == 0


# ── Main ──

def main():
    # Force UTF-8 on stdout so the box-drawing chars in the final
    # checklist don't crash on Windows cp1252. errors='replace' is
    # defense-in-depth for any other Unicode that might land here in
    # the future. No-op on platforms whose default already covers it.
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, OSError):
        # AttributeError on very old Pythons; OSError if the stream
        # cannot be reconfigured (e.g., redirected to a non-text sink).
        pass

    parser = argparse.ArgumentParser(description="Termin release preparation")
    parser.add_argument("--ir-version", help="New IR version (e.g., 0.5.0)")
    parser.add_argument("--compiler-version", help="New compiler version (e.g., 0.5.0)")
    parser.add_argument("--dry-run", action="store_true", help="Preview changes without writing files")
    parser.add_argument("--skip-tests", action="store_true", help="Skip running tests")
    args = parser.parse_args()

    if not args.ir_version and not args.compiler_version:
        parser.error("Specify at least one of --ir-version or --compiler-version")

    banner()

    if args.dry_run:
        print("[DRY RUN] No files will be modified.\n")

    # ── Check preconditions ──
    print("Checking preconditions...")

    if not COMPILER_ROOT.exists():
        print(f"  ERROR: Compiler repo not found at {COMPILER_ROOT}")
        sys.exit(1)
    print(f"  Compiler repo: {COMPILER_ROOT}")

    conformance_ok = CONFORMANCE_ROOT.exists()
    if args.ir_version and not conformance_ok:
        print(f"  WARNING: Conformance repo not found at {CONFORMANCE_ROOT}")
        print(f"  IR version bumps require the conformance repo.")
    elif conformance_ok:
        print(f"  Conformance repo: {CONFORMANCE_ROOT}")

    # ── Bump versions ──
    changed = []

    if args.compiler_version:
        print(f"\nBumping compiler version to {args.compiler_version}...")
        for path, pattern, replacement in VERSION_FILES["compiler_version"]:
            if bump_file(path, pattern, replacement, args.compiler_version, args.dry_run):
                changed.append(path)

    if args.ir_version:
        print(f"\nBumping IR version to {args.ir_version}...")
        for path, pattern, replacement in VERSION_FILES["ir_version"]:
            if path.name == "termin-ir-schema.json":
                if bump_json_schema(path, args.ir_version, args.dry_run):
                    changed.append(path)
            elif pattern:
                if bump_file(path, pattern, replacement, args.ir_version, args.dry_run):
                    changed.append(path)

        # ── Compile all examples ──
        # Uses `termin compile` to produce both .termin.pkg and IR dumps.
        # Packages are written directly to the conformance fixtures dir.
        print(f"\nCompiling all examples...")
        n = compile_examples(args.dry_run)
        print(f"  {n} examples compiled")

        # ── Compile v0.9 cascade test fixtures ──
        # Positive cascade fixtures are runtime-deployable and live in
        # the conformance repo's fixtures-cascade/ directory. Negative
        # fixtures (compile-error cases) stay compiler-side as raw
        # .termin text.
        print(f"\nCompiling cascade test fixtures...")
        n = compile_cascade_fixtures(args.dry_run)
        print(f"  {n} cascade fixtures compiled")

        if conformance_ok:
            # ── Copy IR dumps + schema to conformance ──
            print(f"\nCopying artifacts to conformance repo...")
            n = copy_to_conformance(args.dry_run)
            print(f"  {n} files copied")

    # ── Run tests ──
    if not args.skip_tests and not args.dry_run:
        print("\n" + "-" * 40)
        ok1 = run_tests(COMPILER_ROOT, "compiler")
        ok2 = True
        if conformance_ok and args.ir_version:
            ok2 = run_tests(CONFORMANCE_ROOT, "conformance")

        if not ok1 or not ok2:
            print("\n  TESTS FAILED. Fix issues before committing.")
            sys.exit(1)

    # ── Pre-commit checklist ──
    print("\n" + "=" * 72)
    print("  RELEASE PREPARATION COMPLETE")
    print("=" * 72)

    if args.dry_run:
        print("\n  [DRY RUN] No files were modified.")
    else:
        print(f"\n  Files modified in compiler repo: {len(changed) + (7 if args.ir_version else 0)}")
        if conformance_ok and args.ir_version:
            print(f"  Files modified in conformance repo: ~21")
        print()
        print("  ┌─────────────────────────────────────────────────────────┐")
        print("  │  MANDATORY CHECKLIST — do NOT skip these steps:        │")
        print("  ├─────────────────────────────────────────────────────────┤")
        print("  │  [ ] 1. Update CHANGELOG.md in compiler repo           │")
        print("  │  [ ] 2. Update README.md in conformance repo:          │")
        print("  │         - IR version number                            │")
        print("  │         - Test count                                   │")
        print("  │         - Fixture list (new .termin.pkg files)         │")
        print("  │         - Changelog section for this version           │")
        print("  │  [ ] 3. Review git diff in BOTH repos                  │")
        print("  │  [ ] 4. Run tests in BOTH repos                        │")
        print("  │  [ ] 5. Commit compiler repo                           │")
        print("  │  [ ] 6. Commit conformance repo                        │")
        print("  │  [ ] 7. Tag both repos: git tag vX.Y.Z                 │")
        print("  │  [ ] 8. Push both repos with --tags                    │")
        print("  │  [ ] 9. Rebase messages branch onto new main           │")
        print("  │  [ ] 10. Update open threads with release status       │")
        print("  └─────────────────────────────────────────────────────────┘")
    print()


if __name__ == "__main__":
    main()
