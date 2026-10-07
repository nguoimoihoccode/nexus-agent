"""Compatibility helpers for runtime type inspection on Python 3.11."""

import sys
import typing

from typing_extensions import NotRequired, Required, TypedDict


def install_typing_extensions_aliases() -> None:
    """Use Pydantic-compatible typing constructs on Python versions before 3.12.

    Deep Agents 0.6 imports these constructs from ``typing``. Pydantic requires
    their ``typing_extensions`` implementations when generating graph schemas
    on Python 3.11.
    """
    if sys.version_info < (3, 12):
        typing.TypedDict = TypedDict
        typing.NotRequired = NotRequired
        typing.Required = Required
