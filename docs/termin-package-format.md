# Termin Package Format (.termin.pkg)

**Version:** 1.0.0-draft
**Status:** Formative

---

## Overview

A `.termin.pkg` file is the compiled output of the Termin compiler — the single deployment artifact for a Termin application. It is a ZIP archive with a `.termin.pkg` extension containing a manifest, compiled IR, source files, optional seed data, optional static assets, and integrity checksums.

A `.termin.pkg` is to a Termin application what a `.jar` is to Java or a `.whl` is to Python: a self-contained, deployable unit.

---

## File Structure

```
warehouse.termin.pkg (ZIP archive)
├── manifest.json           # required — package metadata and file index
├── warehouse.ir.json       # required — compiled IR (the runtime reads this)
├── warehouse.termin        # required — source DSL
├── warehouse_seed.json     # optional — seed data for first-run population
└── assets/                 # optional — static files (images, CSS, etc.)
    └── ...
```

---

## Manifest

The manifest is the entry point. A runtime reads `manifest.json` first to understand what the package contains and how to deploy it.

```json
{
  "manifest_version": "1.0.0",
  "app": {
    "id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
    "name": "Warehouse Inventory Manager",
    "version": "1.0.0",
    "revision": 1,
    "description": "Tracks products, stock levels, and reorder alerts"
  },
  "ir": {
    "version": "0.8.0",
    "entry": "warehouse.ir.json"
  },
  "source": {
    "files": ["warehouse.termin"],
    "entry": "warehouse.termin"
  },
  "seed": "warehouse_seed.json",
  "assets": "assets/",
  "checksums": {
    "warehouse.ir.json": "sha256:a1b2c3d4...",
    "warehouse.termin": "sha256:e5f6a7b8...",
    "warehouse_seed.json": "sha256:c9d0e1f2..."
  }
}
```

### Field Definitions

#### `manifest_version` (required)

Semantic version of the manifest format itself. Runtimes check this to know how to read the manifest. Breaking changes to manifest structure bump the major version.

Current: `"1.0.0"`

#### `app` (required)

Application metadata.

| Field | Type | Description |
|-------|------|-------------|
| `app.id` | string (UUID) | Globally unique identifier for this application. Generated on first compile, persisted across recompilations. Used to match an existing deployment for revision incrementing. |
| `app.name` | string | Human-readable application name from the DSL `Application:` declaration. |
| `app.version` | string (semver) | Author-controlled semantic version. Defaults to `"1.0.0"`. Set via `termin compile --version 2.0.0` or a `Version:` declaration in the DSL. |
| `app.revision` | integer | Monotonically incrementing build counter. Automatically incremented by the compiler on each recompilation. See § Revision Logic below. |
| `app.description` | string | Application description from the DSL `Description:` declaration. |

#### `ir` (required)

Intermediate representation metadata.

| Field | Type | Description |
|-------|------|-------------|
| `ir.version` | string (semver) | IR schema version. Compiler-controlled. Tells the runtime which IR specification to expect. |
| `ir.entry` | string | Filename of the compiled IR JSON within the archive. |

#### `source` (required)

Source file metadata. Included for reference, debugging, and visual editor round-tripping. Runtimes do not need source files to run — the IR is sufficient.

| Field | Type | Description |
|-------|------|-------------|
| `source.files` | array of strings | All source `.termin` files in the package. Currently single-file; future multi-file apps will list all files here. |
| `source.entry` | string | The root source file (entry point for multi-file compilation). |

#### `seed` (optional)

Filename of a seed data JSON file. Format: `{"content_name": [records...]}`. Loaded on first run when tables are empty.

#### `assets` (optional)

Directory name within the archive containing static assets. Served by the runtime at a configured path.

#### `checksums` (required)

SHA-256 checksums for every file in the package (excluding `manifest.json` itself). Format: `"filename": "sha256:<hex_digest>"`.

Used for integrity verification:
- Runtime checks checksums on deploy to detect corruption or tampering
- Package comparison: if checksums match, the content is identical

---

## Versioning Model

Three independent version axes:

| Version | Controlled By | Purpose |
|---------|--------------|---------|
| `manifest_version` | Package format spec | How to read the manifest. Runtimes reject unsupported manifest versions. |
| `app.version` | Application author | Semantic version of the application. Author bumps this for releases. |
| `ir.version` | Compiler | IR schema version. Runtimes reject IR versions they don't support. |

Plus one build counter:

| Field | Controlled By | Purpose |
|-------|--------------|---------|
| `app.revision` | Compiler (automatic) | Monotonic build number. Increments on every recompilation. Enables redeployment detection even when the author doesn't bump `app.version`. |

### Revision Logic

When the compiler produces a `.termin.pkg`:

1. If no existing `.termin.pkg` file exists at the output path → `revision = 1`, generate a new `app.id` (UUID).
2. If an existing `.termin.pkg` exists at the output path:
   a. Read its `manifest.json`
   b. If `app.id` matches → increment `revision` by 1, preserve `app.id`
   c. If `app.id` doesn't match (different app) → `revision = 1`, generate new `app.id`
3. If `--version X.Y.Z` is passed, set `app.version` to that value. Otherwise preserve the existing `app.version` (or default to `"1.0.0"` for new packages).

This means:
- A developer can `termin compile` repeatedly and the revision increments automatically
- A deployment system can compare `app.revision` to know if a redeployment is needed
- `app.version` only changes when the author explicitly bumps it

---

## Compiler Output

```bash
# First compile — creates new package
termin compile warehouse.termin -o warehouse.termin.pkg
# → revision 1, new UUID

# Recompile after source changes — revision increments
termin compile warehouse.termin -o warehouse.termin.pkg
# → revision 2, same UUID

# Explicit version bump
termin compile warehouse.termin -o warehouse.termin.pkg --version 2.0.0
# → revision 3, version "2.0.0", same UUID

# With seed data and assets
termin compile warehouse.termin -o warehouse.termin.pkg \
  --seed warehouse_seed.json \
  --assets ./static/
```

---

## Runtime Contract

A conforming runtime must:

1. Accept a `.termin.pkg` file as its deployment input
2. Read `manifest.json` from the ZIP archive
3. Reject packages with unsupported `manifest_version` or `ir.version`
4. Verify `checksums` for all listed files
5. Read the IR from `ir.entry` and deploy the application
6. Load `seed` data on first run (when tables are empty)
7. Serve `assets/` at a configured static file path

A runtime may additionally:
- Display `app.name`, `app.version`, `app.revision` in admin UI
- Use `app.id` to match existing deployments for hot-reload or rolling updates
- Store `app.revision` for deployment audit trails
- Preserve source files for debugging or visual editor access

---

## Future Extensions

- **Multi-file source**: `source.files` will list multiple `.termin` files with import resolution order. `source.entry` remains the root file. The compiler resolves all imports at compile time — the IR is always a single merged file.
- **Cryptographic signatures**: A `signature` field in the manifest containing a detached signature over the checksums. Enables trust verification for packages from external sources. (Deferred — see roadmap Block E.)
- **Package dependencies**: A `dependencies` field listing other `.termin.pkg` files this package imports from. Enables library reuse across applications.
