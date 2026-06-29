import os
from fastapi import Header, HTTPException


def verify_api_key(x_api_key: str = Header(...)) -> str:
    expected = os.getenv("API_KEY")
    if not expected or x_api_key != expected:
        raise HTTPException(status_code=401, detail="API Key inválida")
    return x_api_key
