"""
Capa de persistencia — almacena y gestiona los datos del usuario en disco (JSON).

Al iniciar el servidor:
  - Si existe data/data_store.json → lo carga.
  - Si no existe → carga los datos de muestra de data.py y guarda el archivo.

El usuario puede modificar materias, profesores, salones y franjas a través de
los endpoints CRUD, y los cambios se persisten automáticamente en data/data_store.json.
Las sesiones de clase se generan automáticamente a partir de materias + profesores.

En producción (IIS) solo la carpeta data/ necesita permisos de escritura;
los archivos Python pueden quedar protegidos como solo lectura.
"""

import json
import math
from pathlib import Path
from threading import Lock

from models import FranjaHoraria, Materia, Profesor, Salon, SesionClase

# Nombres predefinidos para salones auto-creados durante importación
_NOMBRES_AUTO = [
    "Aula 101", "Aula 102", "Aula 103", "Aula 104", "Aula 105",
    "Aula 201", "Aula 202", "Aula 203", "Aula 204", "Aula 205",
    "Lab. Informática 1", "Lab. Informática 2", "Lab. Informática 3",
    "Aula 301", "Aula 302", "Aula 303", "Aula 304",
    "Salón Múltiple 1", "Salón Múltiple 2", "Salón Múltiple 3",
]

# Ruta absoluta del archivo de datos.
# Se guarda en data/ (subcarpeta separada de los archivos Python)
# para que en IIS solo esa carpeta necesite permisos de escritura.
_DATA_DIR           = Path(__file__).parent / "data"
_DATA_DIR.mkdir(exist_ok=True)
STORE_FILE          = _DATA_DIR / "data_store.json"
ULTIMO_HORARIO_FILE = _DATA_DIR / "ultimo_horario.json"
_lock = Lock()


