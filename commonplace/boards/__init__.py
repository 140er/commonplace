"""Commonplace boards, loaded automatically by the gateway extension seam."""

from .rest import install
from .service import BoardService, BoardStore, needs_me, open_store
from .tools import register_tools

__all__ = ["BoardService", "BoardStore", "install", "needs_me", "open_store"]

register_tools()
