# Contributing to Termin

Thanks for your interest in contributing. This document covers how to propose changes, the sign-off requirement, and what reviewers look for.

## Getting started

1. **Read [termin.dev](https://termin.dev)** first to understand the project's scope and guarantees. Termin is deliberately narrow; proposals that expand the expressive surface get a lot of scrutiny.
2. **Open an issue before writing code** for anything non-trivial. A short problem description is much easier to review than a large PR built on a misunderstanding.
3. **Fork, branch, and develop.** Standard GitHub flow.

### Local development setup

The project uses two dependency lists:

- **`requirements.txt`** — runtime dependencies only. Sufficient to compile `.termin` files and serve compiled packages (`python -m termin.cli compile|serve`). Use this for production deployments where you only need the compiler/runtime.
- **`setup.py` `[test]` extras** — adds `pytest`, `pytest-asyncio`, and `pytest-cov`. Required to run the test suite, which exercises async paths (runtime, WebSocket, agent, migration tests).

The recommended dev setup pulls in both via an editable install:

```bash
python -m venv venv
source venv/bin/activate           # Linux/macOS
# or: venv\Scripts\activate         # Windows

pip install -e ".[test]"
```

This installs the package itself in editable mode (`-e`) so changes to source take effect without reinstall, and adds the `[test]` extras for the test suite.

If you skip the `[test]` extras and run pytest, you'll see `async def functions are not natively supported` on every async test, plus a `PytestConfigWarning: Unknown config option: asyncio_mode`. That's the missing `pytest-asyncio` plugin — fix it with the editable install above.

## Developer Certificate of Origin (DCO)

Every commit must be signed off to certify you have the right to contribute it under the project's license. This is the [Developer Certificate of Origin](https://developercertificate.org), the same mechanism used by the Linux kernel, Docker, and many other open-source projects. We do not use a CLA.

By signing off your commits, you certify the DCO text. Summary: you wrote the code, or you have the right to contribute it, and you agree it can be distributed under the project's Apache 2.0 license.

### How to sign off

Add a `Signed-off-by:` trailer to every commit message. Git can do this for you automatically with the `-s` flag:

```bash
git commit -s -m "Fix off-by-one in state transition check"
```

That produces a commit message like:

```
Fix off-by-one in state transition check

Signed-off-by: Alex Doe <alex@example.com>
```

Use your real name (or the name you publish under) and a real email address. GitHub-generated `noreply` addresses are acceptable.

If you forget the sign-off, you can add it to your most recent commit with:

```bash
git commit --amend --signoff
```

Or to a range of commits with:

```bash
git rebase --signoff HEAD~3
```

The DCO check runs on every pull request. PRs without sign-offs on every commit are not mergeable.

## Code style and testing

- **Python code follows PEP 8.** Keep lines reasonable; no hard line-length cap but excessively long lines get comments.
- **Tests are required for new behavior.** If you add a new DSL construct, IR field, or runtime feature, add tests that would fail without your change.
- **Run the full test suite before submitting.** `python -m pytest tests/` must pass with zero failures.
- **Compile-smoke-test before submitting.** `python -m termin.cli compile examples/warehouse.termin` must succeed. Tests can coexist with a broken compiler when they use pre-compiled artifacts.
- **Grammar changes go in `termin/termin.peg` first.** The PEG grammar is authoritative; update it before adjusting the parser.
- **IR schema changes require a corresponding conformance test.** The `docs/termin-ir-schema.json` is the machine-readable contract. Any new IR field without a conformance test will be rejected.

## Pull request checklist

Before marking your PR as ready for review:

- [ ] Every commit is signed off (`Signed-off-by:` trailer)
- [ ] Tests pass locally (`python -m pytest tests/`)
- [ ] `python -m termin.cli compile examples/warehouse.termin` succeeds
- [ ] Commit messages are clear and describe the "why," not just the "what"
- [ ] Changes to the DSL, grammar, or IR schema include a conformance test
- [ ] Changes that affect end-user behavior include a `CHANGELOG.md` entry

## Provider packaging

**First-party and third-party providers ship as separate packages.** This applies to every provider category — Identity, Storage, Compute, Channels, Presentation. The `termin-compiler` repository hosts the compiler, the reference runtime, and the contract Protocols any provider must satisfy. Provider implementations live in their own repositories with their own build pipelines, release cadences, and dependency stacks.

Concretely:

- A new Identity provider (e.g., Okta, Auth0, custom SSO) is a separate Python package with a `setup.py` declaring its dependencies, a factory function registering against the `IdentityProvider` Protocol, and its own CI / release.
- A new Presentation provider (e.g., Carbon Design System, GOV.UK, an in-house design system) is its own package. CSR-mode providers may ship a JS bundle as a static asset, an npm package, or a CDN-hosted artifact — that's the provider's choice. The `termin-compiler` repository does not gain a Node toolchain.
- Built-in providers shipped with the reference runtime (the SQLite storage provider, the stub identity provider, Tailwind-default presentation, etc.) are the only providers that live inside `termin-compiler`. Even these load through the same provider registry that third-party providers use — there is no special-case "built-in" code path.

This separation is load-bearing: it's how Tenet 4 (providers over primitives) is enforced operationally. The contract Protocols are the only stable surface; everything else is one realization of those contracts.

### Local development across multiple repositories

When you're working on a provider package alongside the reference runtime, use editable installs rather than git submodules or environment variables:

```bash
# Sibling-checkout layout
~/work/termin-compiler/
~/work/termin-carbon-provider/

# In your venv, install the sibling provider as editable so changes
# reflect immediately without reinstalling.
cd ~/work/termin-compiler
pip install -e ../termin-carbon-provider
```

Each repository's CI tests against published versions of its dependencies; local development swaps in editable installs to test changes across repositories. No submodules, no path-discovery code in either repository.

For Node-side providers (CSR bundles), use `npm link` or local `file:` paths in `package.json` for the equivalent workflow.

## Scope of contributions

Especially welcome:

- Bug reports with minimal repro cases
- Additional example `.termin` files demonstrating real use cases
- Documentation improvements and clarifications
- Conformance test additions (in the separate [termin-conformance](https://github.com/jamieleigh3d/termin-conformance) repo)
- Performance improvements with benchmarks
- Platform compatibility fixes (the reference runtime is developed on Windows and Linux; macOS fixes welcome)

Out of scope for the current project:

- Proposals that make the language generally Turing-complete or more expressive at the cost of the structural security guarantees
- Commercial features (paid tiers, license-gated capabilities, etc.)
- Third-party runtime implementations (those live in their own repositories and interact via the conformance suite)

## Security issues

Do not open public issues for security vulnerabilities. See [SECURITY.md](SECURITY.md) for the private disclosure process.

## License

By contributing, you agree that your contributions are licensed under the Apache License 2.0. See [LICENSE](LICENSE) for the full text.

## Questions

Use [GitHub Discussions](https://github.com/jamieleigh3d/termin-compiler/discussions) for open-ended questions. Use [GitHub Issues](https://github.com/jamieleigh3d/termin-compiler/issues) for specific bug reports and feature requests.
