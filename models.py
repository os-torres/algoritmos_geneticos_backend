from dataclasses import dataclass, field
from typing import List


@dataclass
class Materia:
    id: int
    nombre: str
    semestre: int           # 1-10
    grupo: str              # "A", "B", "C"…
    creditos: int
    bloques: List[int]      # Horas por sesión, p.ej. [3, 2] = 5h/sem en 2 sesiones
    num_alumnos: int = 30           # Alumnos estimados (valida capacidad del salón)
    requiere_laboratorio: bool = False  # True → debe asignarse a un salón tipo "laboratorio"

    @property
    def sesiones_por_semana(self) -> int:
        return len(self.bloques)

    @property
    def horas_semanales(self) -> int:
        return sum(self.bloques)


@dataclass
class Profesor:
    id: int
    nombre: str
    materias_ids: List[int]
    franjas_preferidas: List[int]       # IDs de franjas preferidas (bonificación en fitness)
    franjas_bloqueadas: List[int] = field(default_factory=list)  # IDs donde NO puede dictar (restricción dura)


@dataclass
class Salon:
    id: int
    nombre: str
    capacidad: int
    tipo: str = "aula"      # "aula" | "laboratorio" | "auditorio"


@dataclass
class FranjaHoraria:
    id: int
    dia: str
    hora_inicio: str    # "HH:MM"
    hora_fin: str       # "HH:MM"

    def __str__(self) -> str:
        return f"{self.dia} {self.hora_inicio}-{self.hora_fin}"


@dataclass
class SesionClase:
    """Una sesión = una ocurrencia semanal de una materia con duración específica."""
    id: int
    materia_id: int
    profesor_id: int
    grupo: str
    semestre: int = 1
    duracion_horas: int = 2
    nombre_materia: str = ""
    nombre_profesor: str = ""
    num_alumnos: int = 30           # Propagado desde Materia
    requiere_laboratorio: bool = False  # Propagado desde Materia


@dataclass
class Asignacion:
    """Resultado decodificado: sesión → (franja, salón)."""
    sesion: SesionClase
    franja: FranjaHoraria
    salon: Salon

    def to_dict(self) -> dict:
        h, m = map(int, self.franja.hora_inicio.split(':'))
        total_min = h * 60 + m + self.sesion.duracion_horas * 60
        hora_fin_real = f"{total_min // 60:02d}:{total_min % 60:02d}"
        return {
            "sesion_id":            self.sesion.id,
            "materia_id":           self.sesion.materia_id,
            "materia":              self.sesion.nombre_materia,
            "profesor":             self.sesion.nombre_profesor,
            "grupo":                self.sesion.grupo,
            "semestre":             self.sesion.semestre,
            "duracion_horas":       self.sesion.duracion_horas,
            "num_alumnos":          self.sesion.num_alumnos,
            "requiere_laboratorio": self.sesion.requiere_laboratorio,
            "dia":                  self.franja.dia,
            "hora_inicio":          self.franja.hora_inicio,
            "hora_fin":             hora_fin_real,
            "salon":                self.salon.nombre,
            "salon_capacidad":      self.salon.capacidad,
            "salon_tipo":           self.salon.tipo,
        }


@dataclass
class ResultadoGeneracion:
    numero: int
    mejor_fitness: float
    promedio_fitness: float
    peor_fitness: float
    conflictos_mejor: int
    mejor_horario: List[dict] = field(default_factory=list)
    top_individuos: List[dict] = field(default_factory=list)
    conflictos_detalle: List[dict] = field(default_factory=list)
    razon_parada: str = ""

    def to_dict(self) -> dict:
        return {
            "numero":             self.numero,
            "mejor_fitness":      round(self.mejor_fitness, 2),
            "promedio_fitness":   round(self.promedio_fitness, 2),
            "peor_fitness":       round(self.peor_fitness, 2),
            "conflictos_mejor":   self.conflictos_mejor,
            "mejor_horario":      self.mejor_horario,
            "top_individuos":     self.top_individuos,
            "conflictos_detalle": self.conflictos_detalle,
            "razon_parada":       self.razon_parada,
        }


@dataclass
class ParametrosAG:
    tam_poblacion:    int   = 100
    num_generaciones: int   = 200
    prob_cruzamiento: float = 0.85
    prob_mutacion:    float = 0.10
    num_elite:        int   = 2
    tam_torneo:       int   = 5
    paciencia:        int   = 30
