# Pruebas automatizadas de API con Postman

La coleccion `ventatalk-rescate-api-tests.postman_collection.json` prueba flujos completos de:

- salud y rechazo de requests no autenticados;
- creacion, consulta, edicion, cambio de estado y eliminacion de personas;
- directorio de hospitales y ciclo de vida de pacientes;
- publicacion, privacidad, auditoria y eliminacion de casos de ayuda.

El flujo de casos valida especificamente que el contacto rechaza solicitudes sin API key, con API key invalida y sobre casos no publicados antes de comprobar el acceso autorizado y su auditoria.

Cada ejecucion genera un `run_id`, guarda los identificadores de los registros creados y los elimina al terminar el flujo. Debe ejecutarse contra una instancia local o de pruebas, nunca contra produccion.

## Desde Postman

1. Importa `ventatalk-rescate-api-tests.postman_collection.json`.
2. Importa `ventatalk-rescate-local.postman_environment.json`.
3. Asigna `api_key` como valor local y sensible en Postman.
4. Selecciona el ambiente local y ejecuta la coleccion completa con Collection Runner.

No ejecutes requests sueltos que dependan de variables como `persona_id`, `paciente_id` o `caso_id`; esas variables se capturan durante el flujo.

## Yo te ayudo V2

La colección incluye cuatro carpetas V2:

- `04 - Yo te ayudo V2 publico`: lista y detalle público, sin secretos.
- `05 - Yo te ayudo V2 protegido`: rechazos sin API key/firma y listado con actor HMAC.
- `06 - Yo te ayudo V2 donante y confirmacion`: términos, cuentas, reporte idempotente, Mis ayudas, revisión y confirmación.
- `07 - Contingencia manual BCV`: registro excepcional de una tasa inmutable por `super_admin`.

Ejecuta las lecturas públicas:

```bash
make postman-test-yo-te-ayudo-public
```

Para el listado protegido, configura temporalmente en tu entorno local:

```bash
export V2_ORGANIZATION_ID=org-prueba
export V2_ACTOR_EMAIL=coordinador1@example.test
```

El target toma `API_KEY` y `ACTOR_SIGNING_SECRET` desde `.env`; no los agregues a la colección ni los compartas. Luego ejecuta:

```bash
make postman-test-yo-te-ayudo-protegido
```

La carpeta protegida requiere que el actor HMAC exista en `authorized_users` y que corresponda a `V2_ORGANIZATION_ID`.

El flujo donante requiere un caso V2 ya publicado con una cuenta aprobada. Se ejecuta con variables explícitas para evitar crear o confirmar datos por accidente:

```bash
V2_ORGANIZATION_ID=org-demo \
V2_ACTOR_EMAIL=coordinador.demo@example.test \
V2_ACTOR_UID=coordinador-demo-uid \
V2_DONOR_EMAIL=donante.demo@example.test \
V2_DONOR_UID=donante-demo-uid \
V2_PUBLIC_ID=ayuda-... \
V2_CASE_ID=1 \
V2_HELP_AMOUNT=10.00 \
V2_HELP_CURRENCY=USD \
make postman-test-yo-te-ayudo-donante
```

La contingencia BCV solo debe usarse cuando la fuente oficial no está disponible y se cuenta con evidencia oficial verificable. La tasa afecta globalmente el par y fecha indicados, queda inmutable y no tiene limpieza automática:

```bash
V2_SUPER_ADMIN_EMAIL=admin.demo@example.test \
V2_SUPER_ADMIN_UID=admin-demo-uid \
V2_MANUAL_RATE_DATE=2026-07-20 \
V2_MANUAL_RATE_SOURCE_CURRENCY=USD \
V2_MANUAL_RATE_TARGET_CURRENCY=EUR \
V2_MANUAL_RATE_VALUE=0.9100000000 \
V2_MANUAL_RATE_REFERENCE="Boletín oficial BCV 2026-07-20" \
V2_MANUAL_RATE_REASON="Fuente oficial temporalmente inaccesible" \
make postman-test-bcv-manual
```

La colección `ventatalk-rescate-albergues.postman_collection.json` continúa siendo necesaria: sus 33 requests de sesión web, CSRF, centros, responsables, reportes y órdenes aún no están en la colección consolidada. No debe eliminarse hasta automatizar esa cobertura en carpetas independientes y conservar una colección manual reducida para sesión/CSRF.

La ejecución completa (`make postman-test`) omite automáticamente el request HMAC si `actor_signing_secret` no está disponible; las pruebas públicas y de rechazo sí se ejecutan.

## Desde terminal

Con el backend levantado en `http://localhost:8010`:

```bash
make postman-test
```

Para ejecutar solamente el flujo de casos de ayuda:

```bash
make postman-test-casos-ayuda
```

El target usa `API_KEY` cargada por el `Makefile` desde `.env`, pero no la escribe en los archivos de Postman. Tambien puedes ejecutar Newman directamente:

```bash
npx --yes newman run postman/ventatalk-rescate-api-tests.postman_collection.json \
  --env-var base_url=http://localhost:8010 \
  --env-var api_key="$API_KEY"
```

## Cobertura excluida

La suite repetible no incluye endpoints que requieren cookie/CSRF de administrador, archivos reales, firma HMAC de organizacion o que crean datos sin una ruta segura de limpieza. Las pruebas de Postman complementan, pero no reemplazan, las pruebas aisladas de `tests/`. La coleccion manual `ventatalk-rescate-albergues.postman_collection.json` permanece disponible para exploracion controlada de albergues.
