"""Build backend shim that makes requested editable installs self-contained.

Python 3.12 on macOS can ignore ``.pth`` files carrying Finder's hidden flag.
Setuptools' editable wheel relies on such a file, so a fresh ``uv run`` may fail
to import the project. Returning the regular wheel for PEP 660 requests keeps
the usual command reliable on macOS, Windows, and Linux.
"""

from setuptools import build_meta as _setuptools

build_sdist = _setuptools.build_sdist
build_wheel = _setuptools.build_wheel
get_requires_for_build_sdist = _setuptools.get_requires_for_build_sdist
get_requires_for_build_wheel = _setuptools.get_requires_for_build_wheel
prepare_metadata_for_build_wheel = _setuptools.prepare_metadata_for_build_wheel


def build_editable(
    wheel_directory: str,
    config_settings: dict[str, object] | None = None,
    metadata_directory: str | None = None,
) -> str:
    """Build a normal wheel when an installer requests an editable wheel."""

    return _setuptools.build_wheel(
        wheel_directory,
        config_settings=config_settings,
        metadata_directory=metadata_directory,
    )


def get_requires_for_build_editable(
    config_settings: dict[str, object] | None = None,
) -> list[str]:
    return _setuptools.get_requires_for_build_wheel(config_settings)


def prepare_metadata_for_build_editable(
    metadata_directory: str,
    config_settings: dict[str, object] | None = None,
) -> str:
    return _setuptools.prepare_metadata_for_build_wheel(
        metadata_directory,
        config_settings=config_settings,
    )
