# ADR-002: Outbox de notificaciones de la feature 001

## Decision

Las notificaciones de la feature 001 se registran en la misma transaccion que el cambio de dominio. El outbox solo conserva el hash ciego y el cifrado del correo, junto con un payload cifrado validado contra plantillas cerradas y versionadas.

Resend se configura en tiempo de ejecucion con `RESEND_API_KEY` y `RESEND_FROM_EMAIL`. Los enlaces de acceso temporal usan `HELP_FRONTEND_BASE_URL`.

## Ejecucion operativa

La outbox se procesa fuera del ciclo HTTP mediante `python -m scripts.help_notifications --limit 25`. En el VPS de produccion, un timer systemd ejecuta cada minuto un servicio `oneshot` que invoca el comando dentro del contenedor estable `rescate-api`.

Las unidades versionadas viven en `ops/systemd/`. No contienen secretos: `docker exec` usa el entorno ya inyectado al contenedor por Compose. La instalacion en `/etc/systemd/system` es una accion operativa manual y no forma parte del despliegue automatico de la API.

El servicio puede terminar como `inactive (dead)` despues de cada lote; esto es correcto si el proceso finalizo con `status=0/SUCCESS`. El timer debe permanecer `active (waiting)`. PostgreSQL usa `SKIP LOCKED` para impedir envios duplicados si ocurre una ejecucion concurrente.

La configuracion de produccion asume el usuario `hanowar`, el repositorio en `/home/hanowar/ventatalk-rescate`, Docker en `/usr/bin/docker` y el contenedor `rescate-api`. Cualquier cambio de esos contratos requiere actualizar las unidades y reinstalarlas antes del despliegue correspondiente.

## Administradores globales provisionales

`HELP_ADMIN_NOTIFICATION_EMAILS` es la fuente provisional de destinatarios administradores para alertas de problemas de la feature 001. Acepta una lista opcional separada por comas; los correos se normalizan, validan y se cifran antes de persistirse. La lista vacia es valida y se muestra explicitamente en la senal de readiness.

Esta fuente debe reemplazarse en la feature 002 por membresias autorizadas. No debe evolucionar a una fuente permanente de autorizacion ni sustituye controles de acceso.

## Datos excluidos

Las plantillas no aceptan numeros o instrucciones bancarias, texto medico, contenido o rutas de comprobantes, detalles privados ni otros campos fuera de la lista permitida de cada version.
