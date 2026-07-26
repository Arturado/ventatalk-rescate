# ADR-002: Outbox de notificaciones de la feature 001

## Decision

Las notificaciones de la feature 001 se registran en la misma transaccion que el cambio de dominio. El outbox solo conserva el hash ciego y el cifrado del correo, junto con un payload cifrado validado contra plantillas cerradas y versionadas.

Resend se configura en tiempo de ejecucion con `RESEND_API_KEY` y `RESEND_FROM_EMAIL`. Los enlaces de acceso temporal usan `HELP_FRONTEND_BASE_URL`.

## Administradores globales provisionales

`HELP_ADMIN_NOTIFICATION_EMAILS` es la fuente provisional de destinatarios administradores para alertas de problemas de la feature 001. Acepta una lista opcional separada por comas; los correos se normalizan, validan y se cifran antes de persistirse. La lista vacia es valida y se muestra explicitamente en la senal de readiness.

Esta fuente debe reemplazarse en la feature 002 por membresias autorizadas. No debe evolucionar a una fuente permanente de autorizacion ni sustituye controles de acceso.

## Datos excluidos

Las plantillas no aceptan numeros o instrucciones bancarias, texto medico, contenido o rutas de comprobantes, detalles privados ni otros campos fuera de la lista permitida de cada version.
