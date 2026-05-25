"""
Algoritmo Genético para optimización de horarios universitarios.

Representación por PERMUTACIÓN:
  - Hay N_RANURAS posibles = len(franjas) × len(salones).
  - Un cromosoma es una lista de len(sesiones) enteros únicos, tomados
    de una permutación de [0, N_RANURAS), garantizando que ninguna
    combinación (franja, salón) quede asignada a dos sesiones a la vez.
  - ranura = franja_idx * num_salones + salon_idx

Operadores genéticos:
  - Selección:   Torneo determinístico
  - Cruzamiento: Order Crossover (OX) — preserva la propiedad de permutación
  - Mutación:    Intercambio de dos posiciones (swap mutation)
  - Elitismo:    Los N mejores individuos pasan intactos a la siguiente generación
"""

import random
from copy import deepcopy
from dataclasses import dataclass, field
from typing import Generator, List, Tuple


def _mins(t: str) -> int:
    """Convierte 'HH:MM' a minutos desde medianoche."""
    h, m = map(int, t.split(':'))
    return h * 60 + m

from models import (
    Asignacion, FranjaHoraria, ParametrosAG, Profesor,
    ResultadoGeneracion, Salon, SesionClase,
)

Cromosoma = List[int]


# ---------------------------------------------------------------------------
# Datos de entrada que el AG necesita (inyectados, no importados globalmente)
# ---------------------------------------------------------------------------

@dataclass
class DatosAG:
    sesiones:       list[SesionClase]
    franjas:        list[FranjaHoraria]
    salones:        list[Salon]
    profesores_dict: dict[int, Profesor]
    bloques:        list[tuple[str, str]]    # lista de (hora_inicio, hora_fin)


# ---------------------------------------------------------------------------
# Clase principal del algoritmo genético
# ---------------------------------------------------------------------------

