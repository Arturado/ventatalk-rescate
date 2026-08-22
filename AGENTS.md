# Reglas de trabajo — Ventatalk Rescate backend

## Alcance

Este repositorio contiene la API FastAPI/SQLAlchemy y PostgreSQL de Venezuela Rescate. El frontend React/Firebase vive en el repositorio hermano `../venezuela-rescate`.

Antes de editar, revisar `git status`, las instrucciones aplicables, los tests y los archivos relacionados. Leer `STATE.md` solo al continuar una feature existente, comparar ramas, preparar integración/despliegue o investigar una decisión histórica. Si documentación y código difieren, el código y las pruebas vigentes mandan; reportar la discrepancia.

## Git, producción y secretos

- Trabajar desde `develop` actualizado en una rama descriptiva. No cambiar de rama con cambios locales sin resolver.
- No hacer commit, push, PR, merge ni despliegue sin autorización explícita para esa acción.
- Un push a `main` activa el despliegue de producción mediante `.github/workflows/deploy.yml`.
- No leer ni modificar `.env`, `.env.*`, claves, secretos HMAC o credenciales sin anunciar la necesidad y obtener autorización.
- Probar primero contra el servicio local; nunca escribir en `https://rescate.ventatalk.com` sin autorización específica.

## Arquitectura y límites de seguridad

- `main.py` inicializa y monta la API; `routers/` separa endpoints; `models.py` y `schemas.py` definen persistencia y contratos; `dependencies.py` concentra API key y actor organizacional.
- No existe Alembic: los cambios de esquema requieren procedimiento idempotente, respaldo, prueba de arranque y plan de reversión.
- Las operaciones organizacionales validan actor firmado y `organizacion_id`. No confiar en identidad, rol u organización enviados libremente por el cliente.
- Antes de cambiar esquema, roles, aislamiento organizacional, `CentroResponsable`, autorización o firma HMAC, presentar el plan de seguridad/migración y esperar confirmación.
- Tratar cambios en `require_verified_actor_org`/`get_actor_org_scope` y en `signedActorOrgHeaders` del frontend como un único cambio de protocolo.
- No exponer identificaciones, teléfonos, direcciones, cuentas, comprobantes, consentimientos o documentos privados mediante endpoints públicos, logs o errores.

## Skills y especificaciones

- Usar `$verification-before-completion` antes de afirmar que una tarea está terminada o que una verificación pasó.
- Para features compartidas, seguir la spec versionada en `../venezuela-rescate/spec/features/` y mantener ramas y verificaciones separadas por repositorio.
- No usar el skill de diseño frontend ni el auditor de Firestore en este repositorio.

## Implementación y pruebas

- Preservar FastAPI, SQLAlchemy y las convenciones existentes. No añadir dependencias sin justificar mantenimiento, seguridad y licencia.
- Añadir o actualizar pruebas cuando cambie comportamiento. No imponer TDD a configuración, documentación o código heredado sin cobertura viable.
- Docker copia el código en la imagen; reconstruir después de cambiarlo.

Ejecutar según el alcance:

```bash
python3 -m compileall .
.venv/bin/python -m pytest
docker compose up -d --build
make health
```

Las pruebas PostgreSQL, Postman o manuales que necesiten servicios se ejecutan solo cuando correspondan y contra entorno local. Para autorización o scoping, incluir un actor permitido y otro denegado.

## Entrega

Resumir: qué se hizo; archivos modificados; pruebas con resultados; entorno usado; pendientes y riesgos. No afirmar pruebas que no se ejecutaron.
