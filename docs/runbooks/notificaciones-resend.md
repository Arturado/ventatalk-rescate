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

## Worker de producción

Las unidades de referencia están versionadas en:

```text
ops/systemd/ventatalk-help-notifications.service
ops/systemd/ventatalk-help-notifications.timer
```

Validarlas e instalarlas en Donweb:

```bash
cd /home/hanowar/ventatalk-rescate
sudo systemd-analyze verify \
  ops/systemd/ventatalk-help-notifications.service \
  ops/systemd/ventatalk-help-notifications.timer
sudo install -m 0644 \
  ops/systemd/ventatalk-help-notifications.service \
  /etc/systemd/system/ventatalk-help-notifications.service
sudo install -m 0644 \
  ops/systemd/ventatalk-help-notifications.timer \
  /etc/systemd/system/ventatalk-help-notifications.timer
sudo systemctl daemon-reload
sudo systemctl enable --now ventatalk-help-notifications.timer
```

Probar un lote y revisar el scheduler:

```bash
sudo systemctl start ventatalk-help-notifications.service
systemctl status ventatalk-help-notifications.service --no-pager
systemctl status ventatalk-help-notifications.timer --no-pager
systemctl list-timers ventatalk-help-notifications.timer --no-pager
sudo journalctl -u ventatalk-help-notifications.service -n 20 --no-pager
```

El servicio `oneshot` debe finalizar con `status=0/SUCCESS`; luego aparece como `inactive (dead)`. El timer debe permanecer `active (waiting)`. Cada ejecución procesa hasta 25 filas y escribe en journald únicamente el agregado `claimed`, `sent` y `failed`.

El timer usa el nombre estable `rescate-api`. Después de reconstruir la API, confirmar que el contenedor conserva ese nombre y ejecutar un lote manual. PostgreSQL usa `SKIP LOCKED`, por lo que una concurrencia accidental no envía dos veces la misma fila.

## Pausa y recuperación

Detener el timer antes de corregir credenciales, dominio remitente o conectividad para no agotar los cinco intentos:

```bash
sudo systemctl stop ventatalk-help-notifications.timer
systemctl is-active ventatalk-help-notifications.timer
```

Revisar solo estados agregados, sin destinatarios ni payloads:

```bash
docker exec ventatalk-db-1 sh -lc \
  'psql -U "$POSTGRES_USER" -d rescate -c "
    SELECT estado, ultimo_error_codigo, intentos, COUNT(*)
    FROM casos_ayuda_notificaciones
    GROUP BY estado, ultimo_error_codigo, intentos
    ORDER BY estado, ultimo_error_codigo, intentos;
  "'
```

Después de corregir la causa y cuando `proximo_intento_at` haya vencido, ejecutar un único lote manual. Si termina sin fallos, reactivar el timer:

```bash
docker exec rescate-api python -m scripts.help_notifications --limit 25
sudo systemctl start ventatalk-help-notifications.timer
```

No modificar filas para forzar reintentos ni mostrar `RESEND_API_KEY`, correos cifrados o payloads en consola.

## Fallos

- Revisar `help_notification_queue` y `help_notification_oldest_pending_seconds` en OpenMetrics.
- Los reintentos usan backoff exponencial y un máximo de cinco intentos.
- Los errores almacenan solo códigos acotados; no se conserva el cuerpo de Resend.
- No reintentar manualmente modificando filas. Resolver configuración o conectividad y volver a ejecutar el worker.
