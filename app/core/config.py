from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    DATABASE_URL: str
    GOOGLE_CLIENT_ID: str
    GOOGLE_CLIENT_SECRET: str
    GOOGLE_REDIRECT_URI: str
    JWT_SECRET: str
    GEMINI_API_KEY: str
    REDIS_URL: str = "redis://redis:6379/0"
    FRONTEND_URL: str = "http://localhost:3000"
    SENDGRID_API_KEY: str = ""
    SENDGRID_FROM_EMAIL: str = ""
    JWT_ALGORITHM: str = "HS256"
    JWT_EXPIRE_MINUTES: int = 60 * 24 * 7  # 7 days

    class Config:
        env_file = ".env"


settings = Settings()
