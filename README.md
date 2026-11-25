# FitLife - Backend Django REST Framework

Sistema para el caso 2 de EVA 3: planes, membresías, pagos, control de acceso por QR, reservas con lista de espera y dashboard de aforo.

## 🌐 Enlace de Despliegue en AWS EC2
- **URL Pública:** `http://TU_IP_PUBLICA_DE_AWS/` (Ejemplo: `http://18.215.196.8/`)

## Requisitos

- Python 3.12+.
- Dependencias de `requirements.txt`.
- PostgreSQL 14+ es opcional para desarrollo. Si `POSTGRES_PASSWORD` no
  existe o está vacío, FitLife usa automáticamente `db.sqlite3`.

## Ejecución local

```powershell
# Desde la carpeta raíz del repositorio
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item .env.example .env
python manage.py migrate
python manage.py seed_demo
python manage.py test core
python manage.py runserver
```

El archivo `.env.example` está listo para SQLite: `POSTGRES_PASSWORD` queda
vacío y no es necesario instalar PostgreSQL. Para usar PostgreSQL, edita
solamente `.env` y completa `POSTGRES_PASSWORD`; los valores por defecto son
la base `fitlife`, usuario `postgres`, host `localhost` y puerto `5432`.
Reemplaza también los valores de ejemplo de `DJANGO_SECRET_KEY` y
`JWT_SIGNING_KEY` por claves aleatorias distintas antes de desplegar.
Nunca subas `.env` al repositorio.

Abrir `http://127.0.0.1:8000/` para la página de inicio Bootstrap. La raíz no inicia ninguna
cuenta automáticamente: muestra los accesos disponibles y permite elegir login, Admin o API.
Después del login, el sistema envía automáticamente a staff al dashboard administrativo y a los socios a `/mi-cuenta/`.
También existen los accesos equivalentes `/dashboard/`, `/admin-panel/`, `/socio-portal/` y
`/simulador-qr/`, todos renderizados como HTML.
El panel oficial personalizado está en `http://127.0.0.1:8000/admin/` y la API DRF está
separada en `http://127.0.0.1:8000/api/`.

Desde el Admin se pueden abrir directamente **Pagos** y **Membresías** desde
la portada, la barra superior y el dashboard administrativo. Solo las cuentas
`is_staff` tienen esos accesos.

El RUT se valida con dígito verificador en el formulario web, Admin y API.
Los duplicados muestran `Ya existe un socio con este RUT`.

Para una demostración rápida, `seed_demo` crea usuarios y datos idempotentes:

- Administrador: `fitlife_admin` / `FitLife.Admin.2026!`
- Socio demo: `socio_demo` / `FitLife.Socio.2026!` (nombre visible: Socio Demo)

Si ya había una sesión abierta, pulsa **Cerrar sesión** en la barra superior. Luego puedes iniciar
sesión con el usuario que quieras; la raíz no muestra directamente el portal de ningún socio.

Después de la demostración, cambia o elimina estas credenciales.

### Probar páginas de error amigables

Con `DEBUG=True`, Django muestra la pantalla amarilla de depuración para
excepciones no controladas, porque está diseñada para desarrollo y necesita
mostrar el traceback. Para comprobar las páginas amigables usa:

```powershell
$env:DJANGO_DEBUG="False"
python manage.py runserver --insecure
```

Luego abre una ruta inexistente para comprobar `404` y una ruta protegida sin
permisos para comprobar `403`. Las plantillas son `400.html`, `403.html`,
`404.html` y `500.html`, todas basadas en `base.html`.

### Configuración automática del entorno

`settings.py` carga `.env` desde la raíz del proyecto mediante
`python-dotenv`. `DJANGO_SECRET_KEY` es obligatoria con `DEBUG=False`;
`JWT_SIGNING_KEY` siempre es obligatoria y debe ser distinta. `DJANGO_DEBUG`,
`DJANGO_ALLOWED_HOSTS` y `DJANGO_CSRF_TRUSTED_ORIGINS` se configuran desde
ese archivo. Cuando existe `POSTGRES_PASSWORD`, se usa PostgreSQL con
`POSTGRES_SSLMODE` (por defecto `prefer`) y, opcionalmente,
`POSTGRES_SSLROOTCERT`; cuando no existe, se usa SQLite. `USE_S3=False`
mantiene archivos localmente. En AWS, `USE_S3=True` usa el bucket configurado
y el rol IAM de la instancia, sin guardar credenciales AWS en el proyecto.

`.env` y `db.sqlite3` están excluidos por `.gitignore`. Si `.env` se hubiera
subido anteriormente a Git, dejar de rastrearlo no borra el secreto del
historial:

```powershell
git rm --cached .env
git commit -m "Stop tracking local environment file"
```

Para eliminar credenciales que ya quedaron en commits antiguos, hay que
reescribir el historial con una herramienta como `git filter-repo` y rotar
inmediatamente esas credenciales.

