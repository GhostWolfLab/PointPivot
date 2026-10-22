"""OKX AI Auto-Trading System - Configuration"""

import os
from pydantic_settings import BaseSettings
from cryptography.fernet import Fernet


class Settings(BaseSettings):
    # OKX
    okx_api_key: str = ""
    okx_secret_key: str = ""
    okx_passphrase: str = ""
    okx_simulated: bool = True

    # AI defaults
    ai_default_provider: str = "deepseek"
    ai_default_api_key: str = ""
    ai_default_base_url: str = "https://api.deepseek.com/v1"

    # Encryption
    encryption_key: str = ""

    # Server
    host: str = "0.0.0.0"
    port: int = 8000

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"


settings = Settings()


def get_fernet() -> Fernet:
    key = settings.encryption_key
    if not key:
        key = Fernet.generate_key().decode()
        settings.encryption_key = key
        # Persist the generated key to .env so it survives restarts
        env_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env")
        try:
            if os.path.exists(env_path):
                with open(env_path, "r", encoding="utf-8") as f:
                    content = f.read()
                if "ENCRYPTION_KEY" not in content:
                    with open(env_path, "a", encoding="utf-8") as f:
                        f.write(f"\nENCRYPTION_KEY={key}\n")
            else:
                with open(env_path, "w", encoding="utf-8") as f:
                    f.write(f"ENCRYPTION_KEY={key}\n")
            print(f"[INFO] Generated and saved ENCRYPTION_KEY to .env")
        except Exception as e:
            print(f"[WARN] Could not save ENCRYPTION_KEY to .env: {e}. Set it manually: ENCRYPTION_KEY={key}")
    return Fernet(key.encode() if isinstance(key, str) else key)


def encrypt_value(plain: str) -> str:
    if not plain:
        return ""
    return get_fernet().encrypt(plain.encode()).decode()


def decrypt_value(encrypted: str) -> str:
    if not encrypted:
        return ""
    try:
        return get_fernet().decrypt(encrypted.encode()).decode()
    except Exception:
        return ""
