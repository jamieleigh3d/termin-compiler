# Contributing to Termin

Thanks for your interest in contributing. This document covers how to propose changes, the sign-off requirement, and what reviewers look for.

## Getting started

1. **Read [termin.dev](https://termin.dev)** first to understand the project's scope and guarantees. Termin is deliberately narrow; proposals that expand the expressive surface get a lot of scrutiny.
2. **Open an issue before writing code** for anything non-trivial. A short problem description is much easier to review than a large PR built on a misunderstanding.
3. **Fork, branch, and develop.** Standard GitHub flow.

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
