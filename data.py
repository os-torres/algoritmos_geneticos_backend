"""
Datos de muestra — Universidad de la Amazonia
Facultad de Ingeniería — Ingeniería de Sistemas
Plan de Estudios 2020 — Semestre 1 (Grupos A y B)
Materia: Inteligencia Artificial
Docente: Dr. Jesús Emilio Pinto
"""

from models import Materia, Profesor, Salon, FranjaHoraria, SesionClase

# ---------------------------------------------------------------------------
# Materias — campo "bloques" define las horas de cada sesión semanal
# Ejemplo: bloques=[2,1] → 2 sesiones/semana: una de 2h y otra de 1h (3h total)
# El GA garantiza que ninguna sesión de la misma materia coincida el mismo día.
#
# Semestre 1 — Plan 2020, Ing. Sistemas — Universidad de la Amazonia
# Se cargan los dos grupos (A y B) del primer semestre para demostrar
# la optimización cruzada de grupos en paralelo.
# Se recomienda cargar un semestre a la vez para que el AG sea eficiente.
# ---------------------------------------------------------------------------
MATERIAS: list[Materia] = [
    # ── Semestre 1 · Grupo A ──────────────────────────────────────────────
    # bloques=[3,2] → 5h/sem: una sesión de 3h y otra de 2h
    # bloques=[2,2] → 4h/sem: dos sesiones de 2h cada una
    Materia( 1, "Matemáticas I",               1, "A", 3, [3, 2]),   # 5h = 1×3h + 1×2h
    Materia( 2, "Física I",                    1, "A", 3, [2, 2]),   # 4h = 2×2h
    Materia( 3, "Biología General",            1, "A", 3, [2, 2]),   # 4h = 2×2h
    Materia( 4, "Introducción a la Ingeniería",1, "A", 2, [2, 2]),   # 4h = 2×2h
    Materia( 5, "Lógica y Algoritmos I",       1, "A", 3, [3, 2]),   # 5h = 1×3h + 1×2h
    Materia( 6, "Comunicación",                1, "A", 2, [2, 2]),   # 4h = 2×2h
    Materia( 7, "Deporte y Cultura",           1, "A", 2, [2, 2]),   # 4h = 2×2h

    # ── Semestre 1 · Grupo B ──────────────────────────────────────────────
    Materia( 8, "Matemáticas I",               1, "B", 3, [3, 2]),   # 5h = 1×3h + 1×2h
    Materia( 9, "Física I",                    1, "B", 3, [2, 2]),   # 4h = 2×2h
    Materia(10, "Biología General",            1, "B", 3, [2, 2]),   # 4h = 2×2h
    Materia(11, "Introducción a la Ingeniería",1, "B", 2, [2, 2]),   # 4h = 2×2h
    Materia(12, "Lógica y Algoritmos I",       1, "B", 3, [3, 2]),   # 5h = 1×3h + 1×2h
    Materia(13, "Comunicación",                1, "B", 2, [2, 2]),   # 4h = 2×2h
    Materia(14, "Deporte y Cultura",           1, "B", 2, [2, 2]),   # 4h = 2×2h
]
# Total sesiones:
#   Grupo A: 2+2+2+2+2+2+2 = 14
#   Grupo B: 2+2+2+2+2+2+2 = 14
#   Grand total: 28 sesiones  (40 franjas × 4 salones = 160 ranuras → cómodo)

