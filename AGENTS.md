# Reglas de trabajo — rescate-workspace

Este archivo aplica a todo el workspace: `ventatalk-rescate` (backend FastAPI + SQLAlchemy + Postgres, Docker) y `venezuela-rescate` (frontend React + Vite + Firebase Auth/Firestore/Functions). Léelo completo antes de tocar código, en cada sesión.

## Contexto obligatorio antes de empezar

Antes de proponer o hacer cualquier cambio, lee:

- `STATE.md` — snapshot del estado real del workspace (qué está mergeado, qué sigue en rama, hallazgos vigentes). Es la referencia principal para saber dónde estamos parados hoy, sin importar en qué feature se esté trabajando.
- El historial de git de ambos repos (`git log --oneline -20` en cada uno, en **ambas** ramas `develop` y `main` — no asumas que están sincronizadas) para saber qué se hizo ya, en qué ramas, y qué sigue sin mergear a cada una.
- `analisis-rediseno-flujo-albergues.md` y `prompt-desarrollo-flujo-albergues.md` — quedan como contexto histórico del rediseño del flujo de albergues (Fases 0-4: modelo `Centro`/`CentroResponsable`/`CentroHistorial`, scoping por `organizacion_id`, endurecimiento del backend). Ese trabajo ya está implementado y mergeado a `develop` en ambos repos — solo hace falta releerlos si la tarea puntual toca esa misma área; para features nuevas fuera de ese alcance, no son lectura obligatoria.

Si algo en el código contradice lo que dicen esos documentos, el código real manda — dilo explícitamente en vez de asumir que el documento tiene razón.

## Ramas: `feature/... → develop → main`

- `develop` es la rama de integración: ahí se juntan los cambios ya revisados para probarlos juntos antes de pasar a `main`.
- `main` es la que dispara el despliegue automático a producción (`.github/workflows/deploy.yml`, `on: push: branches: [main]`) — el momento de riesgo real está en el paso `develop → main`, no antes.
- Todas las ramas de trabajo se crean **desde `develop` actualizado**, no desde `main`. `main` solo se toca en el paso explícito de promover `develop → main`, y ese paso lo decide el responsable del proyecto, nunca la IA por su cuenta.

## Disciplina de ramas y commits (no negociable)

El criterio no es "una rama por tarea" a rajatabla — es **una rama por unidad de trabajo que es funcional y revisable de forma independiente**:

- **Si las tareas de una fase son independientes entre sí** (no dependen unas de otras para funcionar — es el caso de la Fase 0: quitar el rol del dropdown, borrar código huérfano, corregir el bug de aprobación, unificar autenticación duplicada), usa **una rama por tarea**, nombrada `fase{N}/{descripcion-corta}`. Mergea cada una apenas esté revisada, sin esperar a las demás.
- **Si las tareas de una fase están acopladas y no tienen sentido/no compilan por separado** (fue el caso de Fase 1, 3 y 4 del rediseño de albergues — `CentroResponsable` no servía sin el flujo de invitación que lo usa, y el scoping por `organizacion_id` no podía quedar a medias sin romper el aislamiento), usa **una rama por unidad de trabajo completa**, con **un commit por sub-tarea dentro de esa rama** — nunca un solo commit gigante. Así la rama es funcional de punta a punta, pero sigue siendo revisable y revertible commit por commit.
- Si no estás seguro de en cuál de los dos casos estás, pregúntame antes de decidir la granularidad, no asumas.
- **Nomenclatura de ramas**: `fase{N}` queda reservado exclusivamente a las fases numeradas en `analisis-rediseno-flujo-albergues.md` (el rediseño de albergues). Para cualquier feature nueva, usa un nombre descriptivo de la unidad de trabajo (ej. `feature/nombre-corto`), nunca reutilices `fase{N}` — ya pasó una vez que una rama de endurecimiento se llamó `fase4/...` y chocó en el nombre con la Fase 4 real del rediseño (multiorganización), que es otra cosa.
- **Nunca hagas merge a `develop` ni a `main` por tu cuenta**, sea rama-tarea o rama-fase. Deja la rama lista, muéstrame el diff (commit por commit si es una rama-fase), y yo decido cuándo mergearla y a cuál de las dos.
- **Nunca mergees más de una rama/fase de una sola vez.** Cada rama nueva se crea desde `develop` ya actualizado con el merge anterior, no en paralelo a una rama todavía sin mergear que toque los mismos archivos (evita conflictos entre fases que comparten, por ejemplo, `models.py`).
- Si una tarea o fase termina abarcando más de lo previsto (el alcance se amplía sobre la marcha sin que yo lo haya pedido), para y pregunta antes de seguir.
- Si en algún momento vas a implementar más de una fase del plan en la misma sesión sin que yo lo haya pedido explícitamente, para y confirma primero. El alcance de cada sesión lo defino yo al empezarla, no se infiere ni se amplía sobre la marcha.

## Entornos: `develop` es el staging informal — no hay uno separado aparte

