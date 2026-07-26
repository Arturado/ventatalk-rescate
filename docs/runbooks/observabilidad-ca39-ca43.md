# Observabilidad y limites CA-39 / CA-43

## Limites de donantes

Los limites se validan al iniciar el proceso y al usarse. Todos aceptan enteros positivos; una configuracion invalida impide iniciar la API.

| Variable | Default | Maximo | Unidad contable |
| --- | ---: | ---: | --- |
| `DONOR_ACCOUNT_ACTOR_LIMIT` | 10 | 1000 | cuenta aprobada divulgada por UID y caso |
| `DONOR_ACCOUNT_IP_LIMIT` | 30 | 1000 | cuenta aprobada divulgada por hash HMAC de IP y caso |
| `DONOR_ACCOUNT_LIMIT_WINDOW_SECONDS` | 300 | 86400 | segundos de la ventana movil de divulgacion |
| `DONOR_REPORT_LIMIT` | 20 | 1000 | reporte creado por UID, entre todos los casos |
| `DONOR_REPORT_LIMIT_WINDOW_SECONDS` | 3600 | 86400 | segundos de la ventana movil de reportes |

Una respuesta que divulga dos cuentas consume dos unidades. El bloqueo de la fila del caso serializa el conteo y la escritura en PostgreSQL. Una denegacion conserva HTTP 429, crea `limite_seguridad_denegado` en la auditoria con solo motivo acotado, caso y organizacion, y aumenta metricas sin UID, correo, cuenta ni IP.

## Endpoints

- `GET /live`: comprueba que el proceso responde. No consulta dependencias.
- `GET /ready`: comprueba DB, claves de cifrado, escritura en `HELP_PRIVATE_UPLOAD_ROOT`, configuracion Resend y acceso al outbox. Responde solo `ready` o `not_ready`.
- `GET /health`: compatibilidad historica; conserva la respuesta anterior. Para balanceadores nuevos, usar `/live` y `/ready`.
- `GET /metrics`: OpenMetrics 1.0 sin autenticacion ni etiquetas de alta cardinalidad.

La readiness falla cerrada cuando Resend no esta configurado. No incluir cuerpos, query strings, rutas privadas, tokens, correos, identificadores de cuenta, texto cifrado o hashes de IP en logs ni etiquetas.

## Metricas

- `rescate_http_requests_total{method,status_class}` y `rescate_http_request_duration_seconds_*`.
- `rescate_access_denials_total{reason}` y `rescate_rate_limits_total{scope}`.
- `rescate_donor_account_disclosures_total`.
- `rescate_bcv_requests_total{outcome}` y `rescate_bcv_request_duration_seconds_*`.
- `rescate_aid_pending_count`, `rescate_aid_review_count` y edades de los elementos mas antiguos.
- `rescate_notification_queue_count{state}` y `rescate_notification_pending_oldest_age_seconds`.

El colector vive en cada proceso. En despliegues con varias replicas, el scraper debe sumar contadores y conservar las distribuciones por instancia. Las metricas derivadas de DB ya representan el estado compartido y no deben sumarse entre replicas para obtener un total global.

## Alertas recomendadas

Estas son bases iniciales; adaptar ventanas a trafico real y objetivos de servicio:

| Condicion | Aviso | Critica |
| --- | --- | --- |
| readiness | 2 fallos consecutivos | 5 minutos sin replicas ready |
| respuestas 5xx | > 2% durante 10 min | > 5% durante 5 min |
| p95 de latencia HTTP | > 1 s durante 15 min | > 2.5 s durante 10 min |
| denegaciones por limite | > 20 en 5 min | > 100 en 5 min o aumento 5x sobre base |
| BCV fallido | 2 fallos consecutivos | sin exito durante 30 min en horario operativo |
| ayuda pendiente mas antigua | > 2 h | > 8 h |
| ayuda en revision mas antigua | > 12 h | > 24 h |
| notificacion pendiente mas antigua | > 10 min | > 30 min |
| notificaciones fallidas | > 5 | crecimiento continuo durante 15 min |

En GCP, Cloud Monitoring puede hacer uptime checks a `/live` y `/ready`; un scraper compatible con Prometheus puede ingerir `/metrics`. En local se pueden usar las mismas condiciones con curl y un scraper local. Este cambio no crea alertas ni recursos de produccion.

## Comprobacion local

```bash
curl -fsS http://127.0.0.1:8010/live
curl -fsS http://127.0.0.1:8010/ready
curl -fsS http://127.0.0.1:8010/health
curl -fsS -H 'Accept: application/openmetrics-text' http://127.0.0.1:8010/metrics
```

Los docs protegidos usan `X-API-Key` tanto para `/cowboy-bebop` como para `/cowboy-bebop/openapi.json`. Nunca colocar la clave en la URL:

```bash
curl -fsS -H "X-API-Key: $API_KEY" http://127.0.0.1:8010/cowboy-bebop/openapi.json
```
