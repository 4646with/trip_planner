"""API路由模块"""

from .map_agents_router import router as map_agents_router
from .photo_router import router as photo_router

__all__ = ["map_agents_router", "photo_router"]
