"""
API REST — Optimizador de Horarios con Algoritmo Genético
Universidad de la Amazonia · Facultad de Ingeniería · Ingeniería de Sistemas
"""

import datetime
import threading
import uuid
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from pydantic import BaseModel, Field

from config import CORS_ORIGINS, IS_PRODUCTION, ROOT_PATH
from genetic_algorithm import crear_ag_desde_store
from models import ParametrosAG
from storage import store

# ---------------------------------------------------------------------------
# Aplicación
# ---------------------------------------------------------------------------

app = FastAPI(
    title="Horario Genético API",
    description="Optimización de horarios universitarios con algoritmos genéticos.",
    version="1.0.0",
    # En producción se deshabilita Swagger/ReDoc para no exponer la documentación
    docs_url=None if IS_PRODUCTION else "/docs",
    redoc_url=None if IS_PRODUCTION else "/redoc",
    openapi_url=None if IS_PRODUCTION else "/openapi.json",
)

# ---------------------------------------------------------------------------
# Middleware: StripPrefix (solo cuando IIS agrega un prefijo de ruta)
# ---------------------------------------------------------------------------
# IIS HttpPlatformHandler sirve la app bajo /HorarioGenetico, pero uvicorn
# recibe las peticiones con ese prefijo incluido. Este middleware lo elimina
# para que las rutas de FastAPI (/api/...) coincidan correctamente.
#
# IMPORTANTE: se implementa como middleware ASGI puro (no BaseHTTPMiddleware)
# porque BaseHTTPMiddleware solo intercepta HTTP y no WebSocket (por si en el
# futuro se agrega algún endpoint ws://).

if ROOT_PATH:
    _prefix_bytes = ROOT_PATH.encode()

    class StripPrefixMiddleware:
        def __init__(self, app):
            self.app = app

        async def __call__(self, scope, receive, send):
            if scope.get("type") in ("http", "websocket"):
                path: str = scope.get("path", "")
                if path.startswith(ROOT_PATH):
                    stripped = path[len(ROOT_PATH):]
                    scope["path"] = stripped if stripped.startswith("/") else "/" + stripped
                    # raw_path es la ruta binaria usada por algunos routers internos
                    raw: bytes = scope.get("raw_path", b"")
                    if raw.startswith(_prefix_bytes):
                        stripped_raw = raw[len(_prefix_bytes):]
                        scope["raw_path"] = (
                            stripped_raw if stripped_raw.startswith(b"/")
                            else b"/" + stripped_raw
                        )
            await self.app(scope, receive, send)

    app.add_middleware(StripPrefixMiddleware)

# ---------------------------------------------------------------------------
# Middleware: CORS
# ---------------------------------------------------------------------------

app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---------------------------------------------------------------------------
# Middleware: GZip (comprime respuestas > 1 KB)
# ---------------------------------------------------------------------------

app.add_middleware(GZipMiddleware, minimum_size=1000)


# ---------------------------------------------------------------------------
# Schemas Pydantic
# ---------------------------------------------------------------------------

class MateriaIn(BaseModel):
    nombre:               str       = Field(..., min_length=2, max_length=100)
    semestre:             int       = Field(..., ge=1, le=10)
    grupo:                str       = Field(..., min_length=1, max_length=10)
    creditos:             int       = Field(..., ge=1, le=10)
    bloques:              list[int] = Field(..., min_length=1)
    num_alumnos:          int       = Field(default=30, ge=1, le=500)
    requiere_laboratorio: bool      = False

class MateriaUpdate(BaseModel):
    nombre:               str       | None = Field(None, min_length=2, max_length=100)
    semestre:             int       | None = Field(None, ge=1, le=10)
    grupo:                str       | None = Field(None, min_length=1, max_length=10)
    creditos:             int       | None = Field(None, ge=1, le=10)
    bloques:              list[int] | None = None
    num_alumnos:          int       | None = Field(None, ge=1, le=500)
    requiere_laboratorio: bool      | None = None

class ProfesorIn(BaseModel):
    nombre:             str       = Field(..., min_length=2, max_length=100)
    materias_ids:       list[int] = Field(default_factory=list)
    franjas_preferidas: list[int] = Field(default_factory=list)
    franjas_bloqueadas: list[int] = Field(default_factory=list)