El alta administrativa de socios permite registrar RUT, credenciales y asignar un plan en el
mismo formulario. La pantalla **Acceso QR** acepta el RUT o el token QR y nunca expone JSON:
el resultado se muestra como tarjeta Bootstrap verde o alerta roja.

Los pagos y cambios de estado de membresías se gestionan exclusivamente desde
`/admin/` o mediante los métodos de escritura de la API usando una cuenta
administradora. El socio solo puede consultar sus propios pagos y membresías;
una membresía congelada o vencida bloquea reservas y acceso QR.

La interfaz navegable de DRF conserva sus endpoints técnicos bajo `/api/`, pero usa una plantilla
personalizada con Bootstrap 5 cuando se solicita HTML; las respuestas JSON quedan reservadas para
clientes API que soliciten explícitamente ese formato.

## API principal

- `POST /api/auth/register/`: registra un socio y genera su token QR.
- `POST /api/token/`: inicia sesión con `username` y `password`; devuelve `access` y `refresh`.
- `POST /api/token/refresh/`: renueva el access token y rota el refresh token.
- `POST /api/token/blacklist/`: revoca el refresh token al cerrar sesión.
- `GET /api/health/`: endpoint público para health checks.
- `GET /api/schema/` y `GET /api/docs/`: esquema OpenAPI y Swagger UI.
- Los viewsets exponen `GET`, `POST`, `PUT`, `PATCH` y `DELETE`; las escrituras
  de planes, membresías y pagos son exclusivas de staff. Los socios solo ven
  sus propios perfiles, reservas, membresías y pagos según sus permisos.
- `POST /api/access-logs/scan-qr/` con `{"qr_token":"..."}` valida membresía y registra entrada.
- `POST /api/bookings/{id}/cancel/` cancela y promueve automáticamente al primero de la lista de espera.
- `GET /api/entries/occupancy/` devuelve el aforo actual.
- `POST /api/entries/{id}/exit/` registra salida.

Las vistas de la API requieren JWT, salvo registro, tokens, health check y
documentación. Envía el access token en `Authorization: Bearer <access>`.
El dashboard y el Admin de Django mantienen su autenticación web por sesión.

## Guía de pruebas funcionales

Después de ejecutar `seed_demo`, inicia el servidor con `python manage.py runserver` y entra a
`http://127.0.0.1:8000/` con `fitlife_admin` / `FitLife.Admin.2026!`.

### 1. Planes, pagos y estados de membresía

- Abre **Planes** para comprobar que los planes activos se muestran con Bootstrap.
- En `/api/plans/`, `/api/memberships/` y `/api/payments/` prueba el CRUD con los botones
  `GET`, `POST`, `PATCH` y `DELETE` de la interfaz navegable de DRF.
- Para crear una membresía, usa un `member`, un `plan`, `start_date`, `end_date` y un
  `estado` (`ACTIVE`, `EXPIRED` o `FROZEN`). Una membresía activa con fecha de término
  pasada se guarda automáticamente como vencida.
- Comprueba el resultado en **Membresías** y verifica las etiquetas **Activa**, **Vencida**
  y **Congelada**.

### 2. Validación de acceso mediante QR

- Entra a **Acceso QR** y copia el `qr_token` del socio demo desde `/api/members/`.
- Ingresa el token y pulsa **Validar código**: debe autorizar el acceso y aumentar el aforo.
- Repite con un valor inventado: debe rechazarlo y registrar el motivo.
- Consulta `/api/access-logs/` para verificar ambos registros y
  `/api/entries/` para comprobar la entrada creada.

### 3. Reservas, cupos y lista de espera

- Abre **Clases grupales** y reserva una clase con cupo disponible: queda **Confirmada**.
- Reserva la misma clase con otro socio cuando se haya agotado el cupo: queda en **Lista de
  espera**.
- Cancela la reserva confirmada desde **Reservas**: el primer socio en espera debe pasar
  automáticamente a **Confirmada**.
- Verifica los estados en `/api/bookings/` o en la pantalla **Reservas**.

### 4. Dashboard y aforo

- En **Dashboard de aforo**, revisa **Personas dentro**, **Membresías activas** y
  **Próximas clases**.
- Pulsa **Actualizar aforo**: ahora recarga el dashboard Bootstrap, no la respuesta JSON de
  la API.
- Valida el dato directamente en `/api/entries/occupancy/`; debe coincidir con
  `current_occupancy`.
- Registra una salida con `POST /api/entries/{id}/exit/` y vuelve a actualizar el dashboard:
  el aforo debe disminuir.

## AWS EC2

En EC2 se usa el mismo proyecto y PostgreSQL local de la instancia. Configura
las variables de `.env`, habilita `USE_S3=True` y el rol IAM de la instancia,
ejecuta `migrate` y `collectstatic`, y sirve con Gunicorn detrás de Nginx.
No habilites cookies Secure ni redirección SSL en el laboratorio HTTP; activa
esas opciones solo al desplegar detrás de HTTPS. Nunca se debe subir `.env`.