class DataStore:
    """Repositorio en memoria con persistencia JSON."""

    def __init__(self):
        self._materias:   list[Materia]        = []
        self._profesores: list[Profesor]        = []
        self._salones:    list[Salon]           = []
        self._franjas:    list[FranjaHoraria]   = []

        if STORE_FILE.exists():
            self._load()
        else:
            self._seed_defaults()
            self._save()

    # ------------------------------------------------------------------
    # Inicialización
    # ------------------------------------------------------------------

    def _seed_defaults(self) -> None:
        """Carga los datos de muestra definidos en data.py."""
        from data import FRANJAS, MATERIAS, PROFESORES, SALONES
        self._materias   = [_clone_materia(m)   for m in MATERIAS]
        self._profesores = [_clone_profesor(p)  for p in PROFESORES]
        self._salones    = [_clone_salon(s)      for s in SALONES]
        self._franjas    = [_clone_franja(f)     for f in FRANJAS]

    def reset_to_defaults(self) -> None:
        """Restaura los datos de muestra y guarda en disco."""
        with _lock:
            self._seed_defaults()
            self._save()

    def importar(self, datos: dict) -> dict:
        """
        Importa materias y profesores desde un dict con formato:
          {
            "accion":    "reemplazar" | "agregar"  (default "reemplazar")
            "materias":  [{"nombre","semestre","grupo","creditos","bloques"}, ...]
            "profesores":[{"nombre","materias":[["NombreMateria","Grupo"],...],
                           "franjas_preferidas":[...]}, ...]
            "salones":   opcional — igual que CRUD (si se omite, no se toca)
            "franjas":   opcional — igual que CRUD (si se omite, no se toca)
          }
        Retorna resumen de cuántos elementos se importaron.
        """
        accion = datos.get("accion", "reemplazar")
        with _lock:
            # ── Materias ──
            raw_mats = datos.get("materias", [])
            if accion == "reemplazar":
                base_id = 1
            else:
                base_id = max((m.id for m in self._materias), default=0) + 1

            nuevas: list[Materia] = []
            for i, m in enumerate(raw_mats):
                nuevas.append(Materia(
                    id                   = base_id + i,
                    nombre               = m["nombre"],
                    semestre             = int(m.get("semestre", 1)),
                    grupo                = str(m.get("grupo", "A")).upper(),
                    creditos             = int(m.get("creditos", 3)),
                    bloques              = [int(b) for b in m.get("bloques", [2, 2])],
                    num_alumnos          = int(m.get("num_alumnos", 30)),
                    requiere_laboratorio = bool(m.get("requiere_laboratorio", False)),
                ))

            # Índice (nombre, grupo) → id para vincular profesores
            idx: dict[tuple[str, str], int] = {
                (m.nombre, m.grupo): m.id for m in nuevas
            }

            # ── Profesores ──
            raw_profs = datos.get("profesores", [])
            if accion == "reemplazar":
                base_pid = 1
            else:
                base_pid = max((p.id for p in self._profesores), default=0) + 1

            nuevos_profs: list[Profesor] = []
            for j, p in enumerate(raw_profs):
                mids: list[int] = []
                for ref in p.get("materias", []):
                    if isinstance(ref, (list, tuple)) and len(ref) == 2:
                        key = (str(ref[0]), str(ref[1]).upper())
                    else:
                        continue
                    if key in idx:
                        mids.append(idx[key])
                nuevos_profs.append(Profesor(
                    id                 = base_pid + j,
                    nombre             = p["nombre"],
                    materias_ids       = mids,
                    franjas_preferidas = [int(x) for x in p.get("franjas_preferidas", [])],
                    franjas_bloqueadas = [int(x) for x in p.get("franjas_bloqueadas", [])],
                ))

            # ── Salones (opcional) ──
            raw_salones = datos.get("salones")
            nuevos_salones: list[Salon] | None = None
            if raw_salones is not None:
                base_sid = 1 if accion == "reemplazar" else max((s.id for s in self._salones), default=0) + 1
                nuevos_salones = [
                    Salon(base_sid + k, s["nombre"], int(s.get("capacidad", 30)),
                          tipo=str(s.get("tipo", "aula")))
                    for k, s in enumerate(raw_salones)
                ]

            # ── Franjas (opcional) ──
            raw_franjas = datos.get("franjas")
            nuevas_franjas: list[FranjaHoraria] | None = None
            if raw_franjas is not None:
                base_fid = 1 if accion == "reemplazar" else max((f.id for f in self._franjas), default=0) + 1
                nuevas_franjas = [
                    FranjaHoraria(base_fid + k, f["dia"], f["hora_inicio"], f["hora_fin"])
                    for k, f in enumerate(raw_franjas)
                ]

            # ── Aplicar ──
            if accion == "reemplazar":
                self._materias   = nuevas
                self._profesores = nuevos_profs
                if nuevos_salones is not None:
                    self._salones = nuevos_salones
                if nuevas_franjas is not None:
                    self._franjas = nuevas_franjas
            else:  # agregar
                self._materias.extend(nuevas)
                self._profesores.extend(nuevos_profs)
                if nuevos_salones is not None:
                    self._salones.extend(nuevos_salones)
                if nuevas_franjas is not None:
                    self._franjas.extend(nuevas_franjas)

            # ── Auto-crear salones si hacen falta ──────────────────────────
            # Fórmula: mínimo necesario = ceil(total_sesiones / franjas) + buffer
            # Solo se aplica cuando el JSON no incluye salones propios.
            salones_auto = 0
            if raw_salones is None:
                total_sesiones = sum(len(m.bloques) for m in self._materias)
                franjas_count  = len(self._franjas) if self._franjas else 25
                # +2 de margen para que el AG tenga holgura
                min_salones    = max(4, math.ceil(total_sesiones / franjas_count) + 2)
                next_id        = max((s.id for s in self._salones), default=0) + 1
                while len(self._salones) < min_salones:
                    salon_idx = len(self._salones)
                    nombre    = (_NOMBRES_AUTO[salon_idx]
                                 if salon_idx < len(_NOMBRES_AUTO)
                                 else f"Salón {next_id}")
                    self._salones.append(Salon(next_id, nombre, 35))
                    next_id  += 1
                    salones_auto += 1

            self._save()

        return {
            "materias_importadas":   len(nuevas),
            "profesores_importados": len(nuevos_profs),
            "salones_importados":    len(nuevos_salones) if nuevos_salones else 0,
            "salones_auto_creados":  salones_auto,
            "franjas_importadas":    len(nuevas_franjas) if nuevas_franjas else 0,
            "total_salones":         len(self._salones),
            "total_ranuras":         len(self._salones) * len(self._franjas),
        }

    # ------------------------------------------------------------------
    # Persistencia
    # ------------------------------------------------------------------

    def _load(self) -> None:
        try:
            with open(STORE_FILE, encoding="utf-8") as fh:
                raw = json.load(fh)
            self._materias   = [Materia(**d)        for d in raw.get("materias",   [])]
            self._profesores = [Profesor(**d)       for d in raw.get("profesores", [])]
            self._salones    = [Salon(**d)          for d in raw.get("salones",    [])]
            self._franjas    = [FranjaHoraria(**d)  for d in raw.get("franjas",    [])]
        except (json.JSONDecodeError, TypeError, KeyError) as exc:
            # Archivo corrupto o esquema incompatible → restaurar datos de muestra
            # para que el servidor arranque en lugar de crashear.
            import logging
            logging.warning(
                "data_store.json corrupto o incompatible (%s). "
                "Se restauran los datos de muestra.", exc
            )
            self._seed_defaults()

    def _save(self) -> None:
        payload = {
            "materias":   [_materia_to_dict(m)   for m in self._materias],
            "profesores": [_profesor_to_dict(p)  for p in self._profesores],
            "salones":    [_salon_to_dict(s)      for s in self._salones],
            "franjas":    [_franja_to_dict(f)     for f in self._franjas],
        }
        with open(STORE_FILE, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False, indent=2)

    # ------------------------------------------------------------------
    # Generación automática de sesiones
    # ------------------------------------------------------------------

    def get_sesiones(self, semestres: list[int] | None = None) -> list[SesionClase]:
        """
        Genera sesiones a partir de las materias y profesores actuales.
        Si `semestres` es una lista no vacía, solo incluye las materias de esos semestres.
        Si es None o lista vacía, devuelve todas las sesiones.
        Propaga num_alumnos y requiere_laboratorio desde cada Materia.
        """
        sesiones: list[SesionClase] = []
        sid = 1
        materias_filtradas = (
            self._materias if not semestres
            else [m for m in self._materias if m.semestre in semestres]
        )
        for mat in materias_filtradas:
            profesor = next(
                (p for p in self._profesores if mat.id in p.materias_ids),
                None,
            )
            if profesor is None:
                # Materia sin profesor asignado → se omite de la optimización.
                # El endpoint /api/datos expone esto a través del resumen; el
                # usuario debe asignar un profesor antes de optimizar.
                continue
            for duracion in mat.bloques:
                sesiones.append(SesionClase(
                    id=sid,
                    materia_id=mat.id,
                    profesor_id=profesor.id,
                    grupo=mat.grupo,
                    semestre=mat.semestre,
                    duracion_horas=duracion,
                    nombre_materia=mat.nombre,
                    nombre_profesor=profesor.nombre,
                    num_alumnos=mat.num_alumnos,
                    requiere_laboratorio=mat.requiere_laboratorio,
                ))
                sid += 1
        return sesiones

    # ------------------------------------------------------------------
    # Acceso a colecciones
    # ------------------------------------------------------------------

    @property
    def materias(self)   -> list[Materia]:       return list(self._materias)
    @property
    def profesores(self) -> list[Profesor]:      return list(self._profesores)
    @property
    def salones(self)    -> list[Salon]:         return list(self._salones)
    @property
    def franjas(self)    -> list[FranjaHoraria]: return list(self._franjas)

    # ------------------------------------------------------------------
    # CRUD — Materias
    # ------------------------------------------------------------------

    def get_materia(self, mid: int) -> Materia | None:
        return next((m for m in self._materias if m.id == mid), None)

    def add_materia(
        self, nombre: str, semestre: int, grupo: str, creditos: int,
        bloques: list[int], num_alumnos: int = 30,
        requiere_laboratorio: bool = False,
    ) -> Materia:
        with _lock:
            new_id = max((m.id for m in self._materias), default=0) + 1
            mat = Materia(
                new_id, nombre, semestre, grupo, creditos, bloques,
                num_alumnos=num_alumnos, requiere_laboratorio=requiere_laboratorio,
            )
            self._materias.append(mat)
            self._save()
        return mat

    def update_materia(self, mid: int, **fields) -> Materia | None:
        with _lock:
            mat = next((m for m in self._materias if m.id == mid), None)
            if mat is None:
                return None
            for k, v in fields.items():
                if hasattr(mat, k):
                    setattr(mat, k, v)
            self._save()
        return mat

    def delete_materia(self, mid: int) -> bool:
        with _lock:
            antes = len(self._materias)
            self._materias = [m for m in self._materias if m.id != mid]
            # Desvincula la materia de todos los profesores
            for p in self._profesores:
                if mid in p.materias_ids:
                    p.materias_ids.remove(mid)
            changed = len(self._materias) < antes
            if changed:
                self._save()
        return changed

    # ------------------------------------------------------------------
    # CRUD — Profesores
    # ------------------------------------------------------------------

    def get_profesor(self, pid: int) -> Profesor | None:
        return next((p for p in self._profesores if p.id == pid), None)

    def add_profesor(
        self, nombre: str, materias_ids: list[int],
        franjas_preferidas: list[int], franjas_bloqueadas: list[int] | None = None,
    ) -> Profesor:
        with _lock:
            new_id = max((p.id for p in self._profesores), default=0) + 1
            prof = Profesor(
                new_id, nombre, materias_ids, franjas_preferidas,
                franjas_bloqueadas=franjas_bloqueadas or [],
            )
            self._profesores.append(prof)
            self._save()
        return prof

    def update_profesor(self, pid: int, **fields) -> Profesor | None:
        with _lock:
            prof = next((p for p in self._profesores if p.id == pid), None)
            if prof is None:
                return None
            for k, v in fields.items():
                if hasattr(prof, k):
                    setattr(prof, k, v)
            self._save()
        return prof

    def delete_profesor(self, pid: int) -> bool:
        with _lock:
            antes = len(self._profesores)
            self._profesores = [p for p in self._profesores if p.id != pid]
            changed = len(self._profesores) < antes
            if changed:
                self._save()
        return changed

    # ------------------------------------------------------------------
    # CRUD — Salones
    # ------------------------------------------------------------------

    def get_salon(self, sid: int) -> Salon | None:
        return next((s for s in self._salones if s.id == sid), None)

    def add_salon(self, nombre: str, capacidad: int, tipo: str = "aula") -> Salon:
        with _lock:
            new_id = max((s.id for s in self._salones), default=0) + 1
            salon = Salon(new_id, nombre, capacidad, tipo=tipo)
            self._salones.append(salon)
            self._save()
        return salon

    def update_salon(self, sid: int, **fields) -> Salon | None:
        with _lock:
            salon = next((s for s in self._salones if s.id == sid), None)
            if salon is None:
                return None
            for k, v in fields.items():
                if hasattr(salon, k):
                    setattr(salon, k, v)
            self._save()
        return salon

    def delete_salon(self, sid: int) -> bool:
        with _lock:
            antes = len(self._salones)
            self._salones = [s for s in self._salones if s.id != sid]
            changed = len(self._salones) < antes
            if changed:
                self._save()
        return changed

    # ------------------------------------------------------------------
    # CRUD — Franjas horarias
    # ------------------------------------------------------------------

    def get_franja(self, fid: int) -> FranjaHoraria | None:
        return next((f for f in self._franjas if f.id == fid), None)

    def add_franja(self, dia: str, hora_inicio: str, hora_fin: str) -> FranjaHoraria:
        with _lock:
            new_id = max((f.id for f in self._franjas), default=0) + 1
            franja = FranjaHoraria(new_id, dia, hora_inicio, hora_fin)
            self._franjas.append(franja)
            self._save()
        return franja

    def update_franja(self, fid: int, **fields) -> FranjaHoraria | None:
        with _lock:
            franja = next((f for f in self._franjas if f.id == fid), None)
            if franja is None:
                return None
            for k, v in fields.items():
                if hasattr(franja, k):
                    setattr(franja, k, v)
            self._save()
        return franja

    def delete_franja(self, fid: int) -> bool:
        with _lock:
            antes = len(self._franjas)
            self._franjas = [f for f in self._franjas if f.id != fid]
            changed = len(self._franjas) < antes
            if changed:
                self._save()
        return changed

    # ------------------------------------------------------------------
    # Último horario optimizado
    # ------------------------------------------------------------------

    def save_ultimo_horario(self, data: dict) -> None:
        """Persiste el resultado de la última optimización exitosa."""
        with _lock:
            with open(ULTIMO_HORARIO_FILE, "w", encoding="utf-8") as fh:
                json.dump(data, fh, ensure_ascii=False, indent=2)

    def get_ultimo_horario(self) -> dict | None:
        """Retorna el último horario guardado, o None si no existe."""
        if not ULTIMO_HORARIO_FILE.exists():
            return None
        with open(ULTIMO_HORARIO_FILE, encoding="utf-8") as fh:
            return json.load(fh)

    # ------------------------------------------------------------------
    # Resumen / validación
    # ------------------------------------------------------------------

    def resumen(self) -> dict:
        sesiones = self.get_sesiones()
        semestres_disponibles = sorted(set(m.semestre for m in self._materias))
        return {
            "total_materias":         len(self._materias),
            "total_profesores":       len(self._profesores),
            "total_salones":          len(self._salones),
            "total_franjas":          len(self._franjas),
            "total_sesiones":         len(sesiones),
            "total_ranuras":          len(self._franjas) * len(self._salones),
            "semestres_disponibles":  semestres_disponibles,
            "listo_para_optimizar": (
                len(sesiones) > 0
                and len(self._salones) > 0
                and len(self._franjas) > 0
                and len(self._franjas) * len(self._salones) >= len(sesiones)
            ),
        }


