#!/usr/bin/env python3
"""Termin release preparation script.

Automates the error-prone steps of preparing a new release:
  - Bumps version strings across all files in both repos
  - Regenerates all IR dumps from example .termin files
  - Rebuilds all .termin.pkg fixtures for the conformance suite
  - Copies artifacts to the conformance repo
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
IR_DUMPS_DIR = COMPILER_ROOT / "ir_dumps"
CONFORMANCE_IR_DIR = CONFORMANCE_ROOT / "fixtures" / "ir"
CONFORMANCE_PKG_DIR = CONFORMANCE_ROOT / "fixtures"
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
    users run). IR dumps are extracted from the packages via --emit-ir.
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
        ir_out = IR_DUMPS_DIR / f"{name}_ir.json"

        # Build seed arg if companion seed file exists
        seed_path = EXAMPLES_DIR / f"{name}_seed.json"
        seed_args = ["--seed", str(seed_path)] if seed_path.exists() else []

        if dry_run:
            print(f"  COMPILE: {name}.termin -> {name}.termin.pkg + {name}_ir.json")
            count += 1
            continue

        try:
            cmd = [
                sys.executable, "-m", "termin.cli", "compile",
                str(src),
                "-o", str(pkg_out),
                "--emit-ir", str(ir_out),
            ] + seed_args

            result = subprocess.run(cmd, cwd=COMPILER_ROOT, capture_output=True, text=True)
            if result.returncode != 0:
                print(f"  FAIL: {name} — {result.stderr.strip()}")
                continue
            print(f"  COMPILE: {name}.termin -> .termin.pkg + _ir.json")
            count += 1
        except Exception as e:
            print(f"  FAIL ({type(e).__name__}): {name} — {e}")

    return count


def copy_to_conformance(dry_run: bool):
    """Copy IR dumps and schema to conformance repo.

    The .termin.pkg files are already written directly to the conformance
    fixtures dir by compile_examples(). This function copies the remaining
    artifacts: IR JSON dumps (for runtimes without .pkg support) and the
    IR JSON Schema spec.
    """
    import shutil
    count = 0

    # IR dumps -> fixtures/ir/
    for f in IR_DUMPS_DIR.glob("*_ir.json"):
        dest = CONFORMANCE_IR_DIR / f.name
        if not dry_run:
            shutil.copy2(f, dest)
        print(f"  COPY: fixtures/ir/{f.name}")
        count += 1

    # Schema -> specs/
    schema_src = COMPILER_ROOT / "docs" / "termin-ir-schema.json"
    schema_dst = CONFORMANCE_SPECS_DIR / "termin-ir-schema.json"
    if schema_src.exists():
        if not dry_run:
            shutil.copy2(schema_src, schema_dst)
        print(f"  COPY: specs/termin-ir-schema.json")
        count += 1

    return count


def run_tests(repo_path: Path, label: str):
    """Run pytest in a repo, return pass/fail."""
    print(f"\n  Running {label} tests...")
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "tests/", "--tb=short", "-q"],
        cwd=repo_path, capture_output=True, text=True,
    )
    # Extract summary line
    for line in result.stdout.strip().splitlines()[-3:]:
        if "passed" in line or "failed" in line:
            print(f"  {line.strip()}")
    return result.returncode == 0


# ── Main ──

def main():
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

    # ── Summary ──
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
        print("  Next steps:")
        print("    1. Review the changes (git diff)")
        print("    2. Commit the compiler repo")
        print("    3. Commit the conformance repo")
        print("    4. Push both")
        print("    5. Add a changelog entry to conformance README.md")
    print()


if __name__ == "__main__":
    main()
