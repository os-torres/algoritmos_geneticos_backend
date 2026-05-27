"""
Algoritmo Genético para optimización de horarios universitarios.

Representación por PERMUTACIÓN:
  - Hay N_RANURAS = len(franjas) × len(salones) posibles asignaciones.
  - Un cromosoma es una lista de len(sesiones) enteros únicos tomados de
    [0, N_RANURAS), garantizando que ninguna ranura (franja, salón) quede
    asignada a dos sesiones simultáneamente.
  - ranura = franja_idx * num_salones + salon_idx

Operadores genéticos:
  - Selección:    Torneo determinístico
  - Cruzamiento:  Order Crossover (OX) — preserva la propiedad de permutación
  - Mutación:     Tres operadores con selección aleatoria uniforme:
                    · Swap      — intercambia dos posiciones
                    · Inversión — voltea un segmento
                    · Inserción — extrae un gen y lo reinserta en otra posición
  - Elitismo:     Los N mejores individuos pasan intactos a la siguiente generación

Optimizaciones:
  - Inicialización 30 % heurística (greedy) + 70 % aleatoria para acelerar convergencia
  - Reinicio parcial cuando hay estancamiento (conserva élites + regenera el resto)
  - Evaluación de fitness paralelizada con ProcessPoolExecutor (fallback a secuencial)
"""

import os
import random
from concurrent.futures import ProcessPoolExecutor
from copy import deepcopy
from dataclasses import dataclass, field
from typing import Generator, List, Optional, Tuple

from models import (
    Asignacion, FranjaHoraria, ParametrosAG, Profesor,
    ResultadoGeneracion, Salon, SesionClase,
)

Cromosoma = List[int]

# Carga horaria máxima permitida por grupo en un mismo día (horas lectivas).
# Máximo físico posible:
#   Mañana (06:00-12:00) = 6 h  +  Tarde (14:00-18:00) = 4 h  → 10 h absoluto.
# Se fija en 9 h para permitir jornadas completas (p. ej. 06-12 + 14-17)
# sin penalizar horarios razonables, pero evitando concentrar todo un día en un solo grupo.
MAX_HORAS_DIARIAS_GRUPO: int = 9

# Número máximo de reinicios parciales de población permitidos por ejecución
MAX_REINICIOS: int = 3

# ── Operadores de mutación ────────────────────────────────────────────────────
# Se definen a nivel de módulo para que ProcessPoolExecutor pueda serializarlos.

def _swap(cromosoma: Cromosoma) -> Cromosoma:
    """Intercambia dos posiciones aleatorias."""
    resultado = cromosoma[:]
    i, j = random.sample(range(len(resultado)), 2)
    resultado[i], resultado[j] = resultado[j], resultado[i]
    return resultado


def _inversion(cromosoma: Cromosoma) -> Cromosoma:
    """Invierte un segmento aleatorio del cromosoma."""
    resultado = cromosoma[:]
    n = len(resultado)
    i = random.randint(0, n - 2)
    j = random.randint(i + 1, n - 1)
    resultado[i : j + 1] = resultado[i : j + 1][::-1]
    return resultado


def _insercion(cromosoma: Cromosoma) -> Cromosoma:
    """Extrae un gen y lo reinserta en una posición aleatoria distinta."""
    resultado = cromosoma[:]
    i = random.randint(0, len(resultado) - 1)
    gen = resultado.pop(i)
    j = random.randint(0, len(resultado) - 1)
    resultado.insert(j, gen)
    return resultado


_OPERADORES_MUTACION = [_swap, _inversion, _insercion]

# ── Worker para evaluación paralela ──────────────────────────────────────────

_worker_ag: Optional["AlgoritmoGenetico"] = None


def _worker_init(ag: "AlgoritmoGenetico") -> None:
    """Inicializa el estado del proceso worker con la instancia del AG."""
    global _worker_ag
    _worker_ag = ag


def _worker_fitness(cromosoma: Cromosoma) -> Tuple[float, int]:
    """Evalúa un cromosoma en el proceso worker."""
    return _worker_ag.calcular_fitness(cromosoma)  # type: ignore[union-attr]


# ── Datos de entrada del AG ───────────────────────────────────────────────────

@dataclass
class DatosAG:
    sesiones:        list[SesionClase]
    franjas:         list[FranjaHoraria]
    salones:         list[Salon]
    profesores_dict: dict[int, Profesor]
    bloques:         list[tuple[str, str]]   # [(hora_inicio, hora_fin), …] únicos y ordenados


# ── Clase principal ───────────────────────────────────────────────────────────

