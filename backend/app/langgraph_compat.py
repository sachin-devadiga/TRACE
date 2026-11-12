"""Compatibility shim for LangGraph's optional ``ormsgpack`` dependency.

This machine's Application Control policy blocks the native ``ormsgpack`` DLL
used by ``langgraph-checkpoint``.  ``msgpack`` is already installed and works,
and its API is close enough for LangGraph's serializer.  Import this module
before importing anything from ``langgraph.checkpoint``.

On machines where ``ormsgpack`` imports normally, this module does nothing.
"""

from __future__ import annotations

import sys
import types
from typing import Any

__all__ = ["install_ormsgpack_compat"]


def _make_shim() -> types.ModuleType:
    import msgpack

    module = types.ModuleType("ormsgpack")

    class MsgpackEncodeError(Exception):
        """Raised when the compatibility serializer cannot encode an object."""

    module.Ext = msgpack.ExtType
    module.MsgpackEncodeError = MsgpackEncodeError

    # LangGraph bitwise-ORs these option flags. Values are irrelevant here
    # because msgpack handles the corresponding Python types via ``default``
    # and ``ext_hook``.
    module.OPT_NON_STR_KEYS = 1
    module.OPT_PASSTHROUGH_DATACLASS = 2
    module.OPT_PASSTHROUGH_DATETIME = 4
    module.OPT_PASSTHROUGH_ENUM = 8
    module.OPT_PASSTHROUGH_UUID = 16
    module.OPT_REPLACE_SURROGATES = 32

    def packb(
        data: Any,
        default: Any = None,
        option: int = 0,
    ) -> bytes:
        del option
        try:
            return msgpack.packb(
                data,
                default=default,
                use_bin_type=True,
                strict_types=False,
            )
        except Exception as exc:  # noqa: BLE001 - normalize for LangGraph
            raise MsgpackEncodeError(str(exc)) from exc

    def unpackb(
        data: bytes,
        ext_hook: Any = None,
        option: int = 0,
    ) -> Any:
        del option
        return msgpack.unpackb(
            data,
            ext_hook=ext_hook,
            raw=False,
            strict_map_key=False,
        )

    module.packb = packb
    module.unpackb = unpackb
    return module


def install_ormsgpack_compat() -> None:
    """Install the shim only if the native module cannot be imported."""
    try:
        import ormsgpack  # noqa: F401
    except (ImportError, OSError):
        sys.modules["ormsgpack"] = _make_shim()
