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
    },
)
