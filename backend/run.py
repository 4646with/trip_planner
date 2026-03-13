"""启动脚本"""

import uvicorn
import argparse
from app.config import get_settings

if __name__ == "__main__":
    # 解析命令行参数
    parser = argparse.ArgumentParser(description="启动智能旅行助手API服务器")
    parser.add_argument("--no-reload", action="store_true", help="禁用热重载")
    args = parser.parse_args()
    
    settings = get_settings()
    
    uvicorn.run(
        "app.api.main:app",
        host=settings.host,
        port=settings.port,
        reload=not args.no_reload,
        log_level=settings.log_level.lower()
    )

