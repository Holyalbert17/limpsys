from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    database_url: str = "postgresql://postgres:1@localhost:5432/limsys"
    jwt_secret: str = "cambie-este-secreto"
    jwt_access_minutes: int = 15
    jwt_refresh_days: int = 7
    env: str = "development"
    cors_origins: str = "http://localhost:4200"
    dev_return_otp: bool = True
    simular_pagos: bool = True
    captcha_dev_mode: bool = True
    captcha_dev_token: str = "dev-captcha"
    admin_cedula: str = "80000001"
    admin_password: str = "Admin#2026"
    rp_id: str = "localhost"
    rp_origins: str = "http://localhost:4200"
    google_maps_server_key: str = ""
    webhook_secret: str = "dev-webhook"
    storage_dir: str = "storage"
    rate_limit_login: int = 30
    app_name: str = "Limsys"

    @property
    def origenes_cors(self) -> list[str]:
        return [parte.strip() for parte in self.cors_origins.split(",") if parte.strip()]

    @property
    def origenes_webauthn(self) -> list[str]:
        return [parte.strip() for parte in self.rp_origins.split(",") if parte.strip()]

    @property
    def carpeta_archivos(self) -> Path:
        return Path(self.storage_dir)

    @property
    def es_desarrollo(self) -> bool:
        return self.env in {"development", "test"}


settings = Settings()
