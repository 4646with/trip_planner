import logging
import sys
from ..config import get_settings


def _configure_console_encoding():
    """Keep Windows consoles from crashing on characters they cannot encode."""
    for stream_name in ("stdout", "stderr"):
        stream = getattr(sys, stream_name)
        if hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(errors="replace")
            except Exception:
                pass


def setup_logging():
    """强力重置全局日志配置"""
    _configure_console_encoding()
    settings = get_settings()
    log_level_str = settings.log_level.upper()
    log_level = getattr(logging, log_level_str, logging.INFO)

    # 强制重置根 logging 配置
    logging.basicConfig(
        level=log_level,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        stream=sys.stdout,
        force=True,
    )

    # 移除 uvicorn 的默认 handler，让日志传播到 root
    for logger_name in ["uvicorn", "uvicorn.error", "uvicorn.access"]:
        u_logger = logging.getLogger(logger_name)
        u_logger.handlers.clear()
        u_logger.setLevel(log_level)
        u_logger.propagate = True

    # 确保所有应用日志都传播到 root
    for logger_name in ["trip_workers", "app"]:
        app_logger = logging.getLogger(logger_name)
        app_logger.setLevel(log_level)
        app_logger.propagate = True

    logging.info(f"🚀 日志系统强力初始化完成，等级: {log_level_str}")
