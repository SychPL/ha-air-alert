"""Expose the pure modules of the integration as package `air_alert`
without executing its __init__.py (which needs Home Assistant)."""
import pathlib
import sys
import types

_pkg = types.ModuleType("air_alert")
_pkg.__path__ = [str(pathlib.Path(__file__).parents[1] / "custom_components" / "air_alert")]
sys.modules["air_alert"] = _pkg

FIXTURES = pathlib.Path(__file__).parent / "fixtures"
