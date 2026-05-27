"""
Datos de muestra — Universidad de la Amazonia
Facultad de Ingeniería — Ingeniería de Sistemas
Plan de Estudios 2020 — Semestre 1 (Grupos A y B)

ESQUEMA REAL DE FRANJAS HORARIAS
=================================
Jornada mañana : 06:00 – 12:00
Jornada tarde  : 14:00 – 18:00

Bloques disponibles (franjas de inicio escalonado):
  Mañana — 5 bloques:  06:00 | 07:00 | 08:00 | 09:00 | 10:00
  Tarde  — 3 bloques:  14:00 | 15:00 | 16:00

Sesiones de 2h — todos los bloques son válidos:
  Mañana: 06-08, 07-09, 08-10, 09-11, 10-12  (fin ≤ 12:00) ✓
  Tarde : 14-16, 15-17, 16-18                 (fin ≤ 18:00) ✓

Sesiones de 3h — algunos bloques quedan restringidos:
  Mañana: 06-09, 07-10, 08-11, 09-12  ✓ | 10-13 ✗ (cruza almuerzo 12-14)
  Tarde : 14-17, 15-18                 ✓ | 16-19 ✗ (excede fin de jornada 18:00)

El inicializador greedy detecta estas restricciones automáticamente usando
  hora_inicio + duracion_horas * 60
y rechaza asignaciones inválidas antes de que el AG las evalúe.

Holgura: 8 franjas × 5 días × 6 salones = 240 ranuras / 28 sesiones ≈ 8.6×
"""

from models import Materia, Profesor, Salon, FranjaHoraria, SesionClase

# ---------------------------------------------------------------------------
# Materias — Semestre 1, Grupos A y B
#
# bloques=[3, 2] → 5h/sem: una sesión de 3h + una sesión de 2h
# bloques=[2, 2] → 4h/sem: dos sesiones de 2h
# ---------------------------------------------------------------------------
MATERIAS: list[Materia] = [
    # ── Semestre 1 · Grupo A ──────────────────────────────────────────────
    Materia( 1, "Matemáticas I",                1, "A", 3, [3, 2]),   # 5h/sem
    Materia( 2, "Física I",                     1, "A", 3, [2, 2]),   # 4h/sem
    Materia( 3, "Biología General",             1, "A", 3, [2, 2]),   # 4h/sem
    Materia( 4, "Introducción a la Ingeniería", 1, "A", 2, [2, 2]),   # 4h/sem
    Materia( 5, "Lógica y Algoritmos I",        1, "A", 3, [3, 2]),   # 5h/sem
    Materia( 6, "Comunicación",                 1, "A", 2, [2, 2]),   # 4h/sem
    Materia( 7, "Deporte y Cultura",            1, "A", 2, [2, 2]),   # 4h/sem

    # ── Semestre 1 · Grupo B ──────────────────────────────────────────────
    Materia( 8, "Matemáticas I",                1, "B", 3, [3, 2]),   # 5h/sem
    Materia( 9, "Física I",                     1, "B", 3, [2, 2]),   # 4h/sem
    Materia(10, "Biología General",             1, "B", 3, [2, 2]),   # 4h/sem
    Materia(11, "Introducción a la Ingeniería", 1, "B", 2, [2, 2]),   # 4h/sem
    Materia(12, "Lógica y Algoritmos I",        1, "B", 3, [3, 2]),   # 5h/sem
    Materia(13, "Comunicación",                 1, "B", 2, [2, 2]),   # 4h/sem
    Materia(14, "Deporte y Cultura",            1, "B", 2, [2, 2]),   # 4h/sem
]
# Sesiones totales:
#   Grupo A: Matemáticas(3h+2h) + Lógica(3h+2h) + 5 materias×2 = 14 sesiones
#   Grupo B: igual = 14 sesiones
#   Total: 28 sesiones  (4 de 3h + 24 de 2h)