class ProfesorUpdate(BaseModel):
    nombre:             str       | None = Field(None, min_length=2, max_length=100)
    materias_ids:       list[int] | None = None
    franjas_preferidas: list[int] | None = None
    franjas_bloqueadas: list[int] | None = None

class SalonIn(BaseModel):
    nombre:    str = Field(..., min_length=2, max_length=100)
    capacidad: int = Field(..., ge=1, le=1000)
    tipo:      str = Field(default="aula", pattern=r"^(aula|laboratorio|auditorio)$")

class SalonUpdate(BaseModel):
    nombre:    str | None = Field(None, min_length=2, max_length=100)
    capacidad: int | None = Field(None, ge=1, le=1000)
    tipo:      str | None = Field(None, pattern=r"^(aula|laboratorio|auditorio)$")

class FranjaIn(BaseModel):
    dia:         str = Field(..., description="Ej: Lunes, Martes…")
    hora_inicio: str = Field(..., pattern=r"^\d{2}:\d{2}$", description="HH:MM")
    hora_fin:    str = Field(..., pattern=r"^\d{2}:\d{2}$", description="HH:MM")

class FranjaUpdate(BaseModel):
    dia:         str | None = None
    hora_inicio: str | None = Field(None, pattern=r"^\d{2}:\d{2}$")
    hora_fin:    str | None = Field(None, pattern=r"^\d{2}:\d{2}$")

class ParamsRequest(BaseModel):
    tam_poblacion:    int   = Field(default=100,  ge=10,  le=500)
    num_generaciones: int   = Field(default=200,  ge=10,  le=1000)
    prob_cruzamiento: float = Field(default=0.85, ge=0.1, le=1.0)
    prob_mutacion:    float = Field(default=0.10, ge=0.0, le=1.0)
    num_elite:        int   = Field(default=2,    ge=0,   le=20)
    tam_torneo:       int   = Field(default=5,    ge=2,   le=20)
    paciencia:        int   = Field(default=30,   ge=5,   le=200)
    semilla:           int | None = None
    # Filtra qué semestres participan en la optimización (None / [] = todos)
    semestres_filtro:  list[int] | None = Field(default=None)

class ImportBody(BaseModel):
    """Cuerpo para importar datos masivos desde JSON."""
    accion:     str        = "reemplazar"  # "reemplazar" | "agregar"
    materias:   list[dict] = Field(default_factory=list)
    profesores: list[dict] = Field(default_factory=list)
    salones:    list[dict] | None = None
    franjas:    list[dict] | None = None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _require_ready(semestres_filtro: list[int] | None = None):
    """Lanza 422 si los datos no están listos para optimizar."""
    sesiones = store.get_sesiones(semestres=semestres_filtro)
    ranuras  = len(store.franjas) * len(store.salones)
    if not sesiones or not store.salones or not store.franjas:
        raise HTTPException(
            status_code=422,
            detail=(
                f"Datos insuficientes para optimizar. "
                f"Sesiones: {len(sesiones)}, "
                f"Ranuras disponibles: {ranuras}. "
                f"Se necesita al menos una sesión y suficientes ranuras."
            ),
        )
    if ranuras < len(sesiones):
        raise HTTPException(
            status_code=422,
            detail=(
                f"No hay suficientes ranuras ({ranuras}) para las sesiones "
                f"({len(sesiones)}). Agrega más salones o franjas horarias."
            ),
        )


# ===========================================================================
# RUTAS — Información general
# ===========================================================================

@app.get("/api/datos", summary="Resumen de todos los datos actuales")
async def get_datos() -> dict[str, Any]:
    return {
        "materias":   [_m(m)  for m in store.materias],
        "profesores": [_p(p)  for p in store.profesores],
        "salones":    [_s(s)  for s in store.salones],
        "franjas":    [_f(f)  for f in store.franjas],
        "sesiones":   [_ses(s) for s in store.get_sesiones()],
        "resumen":    store.resumen(),
    }


@app.post("/api/datos/reset", summary="Restaurar datos de muestra")
async def reset_datos() -> dict[str, str]:
    store.reset_to_defaults()
    return {"mensaje": "Datos restaurados a los valores de muestra."}


