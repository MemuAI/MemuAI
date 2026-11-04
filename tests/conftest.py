"""Shared test setup (E18).

The suite degrades gracefully when optional packages are missing: tests that
need them skip instead of failing collection. Install everything with::

    python -m pip install -r tests/requirements-test.txt

A missing *required* package (fastapi, pytest itself) still fails loudly —
that is a broken environment, not an optional extra.
"""
import importlib.util


def _missing(name):
    return importlib.util.find_spec(name) is None


# Helpful import error when the core web stack is absent, instead of a
# bare ModuleNotFoundError deep inside app imports.
if _missing("fastapi"):
    raise ImportError(
        "fastapi is required to run the MemuAI tests: "
        "python -m pip install -r tests/requirements-test.txt"
    )
