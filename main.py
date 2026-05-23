"""
Punto de entrada del backend.
Ejecutar: python main.py
O directamente: uvicorn main:app --reload --host 0.0.0.0 --port 8000
"""

import uvicorn
from api import app

if __name__ == "__main__":
    uvicorn.run(
        "api:app",
        host="0.0.0.0",
        port=8000,
        reload=True,
        log_level="info",
    )