- El proyecto tiene modo `local` y modo `prod`, controlados por variables de entorno. No hay un ambiente de staging desplegado aparte (ej. un servidor propio para `develop`), pero **`develop` cumple esa función a nivel de integración de código**: es donde se juntan y prueban los cambios antes de tocar `main`. Sigue sin haber un procedimiento de rollback documentado más allá de `git revert`.
- Todo cambio se prueba primero en modo `local`: backend en `http://localhost:8010` (Docker, Postgres en `5432`), frontend apuntando a `http://127.0.0.1:8010` con emuladores de Firebase si la tarea toca Firestore/Functions.
- **Nunca apuntes, despliegues, ni escribas contra `https://rescate.ventatalk.com` ni el proyecto de Firebase de producción** sin confirmación explícita mía, tarea por tarea.
- Antes de dar una tarea por probada, confirma explícitamente contra qué backend y qué proyecto de Firebase quedó apuntando el entorno — no lo asumas.
- El paso `develop → main` es, en la práctica, el despliegue a producción (dispara `deploy.yml` automáticamente). Trátalo con el mismo cuidado que cualquier despliegue: no lo propongas como un merge más, señálalo explícitamente como "esto va a producción" cuando lo sugieras.

## Archivos que nunca se tocan sin decirlo explícitamente

- Cualquier archivo de secretos/credenciales: `.env`, `.env.*`, `functions/.secret.local`, y en particular `functions/.env.sos-ve-20fa3`. Si una tarea requiere leerlos o modificarlos, dilo antes de hacerlo, no después, y muestra el diff exacto.
- Si al terminar una tarea `git status` muestra como modificado un archivo que no debería haber tocado esa tarea, repórtalo explícitamente en el resumen aunque no sepas por qué cambió — no lo omitas ni lo agregues al commit sin avisar.

## Zonas que requieren parada y confirmación explícita antes de implementar (no solo al terminar)

Estas áreas son las que, si tienen un bug, exponen datos o rompen el aislamiento entre usuarios/organizaciones. Antes de escribir código en estas zonas, describe el plan y espera confirmación:

- Cualquier cambio a `authorized_users`, roles, o lógica de scoping por `organizacion_id`.
- Cualquier cambio a las reglas de Firestore (`firestore.rules`) o a las funciones de autorización en `functions/index.js` / `src/services/authorization.js`.
- Cualquier cambio al mecanismo de asignación de responsables por centro (`CentroResponsable`) y sus reglas de negocio (por ejemplo, la regla de que un usuario con `organizacion_id` en `authorized_users` no puede asignarse a un centro de otra organización).
- Cualquier cambio al mecanismo de firma de organización entre Functions y el backend (`ACTOR_SIGNING_SECRET`, `signedActorOrgHeaders` en `functions/index.js`, `require_verified_actor_org`/`get_actor_org_scope` en `dependencies.py`) — es la defensa en profundidad que impide que alguien con el `x-api-key` compartido opere fuera de su organización sin pasar por Functions.
- Cualquier rename o cambio de esquema en la base de datos (tablas, columnas, foreign keys).
- **Instalar cualquier skill externo** (por ejemplo vía `npx skills add <repo>` de skills.sh, o `/plugin install` de un marketplace de Claude Code) que no sea ya parte del repo. Antes de instalarlo: confirmar que la fuente es confiable, leer el `SKILL.md` completo (y cualquier script que traiga) antes de habilitarlo, y fijar una versión/commit específico en vez de apuntar a la rama principal del repositorio del skill — para que una actualización futura del skill no cambie el comportamiento del proyecto sin que se decida explícitamente. La instalación del skill va en su propia rama/commit, separada de cualquier trabajo que lo use.

## Pruebas — no hay suite automatizada todavía

No existe una malla de tests automatizados en ninguno de los dos repos. Mientras eso no cambie:

- Después de cada tarea, corre y reporta el resultado de: `python3 -m compileall .` en el backend; `npm run build` y `npm run lint` en el frontend.
- Además de esos comandos, describe qué probaste manualmente y cómo (qué pantalla, qué endpoint, con qué usuario/rol) — "compiló y no dio error" no es lo mismo que "lo probé y funciona".
- Para cambios en scoping por organización o roles, la prueba manual debe incluir al menos dos casos: un usuario que SÍ debería tener acceso y uno que NO debería tenerlo, mostrando explícitamente que el segundo caso fue bloqueado.
- Si detectas que hace falta una suite de tests automatizados para un área específica (por ejemplo, la lógica de scoping), dilo como hallazgo aparte — no la construyas sin que se acuerde explícitamente, porque es un esfuerzo aparte del que se está pidiendo.

## Formato de resumen al terminar cualquier tarea o sesión

No entregues informes largos. Al final de cada tarea o sesión, resume en este formato:

1. **Qué se hizo** — lista corta, por tarea.
2. **Archivos modificados** — por archivo, y en qué rama/commit quedó cada cosa.
3. **Qué se probó y cómo** — comandos corridos + pruebas manuales concretas (no solo "se validó").
4. **Qué quedó pendiente de confirmación** — cualquier duda, ambigüedad, o desvío del plan original.
5. **Hallazgos nuevos** — cualquier cosa que contradiga o no estuviera contemplada en `analisis-rediseno-flujo-albergues.md` o en `STATE.md`.

No reportes fases como "completadas y validadas" si la validación fue solo build/lint sin pruebas manuales concretas de los casos sensibles (scoping, roles, permisos).