@app.post("/api/importar", summary="Importar materias y profesores desde JSON")
async def importar_datos(body: ImportBody) -> dict[str, Any]:
    """
    Importa masivamente materias y/o profesores desde un JSON.

    **Formato del body:**
    ```json
    {
      "accion": "reemplazar",
      "materias": [
        {"nombre":"Cálculo I","semestre":1,"grupo":"A","creditos":4,"bloques":[3,2]}
      ],
      "profesores": [
        {
          "nombre": "Dr. Matemáticas",
          "materias": [["Cálculo I","A"],["Cálculo I","B"]],
          "franjas_preferidas": []
        }
      ]
    }
    ```
    - `accion`: `"reemplazar"` reemplaza todo; `"agregar"` añade a lo existente.
    - `materias[].bloques`: lista de horas por sesión, p.ej. `[3,2]` = 5h/sem.
    - `profesores[].materias`: lista de `[nombre_materia, grupo]`.
    """
    try:
        resultado = store.importar(body.model_dump(exclude_none=False))
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    return {
        "ok":         True,
        "importado":  resultado,
        "resumen":    store.resumen(),
    }


@app.get("/api/acerca-de", summary="Información del proyecto")
async def acerca_de() -> dict[str, Any]:
    return {
        "proyecto":    "Optimización de Horarios Universitarios con Algoritmos Genéticos",
        "universidad": "Universidad de la Amazonia",
        "facultad":    "Facultad de Ingeniería",
        "programa":    "Ingeniería de Sistemas",
        "materia":     "Inteligencia Artificial",
        "tema":        "Algoritmos Genéticos",
        "docente":     "Dr. Jesús Emilio Pinto",
        "integrantes": ["Oscar Ivan Torres", "Andres Carrillo", "Mercy Florez"],
        "version":     "1.0.0",
        "tecnologias": {"backend": "Python + FastAPI", "frontend": "Angular 21"},
    }


# ===========================================================================
# RUTAS — CRUD Materias
# ===========================================================================

@app.get("/api/materias", summary="Listar materias")
async def listar_materias():
    return [_m(m) for m in store.materias]


@app.post("/api/materias", summary="Crear materia", status_code=201)
async def crear_materia(body: MateriaIn):
    mat = store.add_materia(
        body.nombre, body.semestre, body.grupo, body.creditos, body.bloques,
        num_alumnos=body.num_alumnos, requiere_laboratorio=body.requiere_laboratorio,
    )
    return _m(mat)


@app.put("/api/materias/{mid}", summary="Actualizar materia")
async def actualizar_materia(mid: int, body: MateriaUpdate):
    campos = {k: v for k, v in body.model_dump().items() if v is not None}
    mat = store.update_materia(mid, **campos)
    if mat is None:
        raise HTTPException(404, f"Materia {mid} no encontrada.")
    return _m(mat)


@app.delete("/api/materias/{mid}", summary="Eliminar materia")
async def eliminar_materia(mid: int):
    if not store.delete_materia(mid):
        raise HTTPException(404, f"Materia {mid} no encontrada.")
    return {"mensaje": f"Materia {mid} eliminada."}


# ===========================================================================
# RUTAS — CRUD Profesores
# ===========================================================================

@app.get("/api/profesores", summary="Listar profesores")
async def listar_profesores():
    return [_p(p) for p in store.profesores]


@app.post("/api/profesores", summary="Crear profesor", status_code=201)
async def crear_profesor(body: ProfesorIn):
    prof = store.add_profesor(
        body.nombre, body.materias_ids, body.franjas_preferidas,
        franjas_bloqueadas=body.franjas_bloqueadas,
    )
    return _p(prof)


@app.put("/api/profesores/{pid}", summary="Actualizar profesor")
async def actualizar_profesor(pid: int, body: ProfesorUpdate):
    campos = {k: v for k, v in body.model_dump().items() if v is not None}
    prof = store.update_profesor(pid, **campos)
    if prof is None:
        raise HTTPException(404, f"Profesor {pid} no encontrado.")
    return _p(prof)


