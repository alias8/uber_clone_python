from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="", extra="ignore")

    jwt_secret: str = "change-me-to-a-long-random-secret-in-production"
    jwt_expiration_ms: int = 2_592_000_000  # 30 days, matches application.properties
    cookie_secure: bool = False

    ride_request_limit_per_minute: int = 5
    auth_attempts_limit_per_15_min: int = 10


settings = Settings()