# ---------------------------------------------------------------------------
# Franjas horarias: Lunes–Viernes
#
# Jornada institucional: 06:00–12:00 (mañana)  |  14:00–18:00 (tarde)
# Almuerzo 12:00–14:00: sin clases.
#
# Se definen 8 bloques de inicio por día para que sesiones de 2h y 3h
# siempre queden dentro de su jornada:
#
#   Mañana (5 slots inicio):
#     06:00 → válido para 2h (fin 08:00) y 3h (fin 09:00) ✓
#     07:00 → válido para 2h (fin 09:00) y 3h (fin 10:00) ✓
#     08:00 → válido para 2h (fin 10:00) y 3h (fin 11:00) ✓
#     09:00 → válido para 2h (fin 11:00) y 3h (fin 12:00) ✓
#     10:00 → válido SOLO para 2h (fin 12:00); 3h terminaría a las 13:00 ✗
#
#   Tarde (3 slots inicio):
#     14:00 → válido para 2h (fin 16:00) y 3h (fin 17:00) ✓
#     15:00 → válido para 2h (fin 17:00) y 3h (fin 18:00) ✓
#     16:00 → válido SOLO para 2h (fin 18:00); 3h terminaría a las 19:00 ✗
#
# Total: 8 bloques × 5 días = 40 franjas
# ---------------------------------------------------------------------------
DIAS = ["Lunes", "Martes", "Miércoles", "Jueves", "Viernes"]
BLOQUES = [
    ("06:00", "08:00"),   # mañana slot 1
    ("07:00", "09:00"),   # mañana slot 2
    ("08:00", "10:00"),   # mañana slot 3
    ("09:00", "11:00"),   # mañana slot 4
    ("10:00", "12:00"),   # mañana slot 5 — solo válido para sesiones de ≤2h
    ("14:00", "16:00"),   # tarde slot 1
    ("15:00", "17:00"),   # tarde slot 2
    ("16:00", "18:00"),   # tarde slot 3 — solo válido para sesiones de ≤2h
]

FRANJAS: list[FranjaHoraria] = []
_fid = 1
for _dia in DIAS:
    for _inicio, _fin in BLOQUES:
        FRANJAS.append(FranjaHoraria(_fid, _dia, _inicio, _fin))
        _fid += 1
# Total: 40 franjas  (8 bloques × 5 días)

# ---------------------------------------------------------------------------
# Salones
# ---------------------------------------------------------------------------
SALONES: list[Salon] = [
    Salon(1, "Aula 101",                30),
    Salon(2, "Aula 102",                30),
    Salon(3, "Laboratorio de Sistemas", 25),
    Salon(4, "Aula 201",                40),
]

# ---------------------------------------------------------------------------
# Profesores — Planta docente propuesta
# franjas_preferidas: IDs de las franjas que el docente prefiere
# Franja IDs por día:  L=1–5  |  M=6–10  |  X=11–15  |  J=16–20  |  V=21–25
# Bloques del día:     mañana=1,2,3  tarde=4,5  (por cada día)
# ---------------------------------------------------------------------------
# Con 8 bloques por día, los IDs se distribuyen así:
#   Bloque 0=06:00, 1=07:00, 2=08:00, 3=09:00, 4=10:00 → mañana
#   Bloque 5=14:00, 6=15:00, 7=16:00              → tarde
#   Día d (0=Lunes … 4=Viernes): IDs = d*8+1 … d*8+8
#
# IDs mañana (bloques 0-4 de cada día):
#   Lunes   : 1,2,3,4,5
#   Martes  : 9,10,11,12,13
#   Miércoles: 17,18,19,20,21
#   Jueves  : 25,26,27,28,29
#   Viernes : 33,34,35,36,37
# IDs tarde (bloques 5-7 de cada día):
#   Lunes   : 6,7,8
#   Martes  : 14,15,16
#   Miércoles: 22,23,24
#   Jueves  : 30,31,32
#   Viernes : 38,39,40
_MANANAS = [1,2,3,4,5, 9,10,11,12,13, 17,18,19,20,21, 25,26,27,28,29, 33,34,35,36,37]
_TARDES  = [6,7,8, 14,15,16, 22,23,24, 30,31,32, 38,39,40]

