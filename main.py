"""
Punto de entrada del backend — HorarioGenetico AI Service.

Desarrollo local:
  python main.py

Producción IIS:
  IIS + HttpPlatformHandler lanza uvicorn.exe directamente usando web.config.
  Este archivo NO se usa en producción; se incluye solo para pruebas manuales.

  Si necesitas probar producción localmente:
    ENVIRONMENT=production python main.py
"""

import uvicorn
from api import app  # noqa: F401 — expone el objeto app para 'uvicorn api:app'

if __name__ == "__main__":
    from config import AI_SERVICE_PORT, IS_PRODUCTION

    uvicorn.run(
        "api:app",
        host="127.0.0.1" if IS_PRODUCTION else "0.0.0.0",
        port=AI_SERVICE_PORT,
        reload=not IS_PRODUCTION,
        log_level="warning" if IS_PRODUCTION else "info",
    )