# ---------------------------------------------------------------------------
# Franjas horarias: Lunes–Viernes (8 bloques por día)
#
# IDs por día:
#   Posición  1 → 06:00-08:00 (mañana bloque 1)
#   Posición  2 → 07:00-09:00 (mañana bloque 2)
#   Posición  3 → 08:00-10:00 (mañana bloque 3)
#   Posición  4 → 09:00-11:00 (mañana bloque 4)
#   Posición  5 → 10:00-12:00 (mañana bloque 5)
#   Posición  6 → 14:00-16:00 (tarde bloque 1)
#   Posición  7 → 15:00-17:00 (tarde bloque 2)
#   Posición  8 → 16:00-18:00 (tarde bloque 3)
#
#   Lunes     :  1– 8   Martes     :  9–16
#   Miércoles : 17–24   Jueves     : 25–32
#   Viernes   : 33–40
#
# Total: 40 franjas  (8 × 5 días)
# ---------------------------------------------------------------------------
DIAS   = ["Lunes", "Martes", "Miércoles", "Jueves", "Viernes"]
BLOQUES = [
    ("06:00", "08:00"),   # mañana bloque 1
    ("07:00", "09:00"),   # mañana bloque 2
    ("08:00", "10:00"),   # mañana bloque 3
    ("09:00", "11:00"),   # mañana bloque 4
    ("10:00", "12:00"),   # mañana bloque 5
    ("14:00", "16:00"),   # tarde  bloque 1
    ("15:00", "17:00"),   # tarde  bloque 2
    ("16:00", "18:00"),   # tarde  bloque 3
]

FRANJAS: list[FranjaHoraria] = []
_fid = 1
for _dia in DIAS:
    for _inicio, _fin in BLOQUES:
        FRANJAS.append(FranjaHoraria(_fid, _dia, _inicio, _fin))
        _fid += 1
# Total: 40 franjas  (8 × 5 días)

# ---------------------------------------------------------------------------
# Salones
# 6 salones × 40 franjas = 240 ranuras para 28 sesiones → 8.6× holgura
# ---------------------------------------------------------------------------
SALONES: list[Salon] = [
    Salon(1, "Aula 101",          35),
    Salon(2, "Aula 102",          35),
    Salon(3, "Aula 103",          35),
    Salon(4, "Aula 201",          40),
    Salon(5, "Aula 202",          40),
    Salon(6, "Lab. Sistemas",     30, tipo="laboratorio"),
]

# ---------------------------------------------------------------------------
# Profesores (7 profesores — máx. 4 sesiones cada uno)
# Franjas preferidas (IDs correspondientes al esquema de 8/día):
#   Mañanas: 1-5 | 9-13 | 17-21 | 25-29 | 33-37
#   Tardes : 6-8 | 14-16 | 22-24 | 30-32 | 38-40
# ---------------------------------------------------------------------------
_MANANAS = [1, 2, 3, 4, 5,  9, 10, 11, 12, 13,  17, 18, 19, 20, 21,
            25, 26, 27, 28, 29,  33, 34, 35, 36, 37]
_TARDES  = [6, 7, 8,  14, 15, 16,  22, 23, 24,  30, 31, 32,  38, 39, 40]
_AMBAS   = _MANANAS + _TARDES

PROFESORES: list[Profesor] = [
    # Imparte Matemáticas I a Grupo A y B → prefiere mañanas
    Profesor(1, "Dr. Matemáticas",
             [1, 8],          # Matemáticas I: A=1, B=8
             _MANANAS),

    # Imparte Física I a Grupo A y B → prefiere mañanas
    Profesor(2, "Dra. Física",
             [2, 9],          # Física I: A=2, B=9
             _MANANAS),

    # Imparte Biología General a Grupo A y B → prefiere mañanas
    Profesor(3, "Dra. Biología",
             [3, 10],         # Biología General: A=3, B=10
             _MANANAS),

    # Imparte Lógica y Algoritmos I a A y B → prefiere mañanas y tardes
    Profesor(4, "Prof. Programación",
             [5, 12],         # Lógica A=5, Lógica B=12
             _AMBAS),

    # Imparte Introducción a la Ingeniería a A y B → prefiere tardes
    Profesor(5, "Prof. Ing. Software",
             [4, 11],         # Intro A=4, Intro B=11
             _TARDES),

    # Imparte Comunicación a Grupo A y B → prefiere tardes
    Profesor(6, "Prof. Comunicación",
             [6, 13],         # Comunicación: A=6, B=13
             _TARDES),

    # Imparte Deporte y Cultura a Grupo A y B → prefiere tardes
    Profesor(7, "Prof. Deportes",
             [7, 14],         # Deporte y Cultura: A=7, B=14
             _TARDES),
]

_PROF_BY_MATERIA = {mid: p for p in PROFESORES for mid in p.materias_ids}


# ---------------------------------------------------------------------------
# Generación automática de sesiones
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
