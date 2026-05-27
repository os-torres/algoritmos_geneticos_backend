"""
Configuración centralizada — carga variables de entorno desde:
  .env        → valores base / producción (no se sube a git en este proyecto)
  .env.local  → overrides locales (siempre ignorado por git)

Patrón idéntico al usado en AppClasificacionGiecomAI.
"""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

# ── Carga de archivos ─────────────────────────────────────────────────────────
_base_dir = Path(__file__).parent
load_dotenv(_base_dir / ".env")                         # base / producción
load_dotenv(_base_dir / ".env.local", override=True)    # overrides locales

# ── Entorno ───────────────────────────────────────────────────────────────────
ENVIRONMENT: str = os.getenv("ENVIRONMENT", "development")
IS_PRODUCTION: bool = ENVIRONMENT == "production"

# ── Aplicación ────────────────────────────────────────────────────────────────
APP_NAME: str = os.getenv("APP_NAME", "HorarioGenetico")
AI_SERVICE_PORT: int = int(os.getenv("AI_SERVICE_PORT", "8000"))

# Ruta raíz en IIS (p.ej. /HorarioGenetico).
# En desarrollo local se deja vacía para que no haya prefijo.
ROOT_PATH: str = os.getenv("ROOT_PATH", "/HorarioGenetico" if IS_PRODUCTION else "")

# ── CORS ──────────────────────────────────────────────────────────────────────
_cors_raw: str = os.getenv("CORS_ORIGINS", "")
if _cors_raw.strip():
    CORS_ORIGINS: list[str] = [o.strip() for o in _cors_raw.split(",") if o.strip()]
else:
    # Producción sin CORS_ORIGINS explícito → denegar todo origen desconocido
    CORS_ORIGINS = [] if IS_PRODUCTION else ["*"]
