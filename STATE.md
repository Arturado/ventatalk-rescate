# Rescate Workspace State

Fecha: 2026-08-06
Workspace: `/Users/juliocaicedo/Sites/rescate-workspace`

## Estructura

```text
rescate-workspace/
  STATE.md
  AGENTS.md
  analisis-rediseno-flujo-albergues.md
  prompt-desarrollo-flujo-albergues.md
  ventatalk-rescate/
  venezuela-rescate/
```

## Rol De Cada Proyecto

### `ventatalk-rescate`

- Backend principal.
- Stack: FastAPI, SQLAlchemy, Postgres, Jinja2, Docker Compose.
- Expone API y vistas server-rendered (panel admin propio, login por cookie firmada, sin concepto de organización — es un admin global tipo `AdminUser`).
- Maneja personas reportadas, pacientes hospitalizados, albergues/refugios (`Centro`, `CentroSolicitud`, `CentroReporte`, `CentroOrden`, `CentroEntrega`, `CentroResponsable`, `CentroHistorial`), bomberos, casos de ayuda y admin.
- Usa uploads locales en disco.

Puntos de entrada clave:

- `main.py` (lifespan: create_all + seed + `ensure_optional_columns`)
- `routers/admin.py`, `routers/albergues.py`
- `models.py`, `schemas.py`
- `dependencies.py` (auth por `x-api-key` + verificación de firma de organización)
- `services/shelter_centers.py`
- `docker-compose.yml`

### `venezuela-rescate`

- Frontend público y operativo.
- Stack: React, Vite, Firebase Auth, Firestore, Firebase Functions.
- Usa Firebase Functions para encapsular operaciones sensibles hacia `ventatalk-rescate`.
- Mantiene roles y organización (`organizacion_id`) en Firestore (`authorized_users`).

Puntos de entrada clave:

- `src/App.jsx`, `src/pages/`, `src/services/`
- `src/hooks/useAuthorization.js`, `src/hooks/useShelterOperatorAccess.js`
- `src/components/authority/AccessManager.jsx`
- `src/firebase/config.js`
- `functions/index.js`
- `firestore.rules`

## Relación Entre Ambos

- `venezuela-rescate` consume a `ventatalk-rescate`.
- Lecturas públicas y algunas acciones públicas siguen yendo directo al backend Ventatalk.
- Operaciones sensibles se encapsulan en Firebase Functions, que además firman con HMAC (`ACTOR_SIGNING_SECRET`) la organización/rol/email real del actor hacia el backend — el backend ya no confía ciegamente en lo que venga en el form/query para los endpoints sensibles a organización.
- La integración principal es por URL/API, no por rutas locales compartidas.

## Rediseño Del Flujo De Albergues — Estado (Fases 0-4 + endurecimiento)

Todo el trabajo descrito en `analisis-rediseno-flujo-albergues.md` y `prompt-desarrollo-flujo-albergues.md` está **implementado, mergeado a `develop` y `main`, y en producción** (backend `ventatalk-rescate`; para `venezuela-rescate` asumir el mismo estado salvo que se verifique lo contrario en ese repo):