PROFESORES: list[Profesor] = [
    # ID  Nombre                 Materias sem-1 A&B         Franjas preferidas
    Profesor(1, "Dr. Matemáticas",
             [1, 8],                # Matemáticas I (A y B)
             _MANANAS),             # prefiere mañanas

    Profesor(2, "Dra. Ciencias Básicas",
             [2, 3, 9, 10],         # Física I + Biología General (A y B)
             _MANANAS),             # prefiere mañanas

    Profesor(3, "Prof. Programación",
             [5, 12],               # Lógica y Algoritmos I (A y B)
             [3,8,13,18,23]         # última franja de mañana cada día
             + _TARDES),

    Profesor(4, "Prof. Ing. Software",
             [4, 11],               # Introducción a la Ingeniería (A y B)
             [2,7,12,17,22]         # segunda franja de mañana
             + [4,5,9,10]),

    Profesor(5, "Prof. BD y Redes",
             [],                    # sin materias en sem 1 (carga sem 3-5)
             _TARDES),

    Profesor(6, "Prof. Web y Móvil",
             [],                    # sin materias en sem 1 (carga sem 5-7)
             _TARDES),

    Profesor(7, "Prof. Investigación",
             [],                    # sin materias en sem 1 (carga sem 7-10)
             _MANANAS),

    Profesor(8, "Prof. Humanidades",
             [6, 7, 13, 14],        # Comunicación + Deporte y Cultura (A y B)
             _TARDES),              # prefiere tardes
]

_PROF_BY_MATERIA = {mid: p for p in PROFESORES for mid in p.materias_ids}


# ---------------------------------------------------------------------------
# Generación automática de sesiones
# Cada elemento de materia.bloques → una SesionClase con su duracion_horas
# ---------------------------------------------------------------------------
def generar_sesiones() -> list[SesionClase]:
    sesiones: list[SesionClase] = []
    sid = 1
    for materia in MATERIAS:
        profesor = _PROF_BY_MATERIA.get(materia.id, PROFESORES[0])
        for duracion in materia.bloques:
            sesiones.append(SesionClase(
                id=sid,
                materia_id=materia.id,
                profesor_id=profesor.id,
                grupo=materia.grupo,
                semestre=materia.semestre,
                duracion_horas=duracion,
                nombre_materia=materia.nombre,
                nombre_profesor=profesor.nombre,
            ))
            sid += 1
    return sesiones


SESIONES: list[SesionClase] = generar_sesiones()

# ---------------------------------------------------------------------------
# Utilidades
# ---------------------------------------------------------------------------
PROFESORES_DICT: dict[int, Profesor]      = {p.id: p for p in PROFESORES}
MATERIAS_DICT:   dict[int, Materia]       = {m.id: m for m in MATERIAS}
SALONES_DICT:    dict[int, Salon]         = {s.id: s for s in SALONES}
FRANJAS_DICT:    dict[int, FranjaHoraria] = {f.id: f for f in FRANJAS}


def datos_completos() -> dict:
    return {
        "materias": [_mat_dict(m) for m in MATERIAS],
        "profesores": [
            {"id": p.id, "nombre": p.nombre,
             "materias_ids": p.materias_ids,
             "franjas_preferidas": p.franjas_preferidas}
            for p in PROFESORES
        ],
        "salones": [
            {"id": s.id, "nombre": s.nombre, "capacidad": s.capacidad}
            for s in SALONES
        ],
        "franjas": [
            {"id": f.id, "dia": f.dia,
             "hora_inicio": f.hora_inicio, "hora_fin": f.hora_fin}
            for f in FRANJAS
        ],
        "sesiones": [
            {"id": s.id, "materia": s.nombre_materia,
             "profesor": s.nombre_profesor, "grupo": s.grupo,
             "semestre": s.semestre, "duracion_horas": s.duracion_horas}
            for s in SESIONES
        ],
        "resumen": {
            "total_sesiones": len(SESIONES),
            "total_franjas":  len(FRANJAS),
            "total_salones":  len(SALONES),
            "total_ranuras":  len(FRANJAS) * len(SALONES),
        },
    }


def _mat_dict(m: Materia) -> dict:
    return {
        "id": m.id, "nombre": m.nombre, "semestre": m.semestre,
        "grupo": m.grupo, "creditos": m.creditos, "bloques": m.bloques,
        "horas_semanales": m.horas_semanales,
        "sesiones_por_semana": m.sesiones_por_semana,
    }
