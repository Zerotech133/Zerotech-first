"""Application entry point for the read-only BFF.

The runtime uses the standard-library HTTP server so the prototype remains
runnable before optional FastAPI dependencies are installed.
"""
try:
    from .api import AdminRequestHandler, create_server
except ImportError:  # 支持在 backend 目录内直接执行 app.py
    from api import AdminRequestHandler, create_server

__all__ = ["AdminRequestHandler", "create_server"]

if __name__ == "__main__":
    create_server().serve_forever()
