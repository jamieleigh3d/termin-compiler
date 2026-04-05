from setuptools import setup, find_packages

setup(
    name="termin-compiler",
    version="0.1.0",
    description="Termin: A secure-by-construction application compiler",
    author="Jamie-Leigh Blake",
    packages=find_packages(),
    python_requires=">=3.10",
    install_requires=[
        # Compiler
        "click>=8.0",
        "tatsu>=5.8",
        "lark>=1.1.0",
        # Runtime (termin_runtime)
        "fastapi>=0.100.0",
        "uvicorn>=0.23.0",
        "websockets>=12.0",
        "aiosqlite>=0.19.0",
        "jinja2>=3.1.0",
        "python-multipart>=0.0.6",
        "pyjexl>=0.3.0",
    ],
    package_data={
        "termin": ["termin.peg", "grammar.lark"],
        "termin_runtime": ["static/*.js"],
    },
    entry_points={
        "console_scripts": [
            "termin=termin.cli:main",
        ],
    },
)