- **Fase 0**: rol `acopio` retirado del dropdown, código huérfano de Firestore eliminado, bug de pérdida de datos al aprobar solicitud corregido, autenticación duplicada en `admin.py` unificada.
- **Fase 1**: modelo `CentroResponsable`/`CentroHistorial` creado; rename de `CentroAcopio`/`centros_acopio` (y tablas relacionadas) a `Centro`/`centros` — ejecutado como rename de código únicamente (sin `ALTER TABLE`, por decisión explícita: los datos viejos no eran reales). Ver estado real de las tablas físicas en "Hallazgos Técnicos Vigentes" más abajo — ya no es hipotético, se confirmó contra producción.
- **Fase 2/3**: filtro por `tipo`, gestión de responsables por centro desde `AlberguesRefugios.jsx`.
- **Fase 4 (multiorganización)**: `organizacion_id` habilitado de punta a punta — en el modelo SQL, en `authorized_users`, y en las reglas de Firestore.
- **Endurecimiento adicional** (fuera del plan original): regla cross-org para asignar responsables completada; `firestore.rules` corregidas (antes cualquier `admin` podía leer/escribir/borrar usuarios de **cualquier** organización y auto-promoverse a `super_admin` saltándose las Cloud Functions); firma HMAC de organización entre Functions y el backend (`ACTOR_SIGNING_SECRET`) para que el backend no dependa 100% de que Functions nunca tenga un bug — commit `2285388` (`ventatalk-rescate`) y `463ccdc` (`venezuela-rescate`), desarrollado en rama `fase4/endurecimiento-backend` (ojo: el nombre de esta rama choca con el número de la Fase 4 real del rediseño — son cosas distintas, ver nota en `AGENTS.md`).

**Rollout a producción — YA EJECUTADO:** `fase4/endurecimiento-backend` está mergeada a `develop` y a `main` (ver "Estado De Ramas"). `ACTOR_SIGNING_SECRET` está configurado en el `.env` del VPS de producción. No quedó pendiente de decisión — lo que este documento marcaba como riesgo futuro ya ocurrió.

**Hallazgo aceptado, no corregido (decisión explícita):** la regla cross-org de `CentroResponsable` solo se valida al momento de asignar. Si un usuario ya tenía asignaciones en varias organizaciones y **después** se le crea su registro en `authorized_users`, esas asignaciones previas no se revalidan retroactivamente.

## Módulo "Yo Te Ayudo" (Help Cases V2) — Estado

