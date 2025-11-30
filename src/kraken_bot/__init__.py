"""Package initialization and shared metadata.

This module exposes :data:`APP_VERSION`, which is resolved from installed
package metadata when available (project name: ``kraken-trader``). When the
metadata cannot be found—such as when running directly from a source checkout—a
development placeholder (``"0.0.0-dev"``) is used instead. Update the project
version in ``pyproject.toml`` when publishing a new release so this value stays
in sync.
"""

from importlib import metadata


def _determine_version() -> str:
    """Return the application version string without raising during import."""

    try:
        return metadata.version("kraken-trader")
    except metadata.PackageNotFoundError:
        return "0.0.0-dev"


APP_VERSION: str = _determine_version()
__version__: str = APP_VERSION

__all__ = ["APP_VERSION", "__version__"]
