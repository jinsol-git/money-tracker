"""Vercel 진입점. FastAPI 인스턴스의 이름은 app이어야 한다."""

from app.web import create_app

app = create_app()