@app.delete("/api/profesores/{pid}", summary="Eliminar profesor")
async def eliminar_profesor(pid: int):
    if not store.delete_profesor(pid):
        raise HTTPException(404, f"Profesor {pid} no encontrado.")
    return {"mensaje": f"Profesor {pid} eliminado."}


# ===========================================================================
# RUTAS — CRUD Salones
# ===========================================================================

@app.get("/api/salones", summary="Listar salones")
async def listar_salones():
    return [_s(s) for s in store.salones]


@app.post("/api/salones", summary="Crear salón", status_code=201)
async def crear_salon(body: SalonIn):
    salon = store.add_salon(body.nombre, body.capacidad, tipo=body.tipo)
    return _s(salon)


@app.put("/api/salones/{sid}", summary="Actualizar salón")
async def actualizar_salon(sid: int, body: SalonUpdate):
    campos = {k: v for k, v in body.model_dump().items() if v is not None}
    salon = store.update_salon(sid, **campos)
    if salon is None:
        raise HTTPException(404, f"Salón {sid} no encontrado.")
    return _s(salon)


@app.delete("/api/salones/{sid}", summary="Eliminar salón")
async def eliminar_salon(sid: int):
    if not store.delete_salon(sid):
        raise HTTPException(404, f"Salón {sid} no encontrado.")
    return {"mensaje": f"Salón {sid} eliminado."}


# ===========================================================================
# RUTAS — CRUD Franjas horarias
# ===========================================================================

@app.get("/api/franjas", summary="Listar franjas horarias")
async def listar_franjas():
    return [_f(f) for f in store.franjas]


@app.post("/api/franjas", summary="Crear franja horaria", status_code=201)
async def crear_franja(body: FranjaIn):
    franja = store.add_franja(body.dia, body.hora_inicio, body.hora_fin)
    return _f(franja)


@app.put("/api/franjas/{fid}", summary="Actualizar franja horaria")
async def actualizar_franja(fid: int, body: FranjaUpdate):
    campos = {k: v for k, v in body.model_dump().items() if v is not None}
    franja = store.update_franja(fid, **campos)
    if franja is None:
        raise HTTPException(404, f"Franja {fid} no encontrada.")
    return _f(franja)


@app.delete("/api/franjas/{fid}", summary="Eliminar franja horaria")
async def eliminar_franja(fid: int):
    if not store.delete_franja(fid):
        raise HTTPException(404, f"Franja {fid} no encontrada.")
    return {"mensaje": f"Franja {fid} eliminada."}


# ===========================================================================
# RUTAS — Sesiones (solo lectura, generadas automáticamente)
# ===========================================================================

@app.get("/api/sesiones", summary="Sesiones generadas automáticamente")
async def listar_sesiones():
    return [_ses(s) for s in store.get_sesiones()]


# ===========================================================================
# RUTAS — Optimización (patrón job asíncrono)
#
# El POST inicia el AG en un hilo de fondo y devuelve un job_id de inmediato,
# evitando el timeout de 120 s de proxies/CDN cuando hay muchos semestres.
# El cliente hace polling a GET /api/optimizar/estado/{job_id} cada pocos
# segundos hasta que el estado cambia a "completado" o "error".
# ===========================================================================

# Almacén en memoria: job_id → estado del trabajo
_jobs: dict[str, dict] = {}
_MAX_JOBS = 10          # máximo de trabajos completados/fallidos en memoria


def _limpiar_jobs_viejos() -> None:
    """Elimina los trabajos terminados más antiguos para no acumular memoria."""
    terminados = [jid for jid, j in _jobs.items()
                  if j["estado"] in ("completado", "error")]
    for jid in terminados[:-_MAX_JOBS]:
        _jobs.pop(jid, None)


