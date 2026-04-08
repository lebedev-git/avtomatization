from __future__ import annotations

import uvicorn

from gemini_proxy.config import get_settings


if __name__ == "__main__":
    settings = get_settings()
    uvicorn.run(
        "gemini_proxy.main:app",
        host=settings.app_host,
        port=settings.app_port,
        log_level=settings.app_log_level,
        reload=False,
    )
