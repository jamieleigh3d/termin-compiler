from setuptools import setup, find_packages

setup(
    name="termin-compiler",
    version="0.9.0",
    description="Termin: A secure-by-construction application compiler",
    author="Jamie-Leigh Blake",
    packages=find_packages(),
    python_requires=">=3.10",
    install_requires=[
        # Compiler
        "click>=8.0",
        "tatsu>=5.8",
        # Runtime (termin_runtime)
        "fastapi>=0.100.0",
        "uvicorn>=0.23.0",
        "websockets>=12.0",
        "aiosqlite>=0.19.0",
        "jinja2>=3.1.0",
        "python-multipart>=0.0.6",
        "cel-python>=0.5.0",
        "httpx>=0.25.0",
        # v0.9 Step Zero: markdown sanitizer for the
        # presentation-base.markdown contract envelope (BRD #2 §7.3).
        "markdown-it-py>=3.0.0",
        # v0.9 Phase 5c.1: contract package format. Loader for
        # YAML-shaped contract packages per BRD #2 §10 / Appendix C.
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
        "termin_runtime": ["static/*.js", "static/*.css"],
    },
    entry_points={
        "console_scripts": [
            "termin=termin.cli:main",
        ],
        # v0.9 Phase 5b.3: register the first-party tailwind-default
        # SSR presentation provider via the same `termin.providers`
        # entry-point group external providers (e.g. termin-spectrum-
        # provider) use. The shape mirrors that contract — termin_runtime
        # discovers it via _discover_external_providers at app startup.
        # Tailwind is also registered as a built-in via register_builtins;
        # the registry's register() is overwrite-safe, so the double
        # registration is harmless. Splitting it this way means a future
        # operator who installs an alternative Tailwind plug-in can set
        # the deploy config to bind that product instead, exercising the
        # same pluggability surface Spectrum uses.
        "termin.providers": [
            "tailwind-default = termin_runtime.providers.builtins.presentation_tailwind_default:register_tailwind_default",
        ],
    },
)