Módulo nuevo, en paralelo al `CasoAyuda` v1 (que sigue existiendo sin tocar). Desarrollado en rama `feature/yo-te-ayudo`, **mergeado a `develop` y `main`, y en producción** (PR #6, más los ajustes posteriores en PR #7-#10).

- Routers: `routers/casos_ayuda_v2.py` (organización), `routers/casos_ayuda_donantes.py` (donante), `routers/casos_ayuda_publicos.py` (público, sin auth), `routers/casos_ayuda_accesos.py` (acceso temporal por email sin cuenta).
- Servicios: `services/help_cases.py`, `services/help_donors.py`, `services/help_confirmations.py`, más `help_crypto`, `help_exchange`, `help_notifications`, `help_receipts`, `help_retention`, `help_temporary_access`, `help_feature_flags`, `help_idempotency`, `help_files`, `help_backup`.
- Ciclo de vida del caso: `borrador → pendiente_validacion → listo_publicar → publicado ↔ pausado → meta_alcanzada → cerrado`, con `rechazado`/`suspendido`/`archivado` como ramas laterales. Antes de publicar corre una validación de "readiness" (identidad verificada, consentimiento vigente, cuenta aprobada, documentos públicos aprobados).
- Datos sensibles (nombre y cédula del beneficiario, cuentas, comprobantes) van cifrados con `HelpDataCipher`; hay auditoría inmutable (`AuditoriaCasoAyuda`) y operaciones de donante/organización protegidas con `Idempotency-Key`.
- **Feature flag activo en producción**: `HELP_CASES_V2_ENABLED=true`, con **dos organizaciones piloto** habilitadas vía `HELP_CASES_V2_PILOT_ORGANIZATION_IDS`. Cualquier organización fuera de esa lista no ve el módulo aunque el código esté desplegado.
- Tiene su propia suite de tests (ver "Pruebas" en `AGENTS.md` y el detalle de archivos abajo).

## Hallazgos Técnicos Vigentes

### Backend `ventatalk-rescate`

- La app crea tablas y hace seed al iniciar (`Base.metadata.create_all` + `ensure_optional_columns` + `seed_*`), no hay Alembic ni migraciones formales.
- El código está baked en la imagen Docker (`COPY . .` en el build) — **no hay volumen montado para el código**, así que cambios locales requieren `docker compose up -d --build`, no solo `restart`.
- Auth admin del dashboard por cookie firmada (`AdminUser`, sin organización) sigue con `secure=False`; revisar antes de endurecer producción HTTPS.
- `routers/admin.py` y `routers/albergues.py` ya verifican organización/identidad del actor cuando la autenticación es por `x-api-key` (ver sección anterior); cuando es por sesión de cookie, sigue sin restricción por diseño (el dashboard es un admin global).
- **Sí hay suite de tests automatizados**: 33 archivos en `tests/`, cubriendo principalmente el módulo "Yo te ayudo" (`test_help_v2_*`, `test_help_confirmations.py`, `test_help_donor_*`, `test_help_temporary_access*`, `test_help_exchange*`, `test_help_backup*`, `test_help_retention*`, variantes SQLite y Postgres para varios). Esto reemplaza el hallazgo anterior de "no hay tests" — sigue sin haber cobertura equivalente para el scoping por organización en albergues (`Centro`/`CentroResponsable`), eso continúa pendiente.
- **Estado real de las tablas de albergues en producción (confirmado, ya no es hipotético)**: la tabla vieja `centros_acopio` quedó huérfana con 34 registros de prueba — decisión explícita: dejarla ahí, no migrar esos datos. La tabla nueva `centros` tiene 20 registros reales y está funcionando en producción.
- **Backup**: `pg_dump` a PostgreSQL cada 3 horas.

### Frontend `venezuela-rescate`

- La estrategia de seguridad es buena: roles + `organizacion_id` en Firestore, operaciones sensibles vía Functions, y ahora Firestore rules que replican el scoping (antes no lo hacían).
- `AlberguesRefugios.jsx` filtra centros por organización en el cliente (UX, no es el límite de seguridad real — eso lo hacen Functions/backend); ese filtro fue corregido para fallar cerrado si no se puede confirmar la organización del operador.
- No hay suite de tests automatizados.
- `functions/.env.sos-ve-20fa3` suele tener un cambio local sin commit (toggle de `VENTATALK_API_BASE_URL` entre local y prod) — es intencional para desarrollo, no commitear.

## Estado De Ramas (al momento de escribir esto)

- **`ventatalk-rescate`**: `develop` y `main` están **sincronizadas** — `git log origin/main..origin/develop` no devuelve nada, `develop` no tiene ya nada que `main` no tenga. `main` ya incluye, mergeados vía PR: el rediseño de albergues completo (Fases 0-4), el endurecimiento HMAC (`fase4/endurecimiento-backend`, PR #5), y el módulo "Yo te ayudo" (`feature/yo-te-ayudo`, PR #6), además de PR #7-#10 (fixes de CORS, exclusión de `.env` del build Docker, worker de notificación de versión). Todo esto **ya está en producción**.
- **`venezuela-rescate`**: no verificado en esta ronda (ese repo no está presente en este entorno) — asumir el mismo estado de sincronización solo después de confirmarlo explícitamente ahí, no por analogía.
- No queda ninguna rama de trabajo pendiente de mergear identificada a la fecha de este documento. Cualquier rama nueva se crea desde `develop`/`main` actualizado (son equivalentes ahora mismo).

## Verificaciones Ejecutadas En Esta Ronda

### Ronda 2026-07-17 (rediseño de albergues, previa a este documento)

- `ventatalk-rescate`: `python3 -m compileall .` limpio.
- `venezuela-rescate`: `npm run build` y `npm run lint` limpios.
- Pruebas manuales reales contra Docker local + emuladores de Firebase (Auth/Firestore/Functions): casos de cruce de organización bloqueados (403/401), casos dentro de la misma organización permitidos (200), sesión de cookie del dashboard verificada como no afectada.

### Ronda 2026-08-06 (actualización de este documento)

- Verificación de estado de ramas contra `origin` (`git log`, `git merge-base --is-ancestor`) para confirmar sincronización `develop`/`main` y qué ramas de feature quedaron mergeadas — no fue una prueba funcional, fue lectura de historial de git.
- Lectura completa de `routers/casos_ayuda_v2.py`, `services/help_cases.py`, `services/help_donors.py`, `services/help_confirmations.py` y las secciones relevantes de `models.py` para documentar el módulo "Yo te ayudo" — análisis de código, no ejecución.
- Los datos de estado de producción (`ACTOR_SIGNING_SECRET` configurado, tablas `centros`/`centros_acopio`, cadencia de backup `pg_dump`, `HELP_CASES_V2_ENABLED` y organizaciones piloto) fueron **reportados directamente por el usuario**, no verificados de forma independiente contra el VPS ni la base de datos de producción en esta sesión.

## Comandos Útiles

### Backend

```bash
cd /Users/juliocaicedo/Sites/rescate-workspace/ventatalk-rescate
docker compose up -d --build   # necesario tras cualquier cambio de código (no hay volumen montado)
docker compose logs -f rescate-api
python3 -m compileall .
```

### Frontend

```bash
cd /Users/juliocaicedo/Sites/rescate-workspace/venezuela-rescate
npm run dev
npm run build
npm run lint
firebase emulators:start --only auth,firestore,functions --import=.firebase/emulator-data
```

## Riesgos O Pendientes

1. ~~Decidir el rollout a producción del endurecimiento~~ — **resuelto**: ya está en producción, `ACTOR_SIGNING_SECRET` configurado en el VPS.
2. Revisar `secure=False` en la cookie de admin antes de endurecer producción HTTPS — sigue pendiente.
3. Definir una estrategia de migraciones formal para el backend (hoy es create_all + parches idempotentes a mano) — sigue pendiente; relevante para la próxima vez que se necesite un rename o cambio de esquema real (ver el caso `centros_acopio`/`centros` huérfano como ejemplo de lo que pasa sin esto).
4. La suite de tests (33 archivos) cubre mayormente "Yo te ayudo" — **sigue faltando cobertura automatizada para el scoping por organización de albergues** (`Centro`/`CentroResponsable`), que es donde `AGENTS.md` pide explícitamente al menos dos casos (acceso permitido/bloqueado) probados manualmente cada vez.
5. El caso retroactivo de `CentroResponsable`/`authorized_users` (ver arriba) sigue sin resolver, por decisión explícita.
6. La tabla huérfana `centros_acopio` (34 registros de prueba) queda como decisión aceptada de no migrar — documentarlo acá para que no se reabra como "bug" sin contexto.

## Próximos Pasos Recomendados

1. Definir el alcance del próximo feature a trabajar (el rediseño de albergues y el rollout del endurecimiento ya están cerrados; "Yo te ayudo" está en producción para dos organizaciones piloto).
2. Si se van a sumar más organizaciones piloto a `HELP_CASES_V2_PILOT_ORGANIZATION_IDS`, validar antes que esas organizaciones tengan la configuración de Firestore/`authorized_users` correcta — no fue parte de esta ronda de revisión.
3. Evaluar si conviene una suite mínima de tests para el scoping por organización de albergues, ya que "Yo te ayudo" sí quedó con cobertura y ese modelo no.
4. Confirmar el mismo estado de ramas y despliegue en `venezuela-rescate` — esta ronda solo verificó `ventatalk-rescate`, que es el único repo presente en este entorno de trabajo.

## Prompt De Continuidad

Si abres otro chat, puedes empezar con algo como:

```text
Estoy trabajando en /Users/juliocaicedo/Sites/rescate-workspace.
Lee primero AGENTS.md y STATE.md, y analiza ambos proyectos:
- ventatalk-rescate (backend FastAPI)
- venezuela-rescate (frontend React + Firebase)

Quiero continuar desde el estado actual sin repetir descubrimiento base.
```