def _ejecutar_ag(job_id: str, ag, params_dict: dict, semilla: int | None) -> None:
    """Corre el AG en un hilo separado y actualiza _jobs[job_id] en tiempo real."""
    historial: list[dict] = []
    resultado_final: dict = {}
    try:
        for gen_result in ag.evolucionar(semilla=semilla):
            historial.append({
                "generacion":       gen_result.numero,
                "mejor_fitness":    gen_result.mejor_fitness,
                "promedio_fitness": gen_result.promedio_fitness,
                "peor_fitness":     gen_result.peor_fitness,
                "conflictos":       gen_result.conflictos_mejor,
            })
            resultado_final = gen_result.to_dict()
            # Progreso visible en tiempo real para el cliente
            _jobs[job_id].update({
                "generacion_actual": gen_result.numero,
                "fitness_actual":    round(gen_result.mejor_fitness, 2),
                "conflictos_actual": gen_result.conflictos_mejor,
            })

        mejor_horario  = resultado_final.get("mejor_horario", [])
        mejor_fitness  = resultado_final.get("mejor_fitness", 0)
        conflictos_fin = resultado_final.get("conflictos_mejor", 0)

        store.save_ultimo_horario({
            "fecha":      datetime.datetime.now().isoformat(),
            "fitness":    mejor_fitness,
            "conflictos": conflictos_fin,
            "parametros": params_dict,
            "horario":    mejor_horario,
        })

        _jobs[job_id].update({
            "estado": "completado",
            "resultado": {
                "parametros":              params_dict,
                "generaciones_ejecutadas": len(historial),
                "historial":               historial,
                "mejor_horario":           mejor_horario,
                "mejor_fitness":           mejor_fitness,
                "conflictos_finales":      conflictos_fin,
                "razon_parada":            resultado_final.get("razon_parada", ""),
                "conflictos_detalle":      resultado_final.get("conflictos_detalle", []),
                "top_individuos":          resultado_final.get("top_individuos", []),
            },
        })
    except Exception as exc:
        _jobs[job_id].update({"estado": "error", "error": str(exc)})


@app.post("/api/optimizar", summary="Iniciar optimización genética (asíncrona)")
async def optimizar(params: ParamsRequest) -> dict[str, Any]:
    """
    Inicia el AG en un hilo de fondo y devuelve ``job_id`` de inmediato.
    Usa ``GET /api/optimizar/estado/{job_id}`` para consultar el progreso.
    """
    _require_ready(semestres_filtro=params.semestres_filtro)

    ag_params = ParametrosAG(
        tam_poblacion=params.tam_poblacion,
        num_generaciones=params.num_generaciones,
        prob_cruzamiento=params.prob_cruzamiento,
        prob_mutacion=params.prob_mutacion,
        num_elite=params.num_elite,
        tam_torneo=params.tam_torneo,
        paciencia=params.paciencia,
    )
    ag = crear_ag_desde_store(ag_params, semestres_filtro=params.semestres_filtro)

    job_id = str(uuid.uuid4())
    _jobs[job_id] = {
        "estado":            "en_progreso",
        "generacion_actual": 0,
        "fitness_actual":    0.0,
        "conflictos_actual": 0,
        "num_generaciones":  params.num_generaciones,
        "resultado":         None,
        "error":             None,
    }
    _limpiar_jobs_viejos()

    hilo = threading.Thread(
        target=_ejecutar_ag,
        args=(job_id, ag, params.model_dump(), params.semilla),
        daemon=True,
    )
    hilo.start()

    return {"job_id": job_id, "estado": "en_progreso"}


@app.get("/api/optimizar/estado/{job_id}", summary="Consultar estado de un trabajo de optimización")
async def estado_optimizacion(job_id: str) -> dict[str, Any]:
    """
    Devuelve el estado actual del trabajo:
    - ``en_progreso``: incluye ``generacion_actual``, ``fitness_actual``, ``conflictos_actual``
    - ``completado``:  incluye ``resultado`` completo (mismo formato que antes)
    - ``error``:       incluye ``error`` con la descripción del fallo
    """
    job = _jobs.get(job_id)
    if job is None:
        raise HTTPException(404, detail=f"Trabajo '{job_id}' no encontrado o expirado.")

    resp: dict[str, Any] = {
        "estado":            job["estado"],
        "generacion_actual": job.get("generacion_actual", 0),
        "fitness_actual":    job.get("fitness_actual", 0.0),
        "conflictos_actual": job.get("conflictos_actual", 0),
        "num_generaciones":  job.get("num_generaciones", 0),
    }
    if job["estado"] == "completado":
        resp["resultado"] = job["resultado"]
    elif job["estado"] == "error":
        resp["error"] = job["error"]
    return resp


