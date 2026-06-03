"""Pytest root config.

Lives at the repo root so the `app` package is importable from tests, and
forces Qt into headless ``offscreen`` mode so GUI smoke tests run without a
display server.
"""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
