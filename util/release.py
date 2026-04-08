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


def regenerate_ir_dumps(dry_run: bool):
    """Compile all examples and write IR dumps."""
    sys.path.insert(0, str(COMPILER_ROOT))
    old_limit = sys.getrecursionlimit()
    sys.setrecursionlimit(10000)
    # Suppress TatSu GC RecursionError at exit
    import atexit, gc
    atexit.register(lambda: (gc.disable(), os._exit(0)))

    from dataclasses import asdict
    from termin.peg_parser import parse_peg
    from termin.analyzer import analyze
    from termin.lower import lower

    count = 0
    for fn in sorted(os.listdir(EXAMPLES_DIR)):
        if not fn.endswith(".termin"):
            continue
        name = fn.replace(".termin", "")
        try:
            source = (EXAMPLES_DIR / fn).read_text(encoding="utf-8")
            prog, errors = parse_peg(source)
            if not errors.ok:
                print(f"  FAIL (parse): {name}")
                continue
            result = analyze(prog)
            if not result.ok:
                print(f"  FAIL (analyze): {name}")
                continue
            spec = lower(prog)

            out_path = IR_DUMPS_DIR / f"{name}_ir.json"
            if not dry_run:
                ir_dict = asdict(spec)
                with open(out_path, "w", encoding="utf-8") as f:
                    json.dump(ir_dict, f, indent=2, ensure_ascii=False, cls=TerminEncoder)
                    f.write("\n")
            print(f"  REGEN: {name}_ir.json")
            count += 1
        except RecursionError:
            print(f"  FAIL (recursion): {name} — try increasing sys.setrecursionlimit")
        except Exception as e:
            print(f"  FAIL ({type(e).__name__}): {name} — {e}")

    sys.setrecursionlimit(old_limit)
    return count


def rebuild_packages(dry_run: bool):
    """Rebuild .termin.pkg files for the conformance suite."""
    count = 0
    for fn in sorted(os.listdir(EXAMPLES_DIR)):
        if not fn.endswith(".termin"):
            continue
        name = fn.replace(".termin", "")
        ir_path = IR_DUMPS_DIR / f"{name}_ir.json"
        src_path = EXAMPLES_DIR / fn

        if not ir_path.exists():
            continue

        ir_bytes = ir_path.read_bytes()
        src_bytes = src_path.read_bytes()
        ir = json.loads(ir_bytes)

        manifest = {
            "manifest_version": "1.0.0",
            "app": {
                "id": ir.get("app_id", ""),
                "name": ir.get("name", ""),
                "version": "1.0.0",
                "revision": 1,
                "description": ir.get("description", ""),
            },
            "ir": {
                "version": ir.get("ir_version", ""),
                "entry": f"{name}.ir.json",
            },
            "source": {"files": [fn], "entry": fn},
            "seed": None,
            "assets": None,
            "checksums": {
                f"{name}.ir.json": "sha256:" + hashlib.sha256(ir_bytes).hexdigest(),
                fn: "sha256:" + hashlib.sha256(src_bytes).hexdigest(),
            },
        }

        # Check for seed data
        seed_path = EXAMPLES_DIR / f"{name}_seed.json"
        if seed_path.exists():
            seed_bytes = seed_path.read_bytes()
            manifest["seed"] = f"{name}_seed.json"
            manifest["checksums"][f"{name}_seed.json"] = "sha256:" + hashlib.sha256(seed_bytes).hexdigest()

        pkg_path = CONFORMANCE_PKG_DIR / f"{name}.termin.pkg"
        if not dry_run:
            with zipfile.ZipFile(pkg_path, "w", zipfile.ZIP_DEFLATED) as zf:
                zf.writestr("manifest.json", json.dumps(manifest, indent=2))
                zf.writestr(f"{name}.ir.json", ir_bytes)
                zf.writestr(fn, src_bytes)
                if seed_path.exists():
                    zf.writestr(f"{name}_seed.json", seed_path.read_bytes())

        print(f"  BUILD: {name}.termin.pkg")
        count += 1
    return count


def copy_to_conformance(dry_run: bool):
    """Copy IR dumps and schema to conformance repo."""
    import shutil
    count = 0

    # IR dumps
    for f in IR_DUMPS_DIR.glob("*_ir.json"):
        dest = CONFORMANCE_IR_DIR / f.name
        if not dry_run:
            shutil.copy2(f, dest)
        print(f"  COPY: ir/{f.name}")
        count += 1

    # Schema
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

        # ── Regenerate IR dumps ──
        print(f"\nRegenerating IR dumps...")
        n = regenerate_ir_dumps(args.dry_run)
        print(f"  {n} IR dumps regenerated")

        if conformance_ok:
            # ── Rebuild packages ──
            print(f"\nRebuilding .termin.pkg fixtures...")
            n = rebuild_packages(args.dry_run)
            print(f"  {n} packages rebuilt")

            # ── Copy to conformance ──
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
    # TatSu grammar objects cause RecursionError during Python GC at exit.
    # Disable GC and force-exit to avoid the misleading error message.
    import gc
    gc.disable()
    os._exit(0)