# ===========================================================================
# RUTAS — Último horario y estadísticas
# ===========================================================================

@app.get("/api/ultimo-horario", summary="Último horario optimizado guardado")
async def get_ultimo_horario() -> dict[str, Any]:
    """
    Retorna el horario resultado de la última ejecución del algoritmo genético,
    persistido en disco. No requiere re-optimizar.
    """
    data = store.get_ultimo_horario()
    if data is None:
        raise HTTPException(
            status_code=404,
            detail="No hay horario guardado. Ejecuta la optimización primero.",
        )
    return data


@app.get("/api/estadisticas", summary="Estadísticas del último horario optimizado")
async def get_estadisticas() -> dict[str, Any]:
    """
    Calcula y retorna métricas sobre el último horario guardado:
      - Carga semanal por profesor (horas lectivas totales)
      - Número de sesiones por día de la semana
      - Horas asignadas por salón
      - Sesiones por semestre
      - Resumen de uso de laboratorios
    """
    data = store.get_ultimo_horario()
    if data is None:
        raise HTTPException(
            status_code=404,
            detail="No hay horario guardado. Ejecuta la optimización primero.",
        )

    horario: list[dict] = data.get("horario", [])

    carga_profesor:    dict[str, int] = {}
    clases_por_dia:    dict[str, int] = {}
    horas_por_salon:   dict[str, int] = {}
    clases_por_semestre: dict[int, int] = {}
    uso_laboratorio:   dict[str, int] = {}   # salon_tipo → horas asignadas

    for asig in horario:
        horas = asig.get("duracion_horas", 0)

        prof  = asig.get("profesor", "Desconocido")
        carga_profesor[prof] = carga_profesor.get(prof, 0) + horas

        dia   = asig.get("dia", "?")
        clases_por_dia[dia] = clases_por_dia.get(dia, 0) + 1

        salon = asig.get("salon", "?")
        horas_por_salon[salon] = horas_por_salon.get(salon, 0) + horas

        sem   = asig.get("semestre", 0)
        clases_por_semestre[sem] = clases_por_semestre.get(sem, 0) + 1

        tipo  = asig.get("salon_tipo", "aula")
        uso_laboratorio[tipo] = uso_laboratorio.get(tipo, 0) + horas

    return {
        "fecha_optimizacion":     data.get("fecha"),
        "fitness":                data.get("fitness", 0),
        "conflictos":             data.get("conflictos", 0),
        "total_sesiones":         len(horario),
        "carga_semanal_profesor": dict(sorted(carga_profesor.items())),
        "clases_por_dia":         dict(sorted(clases_por_dia.items())),
        "horas_por_salon":        dict(sorted(horas_por_salon.items())),
        "clases_por_semestre":    dict(sorted(clases_por_semestre.items())),
        "horas_por_tipo_salon":   uso_laboratorio,
    }


# ===========================================================================
# Serializadores internos
# ===========================================================================

def _m(m) -> dict:
    return {
        "id": m.id, "nombre": m.nombre, "semestre": m.semestre,
        "grupo": m.grupo, "creditos": m.creditos, "bloques": m.bloques,
        "horas_semanales": m.horas_semanales, "sesiones_por_semana": m.sesiones_por_semana,
        "num_alumnos": m.num_alumnos, "requiere_laboratorio": m.requiere_laboratorio,
    }

def _p(p) -> dict:
    return {
        "id": p.id, "nombre": p.nombre, "materias_ids": p.materias_ids,
        "franjas_preferidas": p.franjas_preferidas,
        "franjas_bloqueadas": p.franjas_bloqueadas,
    }

def _s(s) -> dict:
    return {"id": s.id, "nombre": s.nombre, "capacidad": s.capacidad, "tipo": s.tipo}

def _f(f) -> dict:
    return {"id": f.id, "dia": f.dia, "hora_inicio": f.hora_inicio, "hora_fin": f.hora_fin}

def _ses(s) -> dict:
    return {"id": s.id, "materia": s.nombre_materia, "profesor": s.nombre_profesor,
            "grupo": s.grupo, "semestre": s.semestre, "duracion_horas": s.duracion_horas}
