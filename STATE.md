# Rescate Workspace State

Fecha: 2026-07-17
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

Todo el trabajo descrito en `analisis-rediseno-flujo-albergues.md` y `prompt-desarrollo-flujo-albergues.md` está **implementado y mergeado a `develop`** en ambos repos:

- **Fase 0**: rol `acopio` retirado del dropdown, código huérfano de Firestore eliminado, bug de pérdida de datos al aprobar solicitud corregido, autenticación duplicada en `admin.py` unificada.
- **Fase 1**: modelo `CentroResponsable`/`CentroHistorial` creado; rename de `CentroAcopio`/`centros_acopio` (y tablas relacionadas) a `Centro`/`centros` — ejecutado como rename de código únicamente (sin `ALTER TABLE`, por decisión explícita: los datos viejos no eran reales). Las tablas físicas viejas (`centros_acopio`, `acopio_reportes`, `acopio_ordenes`, `acopio_entregas`, `centros_acopio_solicitudes`) quedan huérfanas en cualquier base que las tuviera; `seed_centros()` puebla la tabla nueva en el primer arranque.
- **Fase 2/3**: filtro por `tipo`, gestión de responsables por centro desde `AlberguesRefugios.jsx`.
- **Fase 4 (multiorganización)**: `organizacion_id` habilitado de punta a punta — en el modelo SQL, en `authorized_users`, y en las reglas de Firestore.
- **Endurecimiento adicional** (fuera del plan original, hecho en esta ronda de auditoría): regla cross-org para asignar responsables completada; `firestore.rules` corregidas (antes cualquier `admin` podía leer/escribir/borrar usuarios de **cualquier** organización y auto-promoverse a `super_admin` saltándose las Cloud Functions); firma HMAC de organización entre Functions y el backend (`ACTOR_SIGNING_SECRET`) para que el backend no dependa 100% de que Functions nunca tenga un bug — commit `2285388` (`ventatalk-rescate`) y `463ccdc` (`venezuela-rescate`), en rama `fase4/endurecimiento-backend` (ojo: el nombre de esta rama choca con el número de la Fase 4 real del rediseño — son cosas distintas, ver nota en `AGENTS.md`).

**Pendiente de decisión del usuario, no de código:**
- Mergear `fase4/endurecimiento-backend` a `develop` (commiteada, no mergeada ni pusheada a la fecha de este documento).
- Rollout a producción: requiere generar un `ACTOR_SIGNING_SECRET` propio de prod, configurarlo en el `.env` del servidor y vía `firebase functions:secrets:set`, y desplegar Functions **antes** que el backend (si se invierte el orden, los endpoints que exigen firma obligatoria fallan con 401 hasta que Functions se actualice).

**Hallazgo aceptado, no corregido (decisión explícita):** la regla cross-org de `CentroResponsable` solo se valida al momento de asignar. Si un usuario ya tenía asignaciones en varias organizaciones y **después** se le crea su registro en `authorized_users`, esas asignaciones previas no se revalidan retroactivamente.

## Hallazgos Técnicos Vigentes

### Backend `ventatalk-rescate`

- La app crea tablas y hace seed al iniciar (`Base.metadata.create_all` + `ensure_optional_columns` + `seed_*`), no hay Alembic ni migraciones formales.
- El código está baked en la imagen Docker (`COPY . .` en el build) — **no hay volumen montado para el código**, así que cambios locales requieren `docker compose up -d --build`, no solo `restart`.
- Auth admin del dashboard por cookie firmada (`AdminUser`, sin organización) sigue con `secure=False`; revisar antes de endurecer producción HTTPS.
- `routers/admin.py` y `routers/albergues.py` ya verifican organización/identidad del actor cuando la autenticación es por `x-api-key` (ver sección anterior); cuando es por sesión de cookie, sigue sin restricción por diseño (el dashboard es un admin global).
- No hay suite de tests automatizados.

### Frontend `venezuela-rescate`

- La estrategia de seguridad es buena: roles + `organizacion_id` en Firestore, operaciones sensibles vía Functions, y ahora Firestore rules que replican el scoping (antes no lo hacían).
- `AlberguesRefugios.jsx` filtra centros por organización en el cliente (UX, no es el límite de seguridad real — eso lo hacen Functions/backend); ese filtro fue corregido para fallar cerrado si no se puede confirmar la organización del operador.
- No hay suite de tests automatizados.
- `functions/.env.sos-ve-20fa3` suele tener un cambio local sin commit (toggle de `VENTATALK_API_BASE_URL` entre local y prod) — es intencional para desarrollo, no commitear.

## Estado De Ramas (al momento de escribir esto)

- **`develop`** (ambos repos): tiene todo el rediseño de albergues (Fases 0-4) ya mergeado.
- **`fase4/endurecimiento-backend`** (ambos repos, creada desde `develop`): tiene el endurecimiento con firma HMAC + los 2 fixes de UI de Fase 3. Commiteada, **no mergeada ni pusheada** todavía.
- `main` (ambos repos): sin el rediseño ni el endurecimiento — sigue siendo lo que hay en producción hoy.

## Verificaciones Ejecutadas En Esta Ronda

- `ventatalk-rescate`: `python3 -m compileall .` limpio.
- `venezuela-rescate`: `npm run build` y `npm run lint` limpios.
- Pruebas manuales reales contra Docker local + emuladores de Firebase (Auth/Firestore/Functions): casos de cruce de organización bloqueados (403/401), casos dentro de la misma organización permitidos (200), sesión de cookie del dashboard verificada como no afectada.

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

1. Decidir el rollout a producción del endurecimiento (`ACTOR_SIGNING_SECRET`, orden de despliegue Functions-antes-que-backend).
2. Revisar `secure=False` en la cookie de admin antes de producción HTTPS.
3. Definir una estrategia de migraciones formal para el backend (hoy es create_all + parches idempotentes a mano).
4. Agregar al menos pruebas mínimas para la lógica de scoping por organización — señalado como hallazgo aparte, no construido todavía.
5. El caso retroactivo de `CentroResponsable`/`authorized_users` (ver arriba) sigue sin resolver, por decisión explícita.

## Próximos Pasos Recomendados

1. Definir el alcance del próximo feature a trabajar (el rediseño de albergues ya está cerrado salvo el rollout).
2. Cuando se decida el rollout, seguir el checklist de la sección de Fase 4/endurecimiento.
3. Evaluar si conviene una suite mínima de tests para scoping antes de sumar más features sobre este modelo.

## Prompt De Continuidad

Si abres otro chat, puedes empezar con algo como:

```text
Estoy trabajando en /Users/juliocaicedo/Sites/rescate-workspace.
Lee primero AGENTS.md y STATE.md, y analiza ambos proyectos:
- ventatalk-rescate (backend FastAPI)
- venezuela-rescate (frontend React + Firebase)

Quiero continuar desde el estado actual sin repetir descubrimiento base.
```
