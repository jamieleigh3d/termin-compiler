# Hello-World Verification — Spectrum Provider End-to-End

This is the manual-verification recipe for the v0.9 Phase 5b.4 B' loop using the Spectrum presentation provider. It exercises every piece of the loop in one run: entry-point provider discovery, deploy-config binding resolution, bundle build pipeline, bundle serving, shell HTML, page-data API, and the JS-side bootstrap.

**Audience:** the developer (JL or anyone else) who has just landed B' plumbing changes and wants to confirm they actually render in a browser. Run this on WSL — the JS toolchain (npm/esbuild) wants Linux pathnames; the Python runtime is happy on either side.

**Estimated time:** 5-10 minutes the first time (npm install dominates), 1-2 minutes on re-runs.

---

## Prerequisites

WSL with:
- Python ≥ 3.11 (`python3 --version`)
- Node ≥ 20 (`node --version`)
- A virtualenv you'd like to install Termin into

Two sibling clones (the layout `CONTRIBUTING.md` documents):

```
~/work/                                  # or wherever you keep code
├── termin-compiler/                     # this repo
└── termin-spectrum-provider/            # https://github.com/jamieleigh3d/termin-spectrum-provider
```

If termin-spectrum-provider isn't cloned yet:

```bash
cd ~/work
git clone git@github.com:jamieleigh3d/termin-spectrum-provider.git
```

## Step 1 — Install both packages editable

Activate your venv first (`source ~/work/venv/bin/activate` or wherever).

```bash
cd ~/work/termin-compiler
pip install -e ".[test]"

cd ~/work/termin-spectrum-provider
pip install -e ".[test]"
```

The order matters: termin-spectrum-provider's `setup.py` declares a dependency on `termin-compiler>=0.9.0`. With termin-compiler installed first (editable), the constraint is satisfied locally.

**Sanity check** the entry point registered:

```bash
python -c "from importlib.metadata import entry_points; print([e.name for e in entry_points(group='termin.providers')])"
```

Expected output: `['spectrum']`.

## Step 2 — Build the JS bundle

```bash
cd ~/work/termin-spectrum-provider
npm install        # first run only; ~2-3 min
npm run build
```

Expected: `[esbuild] build complete -> termin_spectrum/static/bundle.js`. The bundle should be ~150-200KB.

```bash
ls -lh termin_spectrum/static/bundle.js
```

If you skip this step, the `/_termin/providers/spectrum/bundle.js` route returns a 404 with a message telling you to run the build. The hello-world page renders the SSR fallback (or stays blank, depending on shell behavior).

## Step 3 — Compile hello.termin

```bash
cd ~/work/termin-compiler
python -m termin.cli compile examples/hello.termin -o /tmp/hello.termin.pkg
```

Expected: `Wrote /tmp/hello.termin.pkg (...)`.

## Step 4 — Run the runtime, hello bound to spectrum

The deploy config is checked in at `examples-dev/hello_spectrum.deploy.json`:

```bash
cd ~/work/termin-compiler
python -m termin.cli serve /tmp/hello.termin.pkg \
    --deploy examples-dev/hello_spectrum.deploy.json \
    --port 8765
```

Expected: `Uvicorn running on http://0.0.0.0:8765`. Leave it running.

If you see errors about the spectrum provider not being registered, the entry point didn't load — re-run step 1's sanity-check. If you see errors about `termin-compiler>=0.9.0` not being satisfied, you're on `main` (0.8.1) — switch to `feature/v0.9-presentation` first.

## Step 5 — Verify the loop in a browser

In a separate terminal (still on WSL):

```bash
# Check the bundle is being served:
curl -s -o /dev/null -w "%{http_code} %{size_download}B\n" \
    http://localhost:8765/_termin/providers/spectrum/bundle.js
# Expected: 200, with size matching what `ls -lh` showed in step 2.

# Check the bundle is in the discovery list:
curl -s http://localhost:8765/_termin/presentation/bundles | python3 -m json.tool
# Expected: ten "spectrum" entries, all with the same URL above.

# Check the page-data endpoint:
curl -s "http://localhost:8765/_termin/page-data?path=/hello" | python3 -m json.tool | head -30
# Expected: JSON with component_tree_ir, bound_data, principal_context,
# subscriptions_to_open. The component tree contains a text node with
# "Hello, World".

# Check the shell endpoint:
curl -s "http://localhost:8765/_termin/shell?path=/hello" | head -30
# Expected: HTML with <div id="termin-root"></div> + a <script> tag
# with the embedded bootstrap JSON + a <script src="/_termin/providers/
# spectrum/bundle.js"> reference.
```

