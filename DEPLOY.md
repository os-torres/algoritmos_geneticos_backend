# Guía de Despliegue — HorarioGenetico AI Service (IIS + FastAPI)

> Arquitectura idéntica a **AppClasificacionGiecomAI** (Giecom).  
> El servicio corre como proceso hijo de IIS mediante **HttpPlatformHandler**,
> que inyecta el puerto dinámicamente (`%HTTP_PLATFORM_PORT%`) y hace proxy
> de todas las peticiones HTTP hacia uvicorn.

---

## Requisitos previos (servidor Windows)

| Componente | Versión mínima | Notas |
|---|---|---|
| Windows Server | 2016 / 2019 / 2022 | O Windows 10/11 con IIS habilitado |
| IIS | 10.0 | Rol "Servidor web" en Administrador del servidor |
| HttpPlatformHandler | 1.12 | [Descarga Microsoft](https://www.iis.net/downloads/microsoft/httpplatformhandler) |
| Python | 3.11+ | Instalado para **todos los usuarios** (`C:\Python311\`) |

---

## 1. Preparar IIS

### 1.1 Habilitar IIS (si no está instalado)

```powershell
# PowerShell como Administrador
Enable-WindowsOptionalFeature -Online -FeatureName IIS-WebServerRole,IIS-WebServer,IIS-CommonHttpFeatures,IIS-HttpErrors,IIS-HttpLogging,IIS-RequestFiltering,IIS-StaticContent,IIS-DefaultDocument -All
```

### 1.2 Instalar HttpPlatformHandler

Descarga e instala el MSI desde:  
`https://www.iis.net/downloads/microsoft/httpplatformhandler`

Verifica en IIS Manager → Sitio → Handler Mappings → busca **httpPlatformHandler**.

### 1.3 Crear el sitio o aplicación

**Opción A — Aplicación bajo el sitio Default Web Site:**

1. Abre IIS Manager
2. Expande `Default Web Site` → clic derecho → **Add Application**
3. Alias: `HorarioGenetico`
4. Physical path: `C:\inetpub\wwwroot\HorarioGenetico`
5. Application Pool: crea uno nuevo (ver 1.4)

**Opción B — Sitio dedicado:**

1. `Sites` → clic derecho → **Add Website**
2. Site name: `HorarioGenetico`
3. Physical path: `C:\inetpub\wwwroot\HorarioGenetico`
4. Port: el que elijas (e.g. 8001)

### 1.4 Configurar Application Pool

1. **Application Pools** → clic derecho en el pool del sitio → **Advanced Settings**
2. `.NET CLR Version` → **No Managed Code**
3. `Idle Time-out (minutes)` → **0** (evita que IIS duerma el proceso)
4. `Regular Time Interval (minutes)` → **0** (sin reciclado automático)

### 1.5 Permisos de la carpeta

```powershell
# Archivos Python — solo lectura/ejecución
icacls "C:\inetpub\wwwroot\HorarioGenetico" /grant "IIS AppPool\HorarioGenetico:(OI)(CI)RX" /T

# Carpeta de datos — escritura (data_store.json se guarda aquí)
icacls "C:\inetpub\wwwroot\HorarioGenetico\data" /grant "IIS AppPool\HorarioGenetico:(OI)(CI)M" /T

# Carpeta de logs — escritura
icacls "C:\inetpub\wwwroot\HorarioGenetico\logs" /grant "IIS AppPool\HorarioGenetico:(OI)(CI)M" /T
```

> El nombre del App Pool puede ser diferente; ajústalo al que creaste.
>
> **¿Por qué `data\` separado?** Así los archivos `.py` quedan protegidos
> contra escritura y solo la carpeta de datos es modificable por IIS.

---

## 2. Desplegar el proyecto

### 2.1 Copiar archivos al servidor

```powershell
# Desde tu máquina de desarrollo (ajusta rutas)
$src  = "C:\ruta\local\horario_genetico\backend"
$dest = "C:\inetpub\wwwroot\HorarioGenetico"

Copy-Item $src\* $dest -Recurse -Exclude ".git","__pycache__","*.pyc",".env.local","venv"
```

O usa tu herramienta preferida (SCP, Git, FTP, etc.).

> **Nota**: `data\data_store.json` se genera automáticamente la primera vez
> que el servidor arranca. No necesitas copiarlo manualmente.

### 2.2 Crear el entorno virtual

```cmd
cd C:\inetpub\wwwroot\HorarioGenetico
python -m venv venv
venv\Scripts\pip install --upgrade pip
venv\Scripts\pip install -r requirements.txt
```

### 2.3 Crear el archivo .env de producción

```cmd
copy .env.example .env
notepad .env
```

Edita los valores según tu entorno. Mínimo obligatorio:

```ini
ENVIRONMENT=production
AI_SERVICE_PORT=8000
ROOT_PATH=/HorarioGenetico
APP_NAME=HorarioGenetico
CORS_ORIGINS=https://tudominio.com.co
```

> Si el servicio corre como sitio dedicado (no subaplicación), deja `ROOT_PATH` vacío.

### 2.4 Crear los directorios de datos y logs

```powershell
New-Item -ItemType Directory -Force -Path "C:\inetpub\wwwroot\HorarioGenetico\data"
New-Item -ItemType Directory -Force -Path "C:\inetpub\wwwroot\HorarioGenetico\logs"
```

---

## 3. Verificar web.config

El archivo `web.config` ya está incluido en el repositorio y apunta a:

```
processPath="C:\inetpub\wwwroot\HorarioGenetico\venv\Scripts\uvicorn.exe"
```

Si desplegaste en otra ruta, edita `processPath` y el valor de `PYTHONPATH` antes de
copiar al servidor.

**Fragmento clave:**

```xml
<httpPlatform
    processPath="C:\inetpub\wwwroot\HorarioGenetico\venv\Scripts\uvicorn.exe"
    arguments="api:app --host 127.0.0.1 --port %HTTP_PLATFORM_PORT% --root-path /HorarioGenetico"
    stdoutLogEnabled="true"
    stdoutLogFile=".\logs\uvicorn"
    startupTimeLimit="120"
    requestTimeout="00:10:00">
  <environmentVariables>
    <environmentVariable name="ENVIRONMENT"  value="production" />
    <environmentVariable name="PYTHONPATH"   value="C:\inetpub\wwwroot\HorarioGenetico" />
  </environmentVariables>
</httpPlatform>
```

> **`%HTTP_PLATFORM_PORT%`** es inyectado automáticamente por IIS en cada inicio
> del proceso. Nunca lo reemplaces con un número fijo.

---

## 4. Reiniciar y verificar

### 4.1 Reciclar el Application Pool

```powershell
# PowerShell como Administrador
Import-Module WebAdministration
Restart-WebAppPool "HorarioGenetico"
```

O desde IIS Manager → Application Pools → clic derecho → **Recycle**.

### 4.2 Verificar que el proceso arrancó

```powershell
# Debería listar un proceso uvicorn corriendo
Get-Process uvicorn -ErrorAction SilentlyContinue
```

### 4.3 Revisar logs de uvicorn

```cmd
type C:\inetpub\wwwroot\HorarioGenetico\logs\uvicorn*.log
```

### 4.4 Probar el endpoint de salud

```powershell
# Desde el mismo servidor
Invoke-WebRequest http://localhost/HorarioGenetico/api/acerca-de
```

Respuesta esperada:

```json
{
  "proyecto": "Optimización de Horarios Universitarios con Algoritmos Genéticos",
  "version": "1.0.0",
  ...
}
```

---

## 5. Desarrollo local

Para desarrollo no necesitas IIS. Usa `.env.local` para sobreescribir valores:

```ini
# .env.local  (ignorado por git)
ENVIRONMENT=development
AI_SERVICE_PORT=8000
ROOT_PATH=
CORS_ORIGINS=
```

Luego ejecuta:

```bash
python main.py
# → Swagger en http://localhost:8000/docs
```

---

## 6. Troubleshooting

| Síntoma | Causa probable | Solución |
|---|---|---|
| HTTP 503 inmediato | uvicorn no arrancó | Revisar `logs\uvicorn*.log` |
| HTTP 500 en arranque | Error de importación Python | `venv\Scripts\python api.py` para ver el traceback |
| 404 en todas las rutas | `ROOT_PATH` no coincide | Verificar que `arguments` en web.config y `ROOT_PATH` en .env sean iguales |
| CORS bloqueado | `CORS_ORIGINS` incorrecto | Agregar la URL exacta del frontend a `CORS_ORIGINS` en .env |
| Proceso se cierra al idle | Idle timeout activo | App Pool → Advanced Settings → Idle Time-out → 0 |
| `ModuleNotFoundError` | `PYTHONPATH` no configurado | Verificar `<environmentVariable name="PYTHONPATH" ...>` en web.config |
| Logs vacíos | Permisos insuficientes | Ejecutar `icacls` del paso 1.5 |

---

## 7. Actualizar el servicio

```powershell
# 1. Parar el App Pool
Stop-WebAppPool "HorarioGenetico"

# 2. Copiar nuevos archivos
#    NO sobreescribas: venv\, .env, data\ (contiene los datos del usuario)
Copy-Item $src\* $dest -Recurse -Force `
    -Exclude ".git","__pycache__","*.pyc",".env",".env.local","venv","data"

# 3. Si cambiaron dependencias:
venv\Scripts\pip install -r requirements.txt

# 4. Reiniciar
Start-WebAppPool "HorarioGenetico"
```
