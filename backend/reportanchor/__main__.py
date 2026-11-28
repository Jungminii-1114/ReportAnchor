"""실행: `.venv\\Scripts\\python -m reportanchor` (backend 폴더에서)."""

import logging


def main() -> None:
    import uvicorn

    from .app import create_app
    from .config import Settings

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    settings = Settings.from_env()
    app = create_app(settings)
    # 127.0.0.1에만 바인딩한다(같은 네트워크의 다른 기기에서 접근 불가).
    uvicorn.run(app, host="127.0.0.1", port=settings.port, log_level="info")


if __name__ == "__main__":
    main()