# ------------------------------------------------------------------
# Helpers de serialización / clonación
# ------------------------------------------------------------------

def _materia_to_dict(m: Materia) -> dict:
    return {
        "id": m.id, "nombre": m.nombre, "semestre": m.semestre,
        "grupo": m.grupo, "creditos": m.creditos, "bloques": m.bloques,
        "num_alumnos": m.num_alumnos, "requiere_laboratorio": m.requiere_laboratorio,
    }

def _profesor_to_dict(p: Profesor) -> dict:
    return {
        "id": p.id, "nombre": p.nombre, "materias_ids": p.materias_ids,
        "franjas_preferidas": p.franjas_preferidas,
        "franjas_bloqueadas": p.franjas_bloqueadas,
    }

def _salon_to_dict(s: Salon) -> dict:
    return {"id": s.id, "nombre": s.nombre, "capacidad": s.capacidad, "tipo": s.tipo}

def _franja_to_dict(f: FranjaHoraria) -> dict:
    return {"id": f.id, "dia": f.dia, "hora_inicio": f.hora_inicio, "hora_fin": f.hora_fin}

def _clone_materia(m: Materia)       -> Materia:       return Materia(**_materia_to_dict(m))
def _clone_profesor(p: Profesor)     -> Profesor:      return Profesor(**_profesor_to_dict(p))
def _clone_salon(s: Salon)           -> Salon:         return Salon(**_salon_to_dict(s))
def _clone_franja(f: FranjaHoraria)  -> FranjaHoraria: return FranjaHoraria(**_franja_to_dict(f))


# Instancia global — un único DataStore compartido por toda la aplicación
store = DataStore()
