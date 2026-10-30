# FitLife - Backend Django REST Framework

Sistema para el caso 2 de EVA 3: planes, membresías, pagos, control de acceso por QR, reservas con lista de espera y dashboard de aforo.

## 🌐 Enlace de Despliegue en AWS EC2
- **URL Pública:** `http://18.215.196.8/` 
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

Para una demostración rápida, `seed_demo` crea usuarios y datos idempotentes.

Si ya había una sesión abierta, pulsa **Cerrar sesión** en la barra superior. Luego puedes iniciar
sesión con el usuario que quieras; la raíz no muestra directamente el portal de ningún socio.

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
`python-dotenv`. Los valores de desarrollo son `DJANGO_DEBUG=True`,
`ALLOWED_HOSTS=127.0.0.1,localhost` y una clave secreta local de respaldo.
Cuando existe `POSTGRES_PASSWORD`, se usa PostgreSQL; cuando no existe, se
usa SQLite sin requerir configuración adicional.

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
- `POST /api/token/`: obtiene token DRF (`username`, `password`).
- `GET/POST/PATCH/DELETE /api/plans/`, `/memberships/`, `/payments/`, `/classes/`, `/bookings/`.
- `POST /api/access-logs/scan-qr/` con `{"qr_token":"..."}` valida membresía y registra entrada.
- `POST /api/bookings/{id}/cancel/` cancela y promueve automáticamente al primero de la lista de espera.
- `GET /api/entries/occupancy/` devuelve el aforo actual.
- `POST /api/entries/{id}/exit/` registra salida.

Todas las rutas, excepto registro, requieren autenticación por `Token` o sesión.

El dashboard usa sesión web. La API puede autenticarse con `POST /api/token/` y el encabezado `Authorization: Token <token>`.

## Guía de pruebas funcionales

Después de ejecutar `seed_demo`, inicia el servidor con `python manage.py runserver` y con el usuario administrador de demostración (las credenciales se entregan al docente por el AAI).

### 1. Planes, pagos y estados de membresía

- Abre **Planes** para comprobar que los planes activos se muestran con Bootstrap.
- En `/api/plans/`, `/api/memberships/` y `/api/payments/` prueba el CRUD con los botones
  `GET`, `POST`, `PATCH` y `DELETE` de la interfaz navegable de DRF.
- Para crear una membresía, usa un `member`, un `plan`, `start_date`, `end_date` y un
  `status` (`ACTIVE`, `EXPIRED` o `FROZEN`). Una membresía activa con fecha de término
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


## Despliegue en AWS EC2
- Instancia Ubuntu Server en AWS Academy, con IP elástica http://18.215.196.8/
- Arquitectura: Navegador → Nginx (puerto 80) → socket Unix → Gunicorn → Django/DRF → PostgreSQL (local en la instancia)
- Variables del .env en el servidor: DJANGO_DEBUG=False, DJANGO_SECRET_KEY, DJANGO_ALLOWED_HOSTS, POSTGRES_DB, POSTGRES_USER, POSTGRES_PASSWORD, POSTGRES_HOST, POSTGRES_PORT
- Pasos: clonar el repo, crear venv, pip install -r requirements.txt, configurar .env, migrate, seed_demo, collectstatic, servicio systemd para Gunicorn y sitio de Nginx
- Actualizar el servidor: git pull, pip install -r requirements.txt, migrate, collectstatic y sudo systemctl restart gunicorn