class AlgoritmoGenetico:

    def __init__(self, datos: DatosAG, params: ParametrosAG) -> None:
        self.datos        = datos
        self.params       = params
        self.num_franjas  = len(datos.franjas)
        self.num_salones  = len(datos.salones)
        self.num_ranuras  = self.num_franjas * self.num_salones
        self.num_sesiones = len(datos.sesiones)

    # ── Helpers internos ──────────────────────────────────────────────────────

    @staticmethod
    def _mins(t: str) -> int:
        """Convierte 'HH:MM' a minutos desde medianoche."""
        h, m = map(int, t.split(':'))
        return h * 60 + m

    # ── Decodificación ────────────────────────────────────────────────────────

    def decodificar(self, cromosoma: Cromosoma) -> list[Asignacion]:
        asignaciones: list[Asignacion] = []
        for i, ranura in enumerate(cromosoma):
            franja_idx = ranura // self.num_salones
            salon_idx  = ranura %  self.num_salones
            asignaciones.append(Asignacion(
                sesion=self.datos.sesiones[i],
                franja=self.datos.franjas[franja_idx],
                salon=self.datos.salones[salon_idx],
            ))
        return asignaciones

    # ── Función fitness ───────────────────────────────────────────────────────

    def calcular_fitness(self, cromosoma: Cromosoma) -> Tuple[float, int]:
        """
        Evalúa la calidad de un horario.

        Restricciones duras (penalización alta):
          -200  Profesor con dos sesiones solapadas en el mismo día
          -200  Mismo grupo con dos sesiones solapadas en el mismo día
          -300  Misma materia (mismo grupo) aparece más de una vez en el mismo día
          -300  Profesor asignado en una franja que tiene bloqueada
          -300  Materia que requiere laboratorio asignada a un aula ordinaria
          -150  Salón sin capacidad suficiente para el número de alumnos
          -400  Sesión que cruza el horario de almuerzo (12:00–14:00)
          -400  Sesión que termina después de las 18:00

        Restricciones blandas (penalización media):
          -100  Grupo con más de MAX_HORAS_DIARIAS_GRUPO horas en un día
           -30  Todo el grupo concentrado en un único día de la semana
           -30  Salón de laboratorio ocupado por materia que no lo necesita

        Bonificaciones:
          +15   Profesor dicta en una franja de su preferencia
          +20   Grupo con clases distribuidas en ≥ 3 días distintos
          +5    Grupo con clases distribuidas en 2 días distintos
          +15   Todas las sesiones de un profesor son solo de mañana o solo de tarde

        Todas las penalizaciones se escalan inversamente al tamaño del problema
        para evitar que el fitness colapse a 0 en instancias grandes.

        Retorna: (fitness: float, num_conflictos: int)
        """
        asignaciones = self.decodificar(cromosoma)
        penalizacion = 0.0
        bonificacion = 0.0
        conflictos   = 0
        n = len(asignaciones)

        # Factor de escala: normaliza las penalizaciones al tamaño del problema.
        # Con 14 sesiones (1 semestre) escala=1.0; con 200 sesiones escala≈14.3.
        escala = max(1.0, n / 14.0)

        # ── Solapamiento de sesiones (O(n²), detección por duración real) ────
        for i in range(n):
            for j in range(i + 1, n):
                a_i, a_j = asignaciones[i], asignaciones[j]
                if a_i.franja.dia != a_j.franja.dia:
                    continue
                s_i = self._mins(a_i.franja.hora_inicio)
                e_i = s_i + a_i.sesion.duracion_horas * 60
                s_j = self._mins(a_j.franja.hora_inicio)
                e_j = s_j + a_j.sesion.duracion_horas * 60
                if not (s_i < e_j and s_j < e_i):
                    continue
                if a_i.sesion.profesor_id == a_j.sesion.profesor_id:
                    penalizacion += 200.0 / escala
                    conflictos   += 1
                if a_i.sesion.grupo == a_j.sesion.grupo:
                    penalizacion += 200.0 / escala
                    conflictos   += 1

        # ── Misma materia (mismo grupo) en el mismo día ───────────────────────
        mat_dia: dict[tuple, int] = {}
        for asig in asignaciones:
            key = (asig.sesion.materia_id, asig.sesion.grupo, asig.franja.dia)
            mat_dia[key] = mat_dia.get(key, 0) + 1
        for count in mat_dia.values():
            if count > 1:
                penalizacion += (300.0 / escala) * (count - 1)
                conflictos   += count - 1

        # ── Franjas bloqueadas del profesor ───────────────────────────────────
        for asig in asignaciones:
            prof = self.datos.profesores_dict.get(asig.sesion.profesor_id)
            if prof and asig.franja.id in prof.franjas_bloqueadas:
                penalizacion += 300.0 / escala
                conflictos   += 1

        # ── Tipo de salón ─────────────────────────────────────────────────────
        for asig in asignaciones:
            if asig.sesion.requiere_laboratorio and asig.salon.tipo != "laboratorio":
                penalizacion += 300.0 / escala
                conflictos   += 1
            elif not asig.sesion.requiere_laboratorio and asig.salon.tipo == "laboratorio":
                # Desperdiciar un laboratorio es indeseable pero no crítico
                penalizacion += 30.0 / escala

        # ── Capacidad del salón ───────────────────────────────────────────────
        for asig in asignaciones:
            if asig.salon.capacidad < asig.sesion.num_alumnos:
                penalizacion += 150.0 / escala
                conflictos   += 1

        # ── Carga horaria diaria máxima por grupo ─────────────────────────────
        horas_grupo_dia: dict[tuple, int] = {}
        for asig in asignaciones:
            key = (asig.sesion.grupo, asig.sesion.semestre, asig.franja.dia)
            horas_grupo_dia[key] = horas_grupo_dia.get(key, 0) + asig.sesion.duracion_horas
        for horas in horas_grupo_dia.values():
            if horas > MAX_HORAS_DIARIAS_GRUPO:
                penalizacion += (100.0 / escala) * (horas - MAX_HORAS_DIARIAS_GRUPO)
                conflictos   += 1

        # ── Restricciones de jornada (hard) ───────────────────────────────────
        _MEDIODIA = 12 * 60
        _FIN_DIA  = 18 * 60
        for asig in asignaciones:
            inicio = self._mins(asig.franja.hora_inicio)
            fin    = inicio + asig.sesion.duracion_horas * 60
            if inicio < _MEDIODIA and fin > _MEDIODIA:
                penalizacion += 400.0 / escala
                conflictos   += 1
            if fin > _FIN_DIA:
                penalizacion += 400.0 / escala
                conflictos   += 1

        # ── Preferencias de franja del profesor (bonus) ───────────────────────
        for asig in asignaciones:
            prof = self.datos.profesores_dict.get(asig.sesion.profesor_id)
            if prof and asig.franja.id in prof.franjas_preferidas:
                bonificacion += 15

        # ── Distribución de clases por días (por grupo) ───────────────────────
        dias_por_grupo: dict[str, set] = {}
        for asig in asignaciones:
            dias_por_grupo.setdefault(asig.sesion.grupo, set()).add(asig.franja.dia)
        for dias in dias_por_grupo.values():
            if len(dias) >= 3:
                bonificacion += 20
            elif len(dias) == 2:
                bonificacion += 5
            else:
                penalizacion += 30.0 / escala

        # ── Penalizar último bloque del día ───────────────────────────────────
        for asig in asignaciones:
            try:
                idx = self.datos.bloques.index((asig.franja.hora_inicio, asig.franja.hora_fin))
                if idx == len(self.datos.bloques) - 1:
                    penalizacion += 5.0 / escala
            except ValueError:
                pass

        # ── Cohesión horaria del profesor: todo mañana o todo tarde (bonus) ───
        horas_prof: dict[int, list[str]] = {}
        for asig in asignaciones:
            horas_prof.setdefault(asig.sesion.profesor_id, []).append(asig.franja.hora_inicio)
        for horas in horas_prof.values():
            if all(h < "13:00" for h in horas) or all(h >= "14:00" for h in horas):
                bonificacion += 15

        fitness = max(0.0, 1000.0 - penalizacion + bonificacion)
        return fitness, conflictos

    # ── Detalle de conflictos ─────────────────────────────────────────────────

    def detectar_conflictos_detalle(self, cromosoma: Cromosoma) -> list[dict]:
        """
        Analiza el mejor horario y retorna la lista descriptiva de cada conflicto.

        Tipos detectados:
          "profesor"            — mismo profesor en dos sesiones solapadas
          "grupo"               — mismo grupo en dos sesiones solapadas
          "materia_mismo_dia"   — misma materia (grupo) aparece más de una vez en el día
          "franja_bloqueada"    — profesor asignado en franja que tiene bloqueada
          "capacidad_insuficiente" — salón sin capacidad para el número de alumnos
          "tipo_salon_incorrecto" — materia requiere lab y el salón asignado no lo es
          "carga_diaria_excedida" — grupo supera MAX_HORAS_DIARIAS_GRUPO en un día
          "horario_invalido"    — sesión cruza almuerzo o termina después de 18:00
        """
        asignaciones = self.decodificar(cromosoma)
        conflictos: list[dict] = []
        n = len(asignaciones)

        # ── Solapamientos ─────────────────────────────────────────────────────
        for i in range(n):
            for j in range(i + 1, n):
                a_i, a_j = asignaciones[i], asignaciones[j]
                if a_i.franja.dia != a_j.franja.dia:
                    continue
                s_i = self._mins(a_i.franja.hora_inicio)
                e_i = s_i + a_i.sesion.duracion_horas * 60
                s_j = self._mins(a_j.franja.hora_inicio)
                e_j = s_j + a_j.sesion.duracion_horas * 60
                if not (s_i < e_j and s_j < e_i):
                    continue

                sol_ini = max(s_i, s_j)
                sol_fin = min(e_i, e_j)
                rango   = (
                    f"{sol_ini // 60:02d}:{sol_ini % 60:02d}"
                    f"–{sol_fin // 60:02d}:{sol_fin % 60:02d}"
                )

                if a_i.sesion.profesor_id == a_j.sesion.profesor_id:
                    conflictos.append({
                        "tipo":              "profesor",
                        "dia":               a_i.franja.dia,
                        "hora_solapamiento": rango,
                        "profesor":          a_i.sesion.nombre_profesor,
                        "sesion_a": {
                            "materia":     a_i.sesion.nombre_materia,
                            "grupo":       a_i.sesion.grupo,
                            "semestre":    a_i.sesion.semestre,
                            "salon":       a_i.salon.nombre,
                            "hora_inicio": a_i.franja.hora_inicio,
                            "hora_fin":    f"{e_i // 60:02d}:{e_i % 60:02d}",
                        },
                        "sesion_b": {
                            "materia":     a_j.sesion.nombre_materia,
                            "grupo":       a_j.sesion.grupo,
                            "semestre":    a_j.sesion.semestre,
                            "salon":       a_j.salon.nombre,
                            "hora_inicio": a_j.franja.hora_inicio,
                            "hora_fin":    f"{e_j // 60:02d}:{e_j % 60:02d}",
                        },
                    })

                if (a_i.sesion.grupo == a_j.sesion.grupo
                        and a_i.sesion.semestre == a_j.sesion.semestre):
                    conflictos.append({
                        "tipo":              "grupo",
                        "dia":               a_i.franja.dia,
                        "hora_solapamiento": rango,
                        "grupo":             a_i.sesion.grupo,
                        "semestre":          a_i.sesion.semestre,
                        "sesion_a": {
                            "materia":     a_i.sesion.nombre_materia,
                            "profesor":    a_i.sesion.nombre_profesor,
                            "salon":       a_i.salon.nombre,
                            "hora_inicio": a_i.franja.hora_inicio,
                            "hora_fin":    f"{e_i // 60:02d}:{e_i % 60:02d}",
                        },
                        "sesion_b": {
                            "materia":     a_j.sesion.nombre_materia,
                            "profesor":    a_j.sesion.nombre_profesor,
                            "salon":       a_j.salon.nombre,
                            "hora_inicio": a_j.franja.hora_inicio,
                            "hora_fin":    f"{e_j // 60:02d}:{e_j % 60:02d}",
                        },
                    })

        # ── Misma materia mismo día ───────────────────────────────────────────
        mat_dia: dict[tuple, list] = {}
        for asig in asignaciones:
            key = (asig.sesion.materia_id, asig.sesion.grupo, asig.franja.dia)
            mat_dia.setdefault(key, []).append(asig)
        for (_, grupo, dia), asigs in mat_dia.items():
            if len(asigs) > 1:
                conflictos.append({
                    "tipo":     "materia_mismo_dia",
                    "dia":      dia,
                    "materia":  asigs[0].sesion.nombre_materia,
                    "grupo":    grupo,
                    "semestre": asigs[0].sesion.semestre,
                    "sesiones": [
                        {
                            "hora_inicio": a.franja.hora_inicio,
                            "hora_fin": (
                                lambda fin=self._mins(a.franja.hora_inicio) + a.sesion.duracion_horas * 60:
                                f"{fin // 60:02d}:{fin % 60:02d}"
                            )(),
                            "salon": a.salon.nombre,
                        }
                        for a in asigs
                    ],
                })

        # ── Franjas bloqueadas ────────────────────────────────────────────────
        for asig in asignaciones:
            prof = self.datos.profesores_dict.get(asig.sesion.profesor_id)
            if prof and asig.franja.id in prof.franjas_bloqueadas:
                conflictos.append({
                    "tipo":        "franja_bloqueada",
                    "dia":         asig.franja.dia,
                    "hora_inicio": asig.franja.hora_inicio,
                    "hora_fin":    asig.franja.hora_fin,
                    "profesor":    asig.sesion.nombre_profesor,
                    "materia":     asig.sesion.nombre_materia,
                    "grupo":       asig.sesion.grupo,
                    "semestre":    asig.sesion.semestre,
                    "salon":       asig.salon.nombre,
                })

        # ── Capacidad del salón ───────────────────────────────────────────────
        for asig in asignaciones:
            if asig.salon.capacidad < asig.sesion.num_alumnos:
                conflictos.append({
                    "tipo":            "capacidad_insuficiente",
                    "dia":             asig.franja.dia,
                    "hora_inicio":     asig.franja.hora_inicio,
                    "salon":           asig.salon.nombre,
                    "capacidad_salon": asig.salon.capacidad,
                    "num_alumnos":     asig.sesion.num_alumnos,
                    "deficit":         asig.sesion.num_alumnos - asig.salon.capacidad,
                    "materia":         asig.sesion.nombre_materia,
                    "grupo":           asig.sesion.grupo,
                    "semestre":        asig.sesion.semestre,
                })

        # ── Tipo de salón ─────────────────────────────────────────────────────
        for asig in asignaciones:
            if asig.sesion.requiere_laboratorio and asig.salon.tipo != "laboratorio":
                conflictos.append({
                    "tipo":       "tipo_salon_incorrecto",
                    "dia":        asig.franja.dia,
                    "hora_inicio":asig.franja.hora_inicio,
                    "materia":    asig.sesion.nombre_materia,
                    "grupo":      asig.sesion.grupo,
                    "semestre":   asig.sesion.semestre,
                    "salon":      asig.salon.nombre,
                    "salon_tipo": asig.salon.tipo,
                    "razon":      "la materia requiere laboratorio",
                })

        # ── Carga diaria excedida ─────────────────────────────────────────────
        horas_grupo_dia: dict[tuple, list] = {}
        for asig in asignaciones:
            key = (asig.sesion.grupo, asig.sesion.semestre, asig.franja.dia)
            horas_grupo_dia.setdefault(key, []).append(asig)
        for (grupo, semestre, dia), asigs in horas_grupo_dia.items():
            total = sum(a.sesion.duracion_horas for a in asigs)
            if total > MAX_HORAS_DIARIAS_GRUPO:
                conflictos.append({
                    "tipo":     "carga_diaria_excedida",
                    "dia":      dia,
                    "grupo":    grupo,
                    "semestre": semestre,
                    "horas":    total,
                    "maximo":   MAX_HORAS_DIARIAS_GRUPO,
                    "sesiones": [a.sesion.nombre_materia for a in asigs],
                })

        # ── Horario inválido (almuerzo / fin de jornada) ──────────────────────
        _MEDIODIA = 12 * 60
        _FIN_DIA  = 18 * 60
        for asig in asignaciones:
            inicio = self._mins(asig.franja.hora_inicio)
            fin    = inicio + asig.sesion.duracion_horas * 60
            h_fin  = f"{fin // 60:02d}:{fin % 60:02d}"
            if inicio < _MEDIODIA and fin > _MEDIODIA:
                conflictos.append({
                    "tipo":        "horario_invalido",
                    "dia":         asig.franja.dia,
                    "razon":       "cruza el horario de almuerzo (12:00–14:00)",
                    "materia":     asig.sesion.nombre_materia,
                    "grupo":       asig.sesion.grupo,
                    "semestre":    asig.sesion.semestre,
                    "hora_inicio": asig.franja.hora_inicio,
                    "hora_fin":    h_fin,
                    "salon":       asig.salon.nombre,
                })
            elif fin > _FIN_DIA:
                conflictos.append({
                    "tipo":        "horario_invalido",
                    "dia":         asig.franja.dia,
                    "razon":       f"termina a las {h_fin} (después de las 18:00)",
                    "materia":     asig.sesion.nombre_materia,
                    "grupo":       asig.sesion.grupo,
                    "semestre":    asig.sesion.semestre,
                    "hora_inicio": asig.franja.hora_inicio,
                    "hora_fin":    h_fin,
                    "salon":       asig.salon.nombre,
                })

        return conflictos

    # ── Inicialización de la población ────────────────────────────────────────

    def _cromosoma_aleatorio(self) -> Cromosoma:
        """Permutación aleatoria: elige num_sesiones ranuras únicas de [0, num_ranuras)."""
        return random.sample(range(self.num_ranuras), self.num_sesiones)

    def _cromosoma_greedy(self) -> Cromosoma:
        """
        Construye un cromosoma que ya respeta las restricciones más básicas:
          - No asigna dos sesiones al mismo profesor con solapamiento temporal real.
          - No asigna dos sesiones al mismo grupo con solapamiento temporal real.
          - Evita las franjas bloqueadas del profesor.
          - Evita franjas donde la sesión cruzaría el almuerzo (12:00-14:00)
            o excedería las 18:00, teniendo en cuenta la duración real de la sesión.
          - Prioriza las franjas preferidas del profesor.

        La detección de solapamiento usa intervalos [inicio, fin) reales basados
        en hora_inicio + duracion_horas, en lugar de comparar solo franja_idx.
        Esto previene que el AG parta de soluciones con penalizaciones masivas.

        Si no encuentra ranura válida con todas las restricciones, intenta con
        un fallback que solo respeta la jornada (almuerzo/18:00). En último
        recurso usa cualquier ranura libre.
        """
        _MEDIODIA = 12 * 60   # 720 min — inicio del almuerzo
        _FIN_DIA  = 18 * 60   # 1080 min — fin de la jornada

        sesiones_orden = list(range(self.num_sesiones))
        random.shuffle(sesiones_orden)

        cromosoma: list[int] = [-1] * self.num_sesiones
        ranuras_usadas: set[int] = set()
        # (dia, profesor_id) → lista de (inicio_mins, fin_mins) asignados
        intervalos_prof:  dict[tuple, list[tuple[int, int]]] = {}
        # (dia, grupo, semestre) → lista de (inicio_mins, fin_mins) asignados
        intervalos_grupo: dict[tuple, list[tuple[int, int]]] = {}
        # (dia, materia_id, grupo) → True si ya hay una sesión de esa materia ese día
        materia_dia: set[tuple] = set()
        # (dia, grupo, semestre) → horas acumuladas
        horas_grupo_dia: dict[tuple, int] = {}

        def _registrar(franja_dia: str, sesion: "SesionClase",
                       inicio: int, fin: int) -> None:
            key_p = (franja_dia, sesion.profesor_id)
            key_g = (franja_dia, sesion.grupo, sesion.semestre)
            intervalos_prof.setdefault(key_p, []).append((inicio, fin))
            intervalos_grupo.setdefault(key_g, []).append((inicio, fin))
            materia_dia.add((franja_dia, sesion.materia_id, sesion.grupo))
            key_h = (franja_dia, sesion.grupo, sesion.semestre)
            horas_grupo_dia[key_h] = (
                horas_grupo_dia.get(key_h, 0) + sesion.duracion_horas
            )

        def _es_valida(franja: "FranjaHoraria", sesion: "SesionClase",
                       inicio: int, fin: int, bloq_ids: set) -> bool:
            # Franja bloqueada
            if franja.id in bloq_ids:
                return False
            # Restricción de jornada
            if inicio < _MEDIODIA and fin > _MEDIODIA:
                return False
            if fin > _FIN_DIA:
                return False
            # Solapamiento temporal real — profesor
            key_p = (franja.dia, sesion.profesor_id)
            if any(inicio < e and s < fin
                   for s, e in intervalos_prof.get(key_p, [])):
                return False
            # Solapamiento temporal real — grupo
            key_g = (franja.dia, sesion.grupo, sesion.semestre)
            if any(inicio < e and s < fin
                   for s, e in intervalos_grupo.get(key_g, [])):
                return False
            # Misma materia (mismo grupo) ya asignada ese día
            if (franja.dia, sesion.materia_id, sesion.grupo) in materia_dia:
                return False
            # Carga horaria diaria del grupo
            key_h = (franja.dia, sesion.grupo, sesion.semestre)
            if horas_grupo_dia.get(key_h, 0) + sesion.duracion_horas > MAX_HORAS_DIARIAS_GRUPO:
                return False
            return True

        for i in sesiones_orden:
            sesion = self.datos.sesiones[i]
            dur    = sesion.duracion_horas * 60
            prof   = self.datos.profesores_dict.get(sesion.profesor_id)
            pref_ids = set(prof.franjas_preferidas) if prof else set()
            bloq_ids = set(prof.franjas_bloqueadas) if prof else set()

            # Candidatos: ranuras que no usamos aún
            # Criterio de ordenación:
            #   1. Franja preferida del profesor (0 = preferida, 1 = no preferida)
            #   2. Para sesiones de 3h: PRIMERO los días que ya tienen 3h del mismo
            #      grupo (así quedan 3h+3h=6h en ese día, satisfaciendo el máximo
            #      diario exacto). LUEGO días vacíos. EVITAR días con 2h (dejarían 1h).
            #   3. Para sesiones de 2h: ordenación aleatoria (base shuffle).
            candidatas = [r for r in range(self.num_ranuras) if r not in ranuras_usadas]
            random.shuffle(candidatas)

            dur_h = sesion.duracion_horas

            def _rank(r: int) -> tuple:
                fi = r // self.num_salones
                if fi >= self.num_franjas:
                    return (2, 99)
                franja = self.datos.franjas[fi]
                pref = 0 if franja.id in pref_ids else 1
                if dur_h >= 3:
                    # Para sesiones largas: priorizar días con sesiones largas ya asignadas
                    # (pairing: 3h + 3h = 6h/day → sin violación de carga diaria)
                    day_h = horas_grupo_dia.get(
                        (franja.dia, sesion.grupo, sesion.semestre), 0
                    )
                    if day_h == dur_h:
                        day_rank = 0   # perfecto: forma par de sesiones iguales
                    elif day_h == 0:
                        day_rank = 1   # ok: día nuevo
                    else:
                        day_rank = 2   # evitar: dejaría horas desparejas
                    return (pref, day_rank)
                return (pref, 0)

            candidatas.sort(key=_rank)

            asignada = False
            for ranura in candidatas:
                fi = ranura // self.num_salones
                if fi >= self.num_franjas:
                    continue
                franja = self.datos.franjas[fi]
                inicio = self._mins(franja.hora_inicio)
                fin    = inicio + dur
                if _es_valida(franja, sesion, inicio, fin, bloq_ids):
                    cromosoma[i] = ranura
                    ranuras_usadas.add(ranura)
                    _registrar(franja.dia, sesion, inicio, fin)
                    asignada = True
                    break

            if not asignada:
                # Fallback 1: relajar materia_mismo_dia y carga_diaria
                for ranura in range(self.num_ranuras):
                    if ranura in ranuras_usadas:
                        continue
                    fi = ranura // self.num_salones
                    if fi >= self.num_franjas:
                        continue
                    franja = self.datos.franjas[fi]
                    if franja.id in bloq_ids:
                        continue
                    inicio = self._mins(franja.hora_inicio)
                    fin    = inicio + dur
                    if inicio < _MEDIODIA and fin > _MEDIODIA:
                        continue
                    if fin > _FIN_DIA:
                        continue
                    # Solo verifica solapamientos reales (sin materia/carga)
                    key_p = (franja.dia, sesion.profesor_id)
                    key_g = (franja.dia, sesion.grupo, sesion.semestre)
                    if any(inicio < e and s < fin
                           for s, e in intervalos_prof.get(key_p, [])):
                        continue
                    if any(inicio < e and s < fin
                           for s, e in intervalos_grupo.get(key_g, [])):
                        continue
                    cromosoma[i] = ranura
                    ranuras_usadas.add(ranura)
                    _registrar(franja.dia, sesion, inicio, fin)
                    asignada = True
                    break

            if not asignada:
                # Fallback 2 (último recurso): cualquier ranura libre
                disponibles = [r for r in range(self.num_ranuras) if r not in ranuras_usadas]
                if disponibles:
                    ranura = random.choice(disponibles)
                    cromosoma[i] = ranura
                    ranuras_usadas.add(ranura)
                    fi = ranura // self.num_salones
                    if fi < self.num_franjas:
                        franja = self.datos.franjas[fi]
                        inicio = self._mins(franja.hora_inicio)
                        fin    = inicio + dur
                        _registrar(franja.dia, sesion, inicio, fin)

        # Si alguna sesión quedó sin asignar (caso extremo), usar aleatorio completo
        if -1 in cromosoma:
            return self._cromosoma_aleatorio()
        return cromosoma

    def _reparar(self, cromosoma: Cromosoma) -> Cromosoma:
        """
        Operador de reparación post-crossover / post-mutación.

        Detecta sesiones con solapamientos temporales (profesor / grupo) o
        asignadas a franjas inválidas (cruza almuerzo, excede 18:00) y las
        reasigna a ranuras libres válidas siguiendo la misma lógica del greedy.

        Al aplicarse sobre cada descendiente después del cruzamiento OX, convierte
        cromosomas infactibles en factibles (o casi factibles) sin coste evolutivo
        extra, corrigiendo el defecto fundamental del OX: que mezcla
        las correspondencias sesión→ranura y genera solapamientos masivos incluso
        partiendo de dos padres perfectos.

        Complejidad: O(n²) para detección + O(k·m) para reparación,
        donde n=num_sesiones, k=sesiones conflictivas, m=|ranuras_libres|.
        Con n=28 (1 semestre) y holgura 8.6× esto es siempre < 1 ms por llamada.
        """
        _MEDIODIA = 12 * 60   # 720 min
        _FIN_DIA  = 18 * 60   # 1080 min
        n         = len(cromosoma)
        cromosoma = cromosoma[:]

        # ── Paso 1: identificar sesiones conflictivas ──────────────────────

        conflictivas: set[int] = set()

        # 1a. Franja fuera de rango o jornada inválida  (O(n))
        for i, ranura in enumerate(cromosoma):
            fi = ranura // self.num_salones
            if fi >= self.num_franjas:
                conflictivas.add(i)
                continue
            franja = self.datos.franjas[fi]
            sesion = self.datos.sesiones[i]
            inicio = self._mins(franja.hora_inicio)
            fin    = inicio + sesion.duracion_horas * 60
            if (inicio < _MEDIODIA and fin > _MEDIODIA) or fin > _FIN_DIA:
                conflictivas.add(i)

        # 1b. Solapamientos de profesor y de grupo  (O(n²))
        for i in range(n):
            if i in conflictivas:
                continue
            fi_i  = cromosoma[i] // self.num_salones
            ses_i = self.datos.sesiones[i]
            fra_i = self.datos.franjas[fi_i]
            s_i   = self._mins(fra_i.hora_inicio)
            e_i   = s_i + ses_i.duracion_horas * 60
            for j in range(i + 1, n):
                if j in conflictivas:
                    continue
                fi_j = cromosoma[j] // self.num_salones
                if fi_j >= self.num_franjas:
                    continue
                ses_j = self.datos.sesiones[j]
                fra_j = self.datos.franjas[fi_j]
                if fra_i.dia != fra_j.dia:
                    continue
                s_j = self._mins(fra_j.hora_inicio)
                e_j = s_j + ses_j.duracion_horas * 60
                if not (s_i < e_j and s_j < e_i):
                    continue
                if ses_i.profesor_id == ses_j.profesor_id:
                    conflictivas.add(j)   # el conflicto se atribuye al segundo
                if ses_i.grupo == ses_j.grupo and ses_i.semestre == ses_j.semestre:
                    conflictivas.add(j)

        if not conflictivas:
            return cromosoma

        # ── Paso 2: liberar ranuras de las sesiones conflictivas ───────────

        ranuras_usadas: set[int] = {
            cromosoma[i] for i in range(n) if i not in conflictivas
        }
        for i in conflictivas:
            cromosoma[i] = -1
        ranuras_libres: set[int] = set(range(self.num_ranuras)) - ranuras_usadas

        # ── Paso 3: reconstruir intervalos de las sesiones no conflictivas ─

        intervalos_prof:  dict[tuple, list[tuple[int, int]]] = {}
        intervalos_grupo: dict[tuple, list[tuple[int, int]]] = {}
        for i in range(n):
            if cromosoma[i] == -1:
                continue
            fi = cromosoma[i] // self.num_salones
            if fi >= self.num_franjas:
                continue
            sesion = self.datos.sesiones[i]
            franja = self.datos.franjas[fi]
            inicio = self._mins(franja.hora_inicio)
            fin    = inicio + sesion.duracion_horas * 60
            intervalos_prof.setdefault(
                (franja.dia, sesion.profesor_id), []
            ).append((inicio, fin))
            intervalos_grupo.setdefault(
                (franja.dia, sesion.grupo, sesion.semestre), []
            ).append((inicio, fin))

        # ── Paso 4: reasignar cada sesión conflictiva ─────────────────────

        for i in sorted(conflictivas):
            sesion   = self.datos.sesiones[i]
            dur      = sesion.duracion_horas * 60
            prof     = self.datos.profesores_dict.get(sesion.profesor_id)
            pref_ids = set(prof.franjas_preferidas) if prof else set()
            bloq_ids = set(prof.franjas_bloqueadas) if prof else set()

            # Mezclar aleatoriamente y luego priorizar franjas preferidas
            candidatas = list(ranuras_libres)
            random.shuffle(candidatas)
            candidatas.sort(key=lambda r: 0 if (
                r // self.num_salones < self.num_franjas
                and self.datos.franjas[r // self.num_salones].id in pref_ids
            ) else 1)

            asignada = False
            for ranura in candidatas:
                fi = ranura // self.num_salones
                if fi >= self.num_franjas:
                    continue
                franja = self.datos.franjas[fi]
                if franja.id in bloq_ids:
                    continue
                inicio = self._mins(franja.hora_inicio)
                fin    = inicio + dur
                if inicio < _MEDIODIA and fin > _MEDIODIA:
                    continue
                if fin > _FIN_DIA:
                    continue
                key_p = (franja.dia, sesion.profesor_id)
                key_g = (franja.dia, sesion.grupo, sesion.semestre)
                if any(s < fin and inicio < e
                       for s, e in intervalos_prof.get(key_p, [])):
                    continue
                if any(s < fin and inicio < e
                       for s, e in intervalos_grupo.get(key_g, [])):
                    continue
                # Ranura válida → registrar
                cromosoma[i] = ranura
                ranuras_libres.discard(ranura)
                intervalos_prof.setdefault(key_p, []).append((inicio, fin))
                intervalos_grupo.setdefault(key_g, []).append((inicio, fin))
                asignada = True
                break

            if not asignada:
                # Fallback: cualquier ranura libre sin verificar restricciones
                # (el fitness lo penalizará; evita dejar cromosoma incompleto)
                for ranura in list(ranuras_libres):
                    cromosoma[i] = ranura
                    ranuras_libres.discard(ranura)
                    fi = ranura // self.num_salones
                    if fi < self.num_franjas:
                        franja = self.datos.franjas[fi]
                        inicio = self._mins(franja.hora_inicio)
                        fin    = inicio + dur
                        intervalos_prof.setdefault(
                            (franja.dia, sesion.profesor_id), []
                        ).append((inicio, fin))
                        intervalos_grupo.setdefault(
                            (franja.dia, sesion.grupo, sesion.semestre), []
                        ).append((inicio, fin))
                    asignada = True
                    break

            if not asignada:
                # Extremo absoluto (sin ranuras libres): asignar aleatoriamente
                cromosoma[i] = random.randint(0, self.num_ranuras - 1)

        return cromosoma

    def _inicializar_poblacion(self) -> list[Cromosoma]:
        """
        60 % heurístico (greedy) + 40 % aleatorio reparado.

        El incremento de greedy al 60 % (desde 30 %) asegura que la mayoría
        de los individuos iniciales ya no tengan conflictos de solapamiento.
        Los cromosomas aleatorios se pasan por _reparar() para arrancar sin
        violaciones de jornada ni solapamientos, reduciendo la brecha de calidad
        entre ambos grupos y acortando el número de generaciones necesarias.
        """
        n_greedy = max(1, self.params.tam_poblacion * 60 // 100)
        n_random = self.params.tam_poblacion - n_greedy
        return (
            [self._cromosoma_greedy() for _ in range(n_greedy)]
            + [self._reparar(self._cromosoma_aleatorio()) for _ in range(n_random)]
        )

    # ── Selección por torneo ──────────────────────────────────────────────────

    def _seleccion_torneo(
        self, poblacion: list[Cromosoma], fitnesses: list[float]
    ) -> Cromosoma:
        indices = random.sample(range(len(poblacion)), self.params.tam_torneo)
        ganador = max(indices, key=lambda i: fitnesses[i])
        return deepcopy(poblacion[ganador])

    # ── Order Crossover (OX) ──────────────────────────────────────────────────

    @staticmethod
    def _cruzamiento_ox(
        padre1: Cromosoma, padre2: Cromosoma, prob: float
    ) -> Tuple[Cromosoma, Cromosoma]:
        if random.random() > prob or len(padre1) < 3:
            return deepcopy(padre1), deepcopy(padre2)

        n      = len(padre1)
        inicio = random.randint(0, n - 2)
        fin    = random.randint(inicio + 1, n - 1)

        def _hijo(p1: Cromosoma, p2: Cromosoma) -> Cromosoma:
            segmento  = set(p1[inicio : fin + 1])
            hijo      = [None] * n
            hijo[inicio : fin + 1] = p1[inicio : fin + 1]
            restantes = [x for x in p2 if x not in segmento]
            pos = 0
            for k in range(n):
                if hijo[k] is None:
                    hijo[k] = restantes[pos]
                    pos += 1
            return hijo

        return _hijo(padre1, padre2), _hijo(padre2, padre1)

    # ── Mutación ──────────────────────────────────────────────────────────────

    @staticmethod
    def _mutar(cromosoma: Cromosoma, prob: float) -> Cromosoma:
        """
        Aplica con probabilidad `prob` uno de los tres operadores de mutación
        elegido uniformemente al azar: swap, inversión o inserción.
        """
        if random.random() >= prob:
            return cromosoma[:]
        return random.choice(_OPERADORES_MUTACION)(cromosoma)

    # ── Evaluación paralela ───────────────────────────────────────────────────

    def _evaluar_poblacion(
        self,
        poblacion: list[Cromosoma],
        executor: Optional[ProcessPoolExecutor],
    ) -> list[Tuple[float, int]]:
        """
        Evalúa el fitness de toda la población.
        Usa el ProcessPoolExecutor si está disponible; en caso contrario (o si
        falla por cualquier motivo ambiental) cae al modo secuencial.
        """
        if executor is None:
            return [self.calcular_fitness(c) for c in poblacion]
        chunksize = max(1, len(poblacion) // (executor._max_workers * 2))
        try:
            return list(executor.map(_worker_fitness, poblacion, chunksize=chunksize))
        except Exception:
            return [self.calcular_fitness(c) for c in poblacion]

    # ── Bucle principal (generador) ───────────────────────────────────────────

    def evolucionar(
        self, semilla: int | None = None
    ) -> Generator[ResultadoGeneracion, None, None]:
        """
        Ejecuta el AG generación a generación y hace yield con las estadísticas
        de cada generación para que la API pueda acumular el historial completo.

        Criterios de parada:
          1. "optimo_encontrado"       — el mejor individuo tiene 0 conflictos.
          2. "estancamiento"           — sin mejora durante params.paciencia generaciones.
          3. "generaciones_completadas"— se alcanzó num_generaciones.

        Optimizaciones de convergencia:
          · Reinicio parcial: cuando el estancamiento llega a la mitad del umbral
            de paciencia, se conservan los élites y se regenera el resto de la
            población (un único reinicio por ejecución).
          · Evaluación paralela: se usa ProcessPoolExecutor para distribuir el
            cálculo del fitness entre los núcleos disponibles del servidor.
        """
        if semilla is not None:
            random.seed(semilla)

        # ── Configurar evaluación paralela ────────────────────────────────────
        n_workers  = min(4, max(1, (os.cpu_count() or 1) - 1))
        use_par    = n_workers > 1 and self.num_sesiones >= 20
        executor: Optional[ProcessPoolExecutor] = (
            ProcessPoolExecutor(
                max_workers=n_workers,
                initializer=_worker_init,
                initargs=(self,),
            )
            if use_par else None
        )

        try:
            poblacion = self._inicializar_poblacion()
            mejor_fitness_hist: float = -1.0
            gen_sin_mejora:     int   = 0
            reinicios:          int   = 0
            reinicio_umbral:    int   = max(1, self.params.paciencia // 2)

            for gen in range(1, self.params.num_generaciones + 1):

                # ── Evaluación ────────────────────────────────────────────────
                evaluaciones = self._evaluar_poblacion(poblacion, executor)
                fitnesses    = [e[0] for e in evaluaciones]
                conflictos_  = [e[1] for e in evaluaciones]

                indices_ord  = sorted(range(len(fitnesses)),
                                      key=lambda i: fitnesses[i], reverse=True)
                mejor_idx    = indices_ord[0]
                mejor_fitness = fitnesses[mejor_idx]
                promedio      = sum(fitnesses) / len(fitnesses)
                peor_fitness  = fitnesses[indices_ord[-1]]

                # ── Seguimiento de estancamiento ──────────────────────────────
                if mejor_fitness > mejor_fitness_hist + 0.01:
                    mejor_fitness_hist = mejor_fitness
                    gen_sin_mejora     = 0
                else:
                    gen_sin_mejora += 1

                # ── Criterios de parada ───────────────────────────────────────
                sin_conflictos = conflictos_[mejor_idx] == 0
                estancado      = gen_sin_mejora >= self.params.paciencia
                ultima_normal  = gen == self.params.num_generaciones
                es_ultima      = sin_conflictos or estancado or ultima_normal

                if sin_conflictos:
                    razon = "optimo_encontrado"
                elif estancado:
                    razon = "estancamiento"
                elif ultima_normal:
                    razon = "generaciones_completadas"
                else:
                    razon = ""

                # ── Top-3 individuos ──────────────────────────────────────────
                top_n = min(3, len(poblacion))
                top_individuos = []
                for rank, idx in enumerate(indices_ord[:top_n], start=1):
                    top_individuos.append({
                        "rank":       rank,
                        "fitness":    round(fitnesses[idx], 2),
                        "conflictos": conflictos_[idx],
                        "horario":    [a.to_dict() for a in self.decodificar(poblacion[idx])],
                    })

                # ── Detalle de conflictos (solo en la generación final) ───────
                detalle: list[dict] = []
                if es_ultima:
                    detalle = self.detectar_conflictos_detalle(poblacion[mejor_idx])

                yield ResultadoGeneracion(
                    numero=gen,
                    mejor_fitness=mejor_fitness,
                    promedio_fitness=promedio,
                    peor_fitness=peor_fitness,
                    conflictos_mejor=conflictos_[mejor_idx],
                    mejor_horario=[a.to_dict() for a in self.decodificar(poblacion[mejor_idx])],
                    top_individuos=top_individuos,
                    conflictos_detalle=detalle,
                    razon_parada=razon,
                )

                if es_ultima:
                    break

                # ── Reinicios parciales (hasta MAX_REINICIOS por ejecución) ───
                # Cada reinicio conserva los mejores individuos y regenera el
                # resto mezclando greedy (50 %) con aleatorio reparado (50 %).
                # Permitir múltiples reinicios evita que el AG quede atrapado
                # definitivamente en un óptimo local tras el primer estancamiento.
                if gen_sin_mejora >= reinicio_umbral and reinicios < MAX_REINICIOS:
                    reinicios     += 1
                    gen_sin_mejora = 0
                    n_conservar    = max(self.params.num_elite, len(poblacion) // 4)
                    elites         = [deepcopy(poblacion[i]) for i in indices_ord[:n_conservar]]
                    n_nuevos       = self.params.tam_poblacion - n_conservar
                    nuevos = [
                        self._cromosoma_greedy()
                        if random.random() < 0.5
                        else self._reparar(self._cromosoma_aleatorio())
                        for _ in range(n_nuevos)
                    ]
                    poblacion = elites + nuevos
                    continue

                # ── Evolución normal ──────────────────────────────────────────
                nueva_pobl = [deepcopy(poblacion[i]) for i in indices_ord[:self.params.num_elite]]
                while len(nueva_pobl) < self.params.tam_poblacion:
                    p1 = self._seleccion_torneo(poblacion, fitnesses)
                    p2 = self._seleccion_torneo(poblacion, fitnesses)
                    h1, h2 = self._cruzamiento_ox(p1, p2, self.params.prob_cruzamiento)
                    h1 = self._mutar(h1, self.params.prob_mutacion)
                    h2 = self._mutar(h2, self.params.prob_mutacion)
                    # Reparar solapamientos y violaciones de jornada introducidos
                    # por OX (que mezcla correspondencias sesión→ranura) y por
                    # los operadores de mutación (swap/inversión/inserción).
                    h1 = self._reparar(h1)
                    h2 = self._reparar(h2)
                    nueva_pobl.append(h1)
                    if len(nueva_pobl) < self.params.tam_poblacion:
                        nueva_pobl.append(h2)
                poblacion = nueva_pobl

        finally:
            if executor:
                executor.shutdown(wait=True)


# ── Función de conveniencia para la API ───────────────────────────────────────

def crear_ag_desde_store(
    params: ParametrosAG,
    semestres_filtro: list[int] | None = None,
) -> AlgoritmoGenetico:
    """
    Construye un AlgoritmoGenetico con los datos actuales del DataStore.
    Si `semestres_filtro` es una lista no vacía, solo participan las sesiones
    de esos semestres; de lo contrario se incluyen todas.
    """
    from storage import store

    bloques_unicos: list[tuple[str, str]] = list(dict.fromkeys(
        (f.hora_inicio, f.hora_fin) for f in store.franjas
    ))
    datos = DatosAG(
        sesiones         = store.get_sesiones(semestres=semestres_filtro),
        franjas          = store.franjas,
        salones          = store.salones,
        profesores_dict  = {p.id: p for p in store.profesores},
        bloques          = bloques_unicos,
    )
    return AlgoritmoGenetico(datos, params)