class AlgoritmoGenetico:

    def __init__(self, datos: DatosAG, params: ParametrosAG):
        self.datos         = datos
        self.params        = params
        self.num_franjas   = len(datos.franjas)
        self.num_salones   = len(datos.salones)
        self.num_ranuras   = self.num_franjas * self.num_salones
        self.num_sesiones  = len(datos.sesiones)

    # ------------------------------------------------------------------
    # Decodificación
    # ------------------------------------------------------------------

    def decodificar(self, cromosoma: Cromosoma) -> list[Asignacion]:
        asignaciones: list[Asignacion] = []
        for i, ranura in enumerate(cromosoma):
            franja_idx = ranura // self.num_salones
            salon_idx  = ranura % self.num_salones
            asignaciones.append(Asignacion(
                sesion=self.datos.sesiones[i],
                franja=self.datos.franjas[franja_idx],
                salon=self.datos.salones[salon_idx],
            ))
        return asignaciones

    # ------------------------------------------------------------------
    # Función fitness
    # ------------------------------------------------------------------

    def calcular_fitness(self, cromosoma: Cromosoma) -> Tuple[float, int]:
        """
        Evalúa la calidad de un horario.

        Penalizaciones:
          -200  Profesor dicta dos clases en la misma franja horaria
          -200  Mismo grupo tiene dos clases en la misma franja horaria
          -30   Todo un grupo concentrado en un único día
          -5    Clase en el último bloque del día (bloque tardío)

        Bonificaciones:
          +15   Profesor dicta en franja de su preferencia
          +20   Grupo con clases distribuidas en ≥ 3 días distintos
          +5    Grupo con clases en 2 días distintos
          +15   Todas las clases de un profesor son solo de mañana o solo de tarde

        Retorna: (fitness, num_conflictos)
        """
        asignaciones = self.decodificar(cromosoma)
        penalizacion  = 0.0
        bonificacion  = 0.0
        conflictos    = 0
        n = len(asignaciones)

        # Escala las penalizaciones según el tamaño del problema.
        # Con 14 sesiones (1 semestre) escala = 1.0 → penalizaciones originales.
        # Con 204 sesiones (pensum completo) escala ≈ 14.6 → penalizaciones ~14×
        # menores, evitando que el fitness colapse a 0 en problemas grandes y
        # permitiendo que el algoritmo converja correctamente.
        escala = max(1.0, n / 14.0)

        # O(n²) — detección de solapamiento REAL considerando duración de cada sesión.
        # Dos sesiones solapan si están el mismo día y sus rangos horarios se cruzan.
        # Esto es más preciso que comparar solo franja.id, ya que una clase de 3h
        # puede solapar con una de 2h que empieza 1h después.
        for i in range(n):
            for j in range(i + 1, n):
                a_i, a_j = asignaciones[i], asignaciones[j]
                # Solo pueden solapar si están el mismo día
                if a_i.franja.dia != a_j.franja.dia:
                    continue
                s_i = _mins(a_i.franja.hora_inicio)
                e_i = s_i + a_i.sesion.duracion_horas * 60
                s_j = _mins(a_j.franja.hora_inicio)
                e_j = s_j + a_j.sesion.duracion_horas * 60
                # Solapamiento: [s_i, e_i) ∩ [s_j, e_j) ≠ ∅
                if s_i < e_j and s_j < e_i:
                    if a_i.sesion.profesor_id == a_j.sesion.profesor_id:
                        penalizacion += 200.0 / escala
                        conflictos   += 1
                    if a_i.sesion.grupo == a_j.sesion.grupo:
                        penalizacion += 200.0 / escala
                        conflictos   += 1

        # Penalizar misma materia el mismo día
        mat_dia: dict[tuple, int] = {}
        for asig in asignaciones:
            key = (asig.sesion.materia_id, asig.franja.dia)
            mat_dia[key] = mat_dia.get(key, 0) + 1
        for count in mat_dia.values():
            if count > 1:
                penalizacion += (300.0 / escala) * (count - 1)
                conflictos   += count - 1

        # Preferencias de profesores
        for asig in asignaciones:
            prof = self.datos.profesores_dict.get(asig.sesion.profesor_id)
            if prof and asig.franja.id in prof.franjas_preferidas:
                bonificacion += 15

        # Distribución por grupo
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

        # Penalizar bloques tardíos
        for asig in asignaciones:
            try:
                idx = self.datos.bloques.index((asig.franja.hora_inicio, asig.franja.hora_fin))
                if idx == len(self.datos.bloques) - 1:
                    penalizacion += 5.0 / escala
            except ValueError:
                pass

        # Cohesión horaria del profesor (todo mañana o todo tarde)
        horas_prof: dict[int, list[str]] = {}
        for asig in asignaciones:
            horas_prof.setdefault(asig.sesion.profesor_id, []).append(asig.franja.hora_inicio)

        for horas in horas_prof.values():
            if all(h < "13:00" for h in horas) or all(h >= "14:00" for h in horas):
                bonificacion += 15

        # ── Restricciones duras de jornada ─────────────────────────────────
        # 1. La sesión NO puede cruzar el almuerzo (12:00–14:00):
        #    si empieza antes de las 12:00 y termina después de las 12:00 → inválido.
        # 2. La sesión NO puede terminar después de las 18:00.
        # Penalización alta (escalada) para que el AG las evite siempre.
        _MEDIODIA = 12 * 60   # 720 min
        _FIN_DIA  = 18 * 60   # 1080 min
        for asig in asignaciones:
            inicio = _mins(asig.franja.hora_inicio)
            fin    = inicio + asig.sesion.duracion_horas * 60
            if inicio < _MEDIODIA and fin > _MEDIODIA:
                penalizacion += 400.0 / escala
                conflictos   += 1
            if fin > _FIN_DIA:
                penalizacion += 400.0 / escala
                conflictos   += 1

        fitness = max(0.0, 1000.0 - penalizacion + bonificacion)
        return fitness, conflictos

    # ------------------------------------------------------------------
    # Detalle de conflictos (para el reporte final)
    # ------------------------------------------------------------------

    def detectar_conflictos_detalle(self, cromosoma: Cromosoma) -> list[dict]:
        """
        Analiza el horario y devuelve una lista descriptiva de cada conflicto.
        Tipos detectados:
          - "profesor"         : mismo profesor en dos sesiones solapadas
          - "grupo"            : mismo grupo en dos sesiones solapadas
          - "materia_mismo_dia": la misma materia (grupo) aparece más de una vez el mismo día
        """
        asignaciones = self.decodificar(cromosoma)
        conflictos: list[dict] = []
        n = len(asignaciones)

        for i in range(n):
            for j in range(i + 1, n):
                a_i, a_j = asignaciones[i], asignaciones[j]
                if a_i.franja.dia != a_j.franja.dia:
                    continue
                s_i = _mins(a_i.franja.hora_inicio)
                e_i = s_i + a_i.sesion.duracion_horas * 60
                s_j = _mins(a_j.franja.hora_inicio)
                e_j = s_j + a_j.sesion.duracion_horas * 60
                if not (s_i < e_j and s_j < e_i):
                    continue

                # Rango horario de solapamiento real
                sol_ini = max(s_i, s_j)
                sol_fin = min(e_i, e_j)
                rango   = f"{sol_ini // 60:02d}:{sol_ini % 60:02d}–{sol_fin // 60:02d}:{sol_fin % 60:02d}"

                if a_i.sesion.profesor_id == a_j.sesion.profesor_id:
                    conflictos.append({
                        "tipo":      "profesor",
                        "dia":       a_i.franja.dia,
                        "hora_solapamiento": rango,
                        "profesor":  a_i.sesion.nombre_profesor,
                        "sesion_a": {
                            "materia":  a_i.sesion.nombre_materia,
                            "grupo":    a_i.sesion.grupo,
                            "semestre": a_i.sesion.semestre,
                            "salon":    a_i.salon.nombre,
                            "hora_inicio": a_i.franja.hora_inicio,
                            "hora_fin":    f"{e_i // 60:02d}:{e_i % 60:02d}",
                        },
                        "sesion_b": {
                            "materia":  a_j.sesion.nombre_materia,
                            "grupo":    a_j.sesion.grupo,
                            "semestre": a_j.sesion.semestre,
                            "salon":    a_j.salon.nombre,
                            "hora_inicio": a_j.franja.hora_inicio,
                            "hora_fin":    f"{e_j // 60:02d}:{e_j % 60:02d}",
                        },
                    })

                if a_i.sesion.grupo == a_j.sesion.grupo and \
                   a_i.sesion.semestre == a_j.sesion.semestre:
                    conflictos.append({
                        "tipo":      "grupo",
                        "dia":       a_i.franja.dia,
                        "hora_solapamiento": rango,
                        "grupo":     a_i.sesion.grupo,
                        "semestre":  a_i.sesion.semestre,
                        "sesion_a": {
                            "materia":  a_i.sesion.nombre_materia,
                            "profesor": a_i.sesion.nombre_profesor,
                            "salon":    a_i.salon.nombre,
                            "hora_inicio": a_i.franja.hora_inicio,
                            "hora_fin":    f"{e_i // 60:02d}:{e_i % 60:02d}",
                        },
                        "sesion_b": {
                            "materia":  a_j.sesion.nombre_materia,
                            "profesor": a_j.sesion.nombre_profesor,
                            "salon":    a_j.salon.nombre,
                            "hora_inicio": a_j.franja.hora_inicio,
                            "hora_fin":    f"{e_j // 60:02d}:{e_j % 60:02d}",
                        },
                    })

        # Misma materia (mismo grupo) programada más de una vez el mismo día
        mat_dia: dict[tuple, list] = {}
        for asig in asignaciones:
            key = (asig.sesion.materia_id, asig.sesion.grupo, asig.franja.dia)
            mat_dia.setdefault(key, []).append(asig)

        for (mat_id, grupo, dia), asigs in mat_dia.items():
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
                            "hora_fin": (lambda s=_mins(a.franja.hora_inicio) +
                                         a.sesion.duracion_horas * 60:
                                         f"{s // 60:02d}:{s % 60:02d}")(),
                            "salon": a.salon.nombre,
                        }
                        for a in asigs
                    ],
                })

        # Sesiones fuera de jornada institucional
        _MEDIODIA = 12 * 60
        _FIN_DIA  = 18 * 60
        for asig in asignaciones:
            inicio = _mins(asig.franja.hora_inicio)
            fin    = inicio + asig.sesion.duracion_horas * 60
            h_fin  = f"{fin // 60:02d}:{fin % 60:02d}"
            if inicio < _MEDIODIA and fin > _MEDIODIA:
                conflictos.append({
                    "tipo":    "horario_invalido",
                    "dia":     asig.franja.dia,
                    "razon":   "cruza el almuerzo (12:00–14:00)",
                    "materia": asig.sesion.nombre_materia,
                    "grupo":   asig.sesion.grupo,
                    "semestre": asig.sesion.semestre,
                    "hora_inicio": asig.franja.hora_inicio,
                    "hora_fin":   h_fin,
                    "salon":   asig.salon.nombre,
                })
            elif fin > _FIN_DIA:
                conflictos.append({
                    "tipo":    "horario_invalido",
                    "dia":     asig.franja.dia,
                    "razon":   f"termina a las {h_fin} (después de las 18:00)",
                    "materia": asig.sesion.nombre_materia,
                    "grupo":   asig.sesion.grupo,
                    "semestre": asig.sesion.semestre,
                    "hora_inicio": asig.franja.hora_inicio,
                    "hora_fin":   h_fin,
                    "salon":   asig.salon.nombre,
                })

        return conflictos

    # ------------------------------------------------------------------
    # Inicialización
    # ------------------------------------------------------------------

    def _cromosoma_aleatorio(self) -> Cromosoma:
        """Permutación aleatoria: NUM_SESIONES ranuras únicas de [0, NUM_RANURAS)."""
        return random.sample(range(self.num_ranuras), self.num_sesiones)

    def _inicializar_poblacion(self) -> list[Cromosoma]:
        return [self._cromosoma_aleatorio() for _ in range(self.params.tam_poblacion)]

    # ------------------------------------------------------------------
    # Selección por torneo
    # ------------------------------------------------------------------

    def _seleccion_torneo(self, poblacion: list[Cromosoma], fitnesses: list[float]) -> Cromosoma:
        indices  = random.sample(range(len(poblacion)), self.params.tam_torneo)
        ganador  = max(indices, key=lambda i: fitnesses[i])
        return deepcopy(poblacion[ganador])

    # ------------------------------------------------------------------
    # Order Crossover (OX)
    # ------------------------------------------------------------------

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
            hijo     = [None] * n
            segmento = set(p1[inicio : fin + 1])
            hijo[inicio : fin + 1] = p1[inicio : fin + 1]
            restantes = [x for x in p2 if x not in segmento]
            pos = 0
            for i in range(n):
                if hijo[i] is None:
                    hijo[i] = restantes[pos]
                    pos += 1
            return hijo

        return _hijo(padre1, padre2), _hijo(padre2, padre1)

    # ------------------------------------------------------------------
    # Mutación por intercambio
    # ------------------------------------------------------------------

    @staticmethod
    def _mutacion(cromosoma: Cromosoma, prob: float) -> Cromosoma:
        resultado = cromosoma[:]
        if random.random() < prob:
            i, j = random.sample(range(len(resultado)), 2)
            resultado[i], resultado[j] = resultado[j], resultado[i]
        return resultado

    # ------------------------------------------------------------------
    # Bucle principal (generador)
    # ------------------------------------------------------------------

    def evolucionar(self, semilla: int | None = None) -> Generator[ResultadoGeneracion, None, None]:
        """
        Ejecuta el AG generación por generación.
        Es un generador: hace yield con las estadísticas de cada generación
        para que el WebSocket pueda enviar actualizaciones en tiempo real.

        Criterios de parada anticipada:
          1. «optimo_encontrado»: el mejor individuo tiene 0 conflictos.
          2. «estancamiento»: el mejor fitness no mejora en self.params.paciencia
             generaciones consecutivas.
          3. «generaciones_completadas»: se alcanzó num_generaciones (caso normal).
        """
        if semilla is not None:
            random.seed(semilla)

        poblacion = self._inicializar_poblacion()
        mejor_fitness_historico: float = -1.0
        generaciones_sin_mejora: int   = 0

        for gen in range(1, self.params.num_generaciones + 1):
            evaluaciones = [self.calcular_fitness(c) for c in poblacion]
            fitnesses    = [e[0] for e in evaluaciones]
            conflictos_  = [e[1] for e in evaluaciones]

            # Ordenar de mayor a menor fitness (se reutiliza para elitismo)
            indices_ord   = sorted(range(len(fitnesses)), key=lambda i: fitnesses[i], reverse=True)
            mejor_idx     = indices_ord[0]
            mejor_fitness = fitnesses[mejor_idx]
            promedio      = sum(fitnesses) / len(fitnesses)
            peor_fitness  = fitnesses[indices_ord[-1]]
            mejor_horario = self.decodificar(poblacion[mejor_idx])

            # ── Seguimiento de estancamiento ──────────────────────────────────
            if mejor_fitness > mejor_fitness_historico + 0.01:
                mejor_fitness_historico = mejor_fitness
                generaciones_sin_mejora = 0
            else:
                generaciones_sin_mejora += 1

            # ── Determinar si esta es la última generación ────────────────────
            sin_conflictos   = (conflictos_[mejor_idx] == 0)
            estancado        = (generaciones_sin_mejora >= self.params.paciencia)
            ultima_normal    = (gen == self.params.num_generaciones)
            es_ultima        = sin_conflictos or estancado or ultima_normal

            if sin_conflictos:
                razon = "optimo_encontrado"
            elif estancado:
                razon = "estancamiento"
            elif ultima_normal:
                razon = "generaciones_completadas"
            else:
                razon = ""

            # Top-3 individuos con horario completo (para la vista de población)
            top_n = min(3, len(poblacion))
            top_individuos = []
            for rank, idx in enumerate(indices_ord[:top_n], start=1):
                horario_ind = self.decodificar(poblacion[idx])
                top_individuos.append({
                    "rank":       rank,
                    "fitness":    round(fitnesses[idx], 2),
                    "conflictos": conflictos_[idx],
                    "horario":    [a.to_dict() for a in horario_ind],
                })

            # Detalle de conflictos: solo en la última generación efectiva
            detalle: list[dict] = []
            if es_ultima:
                detalle = self.detectar_conflictos_detalle(poblacion[mejor_idx])

            yield ResultadoGeneracion(
                numero=gen,
                mejor_fitness=mejor_fitness,
                promedio_fitness=promedio,
                peor_fitness=peor_fitness,
                conflictos_mejor=conflictos_[mejor_idx],
                mejor_horario=[a.to_dict() for a in mejor_horario],
                top_individuos=top_individuos,
                conflictos_detalle=detalle,
                razon_parada=razon,
            )

            # ── Parada anticipada ─────────────────────────────────────────────
            if sin_conflictos or estancado:
                break

            # ── Nueva generación ──────────────────────────────────────────────
            nueva_pobl   = [deepcopy(poblacion[i]) for i in indices_ord[: self.params.num_elite]]

            while len(nueva_pobl) < self.params.tam_poblacion:
                p1 = self._seleccion_torneo(poblacion, fitnesses)
                p2 = self._seleccion_torneo(poblacion, fitnesses)
                h1, h2 = self._cruzamiento_ox(p1, p2, self.params.prob_cruzamiento)
                h1 = self._mutacion(h1, self.params.prob_mutacion)
                h2 = self._mutacion(h2, self.params.prob_mutacion)
                nueva_pobl.append(h1)
                if len(nueva_pobl) < self.params.tam_poblacion:
                    nueva_pobl.append(h2)

            poblacion = nueva_pobl


# ---------------------------------------------------------------------------
# Función de conveniencia para la API
# ---------------------------------------------------------------------------

def crear_ag_desde_store(
    params: ParametrosAG,
    semestres_filtro: list[int] | None = None,
) -> "AlgoritmoGenetico":
    """
    Construye un AlgoritmoGenetico usando los datos actuales del DataStore.
    Si `semestres_filtro` es una lista, solo se incluyen las sesiones de esos semestres.
    Si es None o lista vacía, se incluyen todas las sesiones.
    """
    from storage import store

    bloques_unicos: list[tuple[str, str]] = list(dict.fromkeys(
        (f.hora_inicio, f.hora_fin) for f in store.franjas
    ))

    datos = DatosAG(
        sesiones        = store.get_sesiones(semestres=semestres_filtro),
        franjas         = store.franjas,
        salones         = store.salones,
        profesores_dict = {p.id: p for p in store.profesores},
        bloques         = bloques_unicos,
    )
    return AlgoritmoGenetico(datos, params)