Open a browser and visit:

```
http://localhost:8765/_termin/shell?path=/hello
```

Expected page contents:

- The text **"Hello, World"** rendered inside a `<main>` element with a `data-termin-contract="presentation-base.page"` attribute on it. The text is in a `<span>` with `data-termin-contract="presentation-base.text"`.
- Open DevTools → Console: you should see `[termin-spectrum] registered renderers (v0.1.0 — page + text live)` followed by `[Termin] Client runtime 0.3.0 initialized` (or similar).
- Open DevTools → Network: a request to `/_termin/providers/spectrum/bundle.js` returned 200, content-type `application/javascript`. A request to `/_termin/page-data?path=/hello` returned 200 with the bootstrap JSON.

That's the proof: the runtime served a bootstrap payload, the spectrum bundle loaded, the bundle's `__app_shell__` renderer consumed the payload, and the React tree rendered the `text` node into the `#termin-root` container.

Visiting `http://localhost:8765/hello` (without the `/_termin/shell` prefix) hits the legacy SSR path which still serves Tailwind-default-rendered HTML — the page-route cut-over to B' shell is a separate slice. That's expected and not a regression.

## Step 6 — (Optional) Test SPA navigation

The shell endpoint is one path. To prove `Termin.navigate(...)` works:

```bash
# In the running browser tab's DevTools console:
await Termin.navigate("/hello");
```

Expected: the page re-renders without a full reload. Browser history should show a new `/hello` entry; `history.back()` returns to the previous shell URL. (For a single-page hello-world there's only one path; SPA navigation is more interesting once a multi-page example is wired up.)

## Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| `pip install -e .` fails with "termin-compiler>=0.9.0 not found" | termin-compiler not installed in the active venv | Run `pip install -e .` from `termin-compiler/` first |
| Entry-point sanity check returns `[]` | Editable install of spectrum provider wasn't picked up | `pip install -e .` again from `termin-spectrum-provider/`; ensure the same venv is active |
| `/_termin/providers/spectrum/bundle.js` returns 404 with "build" hint | `npm run build` not run, or bundle file missing | Re-run `npm run build` in `termin-spectrum-provider/` |
| Bundle URL in discovery list points to a CDN | Deploy config has a `bundle_url_override` set | Remove the override, or trust the CDN URL is reachable |
| Browser console: "Termin global not present" | The bundle loaded before termin.js, race condition | Hard-reload (Ctrl+Shift+R); termin.js is loaded synchronously before bundles |
| Browser console: bundle 404 + then "no renderer for contract" | Build was skipped, fallback to placeholder | Run step 2 |
| Page renders Tailwind HTML instead of Spectrum | You hit `/hello` instead of `/_termin/shell?path=/hello` | Use the shell URL — the page-route cut-over to default-to-shell is a separate slice |

## What this verifies

- `pip install -e ../termin-spectrum-provider` registers an entry point under `termin.providers`
- The Termin runtime's `_discover_external_providers` calls `register_spectrum`
- The deploy config's `bindings.presentation."presentation-base"` resolves the spectrum factory
- `_populate_presentation_providers` instantiates the provider and binds it to all ten `presentation-base.*` contracts
- The bundle-discovery endpoint returns the spectrum URLs
- The bundle-serving route reads `termin_spectrum/static/bundle.js` from the editable-installed package directory and serves it with `application/javascript`
- The shell endpoint returns an HTML page with the bootstrap JSON island + script tags pointing at termin.js and the bundle
- The page-data endpoint returns the same bootstrap shape over JSON for SPA navigation
- The Spectrum bundle's `index.tsx` calls `Termin.registerRenderer("__app_shell__", ...)` and per-contract renderers
- The shell renderer walks the component-tree IR and dispatches each node to its registered renderer
- The `text` renderer renders the literal string from the IR's `props.value`

That's the full server-side and client-side B' loop, end-to-end, with one provider.

## What this doesn't verify (yet)

- The page-route cut-over (visiting `/hello` directly should serve the shell when bound provider is CSR-only)
- WebSocket subscription dispatch into provider-registered handlers (no contract uses subscriptions in hello-world)
- Action submission via `Termin.action()` (hello-world has no buttons / forms)
- Theme application (Spectrum's `<Provider>` wrapper isn't active until a Spectrum primitive renders — currently the bundle uses plain HTML elements for `text` + `page`)
- Any contract beyond `text` and `page` (the other eight render labeled placeholders)

These land in subsequent slices — track them in the journal's "what's next" section.
