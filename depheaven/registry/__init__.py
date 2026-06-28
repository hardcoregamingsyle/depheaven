"""Registry clients for version lookup."""

from .pypi import get_latest_version as pypi_latest
from .npm import get_latest_version as npm_latest

__all__ = ["pypi_latest", "npm_latest"]
