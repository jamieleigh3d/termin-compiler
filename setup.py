from setuptools import setup, find_packages

setup(
    name="termin-compiler",
    version="0.9.3",
    description="Termin: A secure-by-construction application compiler",
    author="Jamie-Leigh Blake",
    packages=find_packages(),
    python_requires=">=3.10",
    install_requires=[
        # Phase 7 slice 7.1: contract Protocols, IR types, deploy-config
        # parser, expression evaluator, confidentiality, identity value
        # types, errors, validation, and state rules live in termin-core.
        # Pinned >=0.9.0,<0.10 for the duration of v0.9 development; the
        # pin tightens to a specific version when Phase 7 closes.
        "termin-core>=0.9.0,<0.10",
        # Phase 7 slice 7.3: the FastAPI hosting layer + IO-bound
        # builtins (sqlite storage, Anthropic compute, Tailwind SSR,
        # channel stubs) and the static client assets are in the
        # termin-server sibling package. The slice 7.5 cleanup
        # dropped the back-compat termin_runtime/* shim layer; new
        # code imports from termin_server directly. termin-server
        # pulls in fastapi, uvicorn, aiosqlite, jinja2, websockets,
        # httpx, anthropic transitively.
        "termin-server>=0.9.0,<0.10",
        # Compiler
        "click>=8.0",
        "tatsu>=5.8",
        # `termin serve <pkg>` uses uvicorn directly. The bulk of
        # the runtime moved to termin-server (which also pulls
        # uvicorn standard extras) in slice 7.3, but the serve CLI
        # still lives here in v0.9. Slice 7.5 may move serve to a
        # termin-server CLI; until then this stays declared.
        "uvicorn>=0.23.0",
        # v0.9 Step Zero: markdown sanitizer for the
        # presentation-base.markdown contract envelope (BRD #2 §7.3).
        # Compiler reads the envelope at validation time.
        "markdown-it-py>=3.0.0",
        # v0.9 Phase 5c.1: contract package format. Loader for
        # YAML-shaped contract packages per BRD #2 §10 / Appendix C.
        # Compiler reads contract packages at parse time.
        "pyyaml>=6.0",
    ],
    extras_require={
        # Test dependencies. Install with: pip install -e .[test]
        # pytest-asyncio is required for the runtime / WebSocket /
        # agent test suites; without it, pytest emits "Unknown config
        # option: asyncio_mode" warnings against the asyncio_mode
        # setting in pyproject.toml.
        "test": [
            "pytest>=8.0",
            "pytest-asyncio>=0.21",
            "pytest-cov>=4.0",
        ],
    },
    package_data={
        "termin": ["termin.peg"],
        # Slice 7.3 of Phase 7 (2026-04-30): static assets moved with
        # the runtime to termin-server's package_data. The compiler's
        # package_data carries only the PEG grammar.
    },
    entry_points={
        "console_scripts": [
            "termin=termin.cli:main",
        ],
        # Slice 7.3 of Phase 7 (2026-04-30): the tailwind-default
        # provider entry point moved with the rest of the runtime to
        # termin-server. termin-compiler no longer registers any
        # provider entry points — the compiler is no longer the
        # hosting layer. Spectrum and other external providers
        # continue to register through the same termin.providers
        # group; termin-server's app discovers them at startup.
    },
)
