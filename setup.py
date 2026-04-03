from setuptools import setup, find_packages

setup(
    name="termin-compiler",
    version="0.1.0",
    description="Termin: A secure-by-construction application compiler",
    author="Jamie-Leigh Blake",
    packages=find_packages(),
    python_requires=">=3.10",
    install_requires=[
        "click>=8.0",
        "jinja2>=3.1.0",
        "pyjexl>=0.3.0",
    ],
    entry_points={
        "console_scripts": [
            "termin=termin.cli:main",
        ],
    },
)
