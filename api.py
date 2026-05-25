"""
API REST + WebSocket — Optimizador de Horarios con Algoritmo Genético
Universidad de la Amazonia · Facultad de Ingeniería · Ingeniería de Sistemas
"""

import asyncio
import json
from typing import Any

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

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
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------------------------------------------------------------------------
# Schemas Pydantic
# ---------------------------------------------------------------------------

class MateriaIn(BaseModel):
    nombre:   str      = Field(..., min_length=2, max_length=100)
    semestre: int      = Field(..., ge=1, le=10)
    grupo:    str      = Field(..., min_length=1, max_length=10)
    creditos: int      = Field(..., ge=1, le=10)
    bloques:  list[int] = Field(..., min_length=1)

class MateriaUpdate(BaseModel):
    nombre:   str        | None = Field(None, min_length=2, max_length=100)
    semestre: int        | None = Field(None, ge=1, le=10)
    grupo:    str        | None = Field(None, min_length=1, max_length=10)
    creditos: int        | None = Field(None, ge=1, le=10)
    bloques:  list[int]  | None = None

class ProfesorIn(BaseModel):
    nombre:             str        = Field(..., min_length=2, max_length=100)
    materias_ids:       list[int]  = Field(default_factory=list)
    franjas_preferidas: list[int]  = Field(default_factory=list)

class ProfesorUpdate(BaseModel):
    nombre:             str        | None = Field(None, min_length=2, max_length=100)
    materias_ids:       list[int]  | None = None
    franjas_preferidas: list[int]  | None = None

class SalonIn(BaseModel):
    nombre:    str = Field(..., min_length=2, max_length=100)
    capacidad: int = Field(..., ge=1, le=1000)

class SalonUpdate(BaseModel):
    nombre:    str | None = Field(None, min_length=2, max_length=100)
    capacidad: int | None = Field(None, ge=1, le=1000)

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
        "tecnologias": {"backend": "Python + FastAPI", "frontend": "Flutter"},
    }


# ===========================================================================
# RUTAS — CRUD Materias
# ===========================================================================

@app.get("/api/materias", summary="Listar materias")
async def listar_materias():
    return [_m(m) for m in store.materias]


@app.post("/api/materias", summary="Crear materia", status_code=201)
async def crear_materia(body: MateriaIn):
    mat = store.add_materia(body.nombre, body.semestre, body.grupo, body.creditos, body.bloques)
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
    prof = store.add_profesor(body.nombre, body.materias_ids, body.franjas_preferidas)
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
    salon = store.add_salon(body.nombre, body.capacidad)
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
# RUTAS — Optimización
# ===========================================================================

@app.post("/api/optimizar", summary="Ejecutar algoritmo genético (síncrono)")
async def optimizar(params: ParamsRequest) -> dict[str, Any]:
    """
    Corre el AG completo con los datos actuales del sistema.
    Para actualizaciones generación por generación usa el WebSocket /ws/optimizar.
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

    historial: list[dict] = []
    resultado_final: dict = {}

    for gen_result in ag.evolucionar(semilla=params.semilla):
        historial.append({
            "generacion":       gen_result.numero,
            "mejor_fitness":    gen_result.mejor_fitness,
            "promedio_fitness": gen_result.promedio_fitness,
            "peor_fitness":     gen_result.peor_fitness,
            "conflictos":       gen_result.conflictos_mejor,
        })
        resultado_final = gen_result.to_dict()

    return {
        "parametros":              params.model_dump(),
        "generaciones_ejecutadas": len(historial),
        "historial":               historial,
        "mejor_horario":           resultado_final.get("mejor_horario", []),
        "mejor_fitness":           resultado_final.get("mejor_fitness", 0),
        "conflictos_finales":      resultado_final.get("conflictos_mejor", 0),
    }


@app.websocket("/ws/optimizar")
async def ws_optimizar(websocket: WebSocket):
    """
    WebSocket para visualización en tiempo real.

    El cliente envía un JSON con los parámetros (igual que ParamsRequest).
    Mensajes del servidor:
      { "tipo": "generacion", "datos": { ...ResultadoGeneracion... } }
      { "tipo": "finalizado",  "datos": { ...último resultado...  } }
      { "tipo": "error",       "mensaje": "..."                    }
    """
    await websocket.accept()

    try:
        raw    = await websocket.receive_text()
        params = ParamsRequest(**json.loads(raw))
    except Exception as exc:
        await websocket.send_json({"tipo": "error", "mensaje": f"Parámetros inválidos: {exc}"})
        await websocket.close()
        return

    try:
        _require_ready(semestres_filtro=params.semestres_filtro)
    except HTTPException as exc:
        await websocket.send_json({"tipo": "error", "mensaje": exc.detail})
        await websocket.close()
        return

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

    try:
        resultado_final: dict = {}
        for gen_result in ag.evolucionar(semilla=params.semilla):
            await websocket.send_json({"tipo": "generacion", "datos": gen_result.to_dict()})
            await asyncio.sleep(0)
            resultado_final = gen_result.to_dict()

        await websocket.send_json({"tipo": "finalizado", "datos": resultado_final})

    except WebSocketDisconnect:
        pass
    except Exception as exc:
        await websocket.send_json({"tipo": "error", "mensaje": str(exc)})
    finally:
        try:
            await websocket.close()
        except Exception:
            pass


# ===========================================================================
# Serializadores internos
# ===========================================================================

def _m(m) -> dict:
    return {"id": m.id, "nombre": m.nombre, "semestre": m.semestre,
            "grupo": m.grupo, "creditos": m.creditos, "bloques": m.bloques,
            "horas_semanales": m.horas_semanales,
            "sesiones_por_semana": m.sesiones_por_semana}

def _p(p) -> dict:
    return {"id": p.id, "nombre": p.nombre, "materias_ids": p.materias_ids,
            "franjas_preferidas": p.franjas_preferidas}

def _s(s) -> dict:
    return {"id": s.id, "nombre": s.nombre, "capacidad": s.capacidad}

def _f(f) -> dict:
    return {"id": f.id, "dia": f.dia, "hora_inicio": f.hora_inicio, "hora_fin": f.hora_fin}

def _ses(s) -> dict:
    return {"id": s.id, "materia": s.nombre_materia, "profesor": s.nombre_profesor,
            "grupo": s.grupo, "semestre": s.semestre, "duracion_horas": s.duracion_horas}
