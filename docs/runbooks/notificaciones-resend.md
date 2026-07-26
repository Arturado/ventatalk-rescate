# Notificaciones de ayuda con Resend

La outbox se escribe en la misma transacción que cada operación de dominio. El worker nunca recibe correos, tokens ni contenido bancario mediante argumentos de línea de comandos.

## Configuración requerida

Configurar mediante el gestor de secretos o variables del entorno autorizado, nunca en archivos versionados:

```text
RESEND_API_KEY
RESEND_FROM_EMAIL
HELP_FRONTEND_BASE_URL
HELP_ADMIN_NOTIFICATION_EMAILS
```

`HELP_ADMIN_NOTIFICATION_EMAILS` es una fuente provisional para `001`. Debe sustituirse por membresías organizacionales en `002`.

## Verificación local

Sin configuración, `/ready` responde `503` y el worker termina sin consumir la cola.

Con Resend configurado:

```bash
python3 -m scripts.help_notifications --limit 25
curl -sf http://localhost:8010/ready
curl -sf http://localhost:8010/metrics
```

Programar el comando con un scheduler que impida ejecuciones solapadas innecesarias. PostgreSQL usa `SKIP LOCKED`, por lo que dos workers no envían la misma fila.

## Fallos

- Revisar `help_notification_queue` y `help_notification_oldest_pending_seconds` en OpenMetrics.
- Los reintentos usan backoff exponencial y un máximo de cinco intentos.
- Los errores almacenan solo códigos acotados; no se conserva el cuerpo de Resend.
- No reintentar manualmente modificando filas. Resolver configuración o conectividad y volver a ejecutar el worker.
