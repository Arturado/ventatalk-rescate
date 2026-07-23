# Prueba local completa de Yo te ayudo

Esta guía usa datos completamente ficticios. No ingresar cédulas, cuentas, correos ni comprobantes reales.

## Actores locales

- Coordinador Auth Emulator: `coordinador.demo@example.test`, correo verificado.
- Firestore `authorized_users/coordinador.demo@example.test`: `role=coordinador`, `organizacion_id=org-demo`.
- Donante Auth Emulator: `donante.demo@example.test`, correo verificado y sin documento en `authorized_users`.

## Habilitar el piloto local

El backend falla de forma cerrada. Para habilitar únicamente organizaciones locales autorizadas, configurar variables no secretas y reconstruir la imagen:

```text
HELP_CASES_V2_ENABLED=true
HELP_CASES_V2_PILOT_ORGANIZATION_IDS=org-prueba,org-demo
```

Con la flag apagada o una allowlist vacía, el listado público queda vacío, los detalles no habilitados responden `404` y las operaciones organizacionales responden `403`. El historial existente del donante permanece disponible. No usar una allowlist de producción sin aprobación explícita.

## Probar estados administrativos

- `publicado → pausado → publicado`: coordinación; el caso pausado sigue visible y permite administrar cuentas, pero los donantes no pueden consultar cuentas ni registrar ayudas nuevas hasta reanudarlo.
- `meta_alcanzada → cerrado → archivado`: coordinación.
- `borrador|pendiente_validacion|listo_publicar → rechazado → archivado`: coordinación con motivo cerrado.
- `publicado|pausado|meta_alcanzada → suspendido → estado anterior`: solo `admin` o `super_admin` y con motivo cerrado.

Verificar un coordinador permitido, un coordinador de otra organización, una organización fuera del piloto y un coordinador intentando suspender. Cada transición debe dejar auditoría con estado anterior/nuevo sin texto libre ni datos sensibles.

## Probar administración posterior a la publicación

- Como `admin` o `super_admin`, modificar nombre, título, descripción o localidad de un caso `publicado`, `pausado` o `meta_alcanzada`; el cambio debe verse inmediatamente en el detalle público y crear una versión inmutable con auditoría.
- Como coordinador, comprobar que la información pública queda en modo de solo lectura después de publicar.
- Como coordinador, `admin` o `super_admin`, pulsar `Agregar otra cuenta` en un caso `listo_publicar`, `publicado` o `pausado`; debe crearse una clave lógica nueva sin desactivar la cuenta `principal`.
- `Crear nueva versión` debe conservar la clave lógica, inactivar solo la versión previa de esa cuenta y no afectar las demás cuentas.
- Los estados `meta_alcanzada`, `cerrado`, `rechazado`, `suspendido` y `archivado` no admiten altas ni versiones de cuentas.
- Como `super_admin` o `admin` sin organización, dejar vacío `Filtrar por organización` en `/admin/casos-ayuda`; deben aparecer juntos los casos de todas las organizaciones habilitadas, identificados con su `organizacion_id`.
- Elegir una organización concreta debe filtrar el listado y habilitar `Nuevo caso`; crear sin una organización concreta debe permanecer bloqueado.

## Crear el caso como coordinador

Abrir `/admin/casos-ayuda` e ingresar:

- Nombre legal: `Ana Demo Rescate`.
- Cédula: `V-99000123`.
- Título interno: `Tratamiento demo Ana`.
- Categoría: `medicamentos`.
- Meta: `25.00`.
- Moneda: `USD`.

En el detalle del caso completar la revisión:

- Identidad revisada: sí.
- Necesidad revisada: sí.
- Documentos revisados: sí.
- Sin duplicado conocido: sí.
- Menor o representado: no.
- Observaciones: `Caso ficticio para validación local integral.`

Completar información pública:

- Nombre mostrado: `Ana D.`.
- Título público: `Apoyo para tratamiento médico de Ana`.
- Descripción: `Caso ficticio creado únicamente para validar el flujo local de ayudas.`
- Localidad: `Caracas`.

Registrar consentimiento:

- Pulsar `Consentimiento del propio beneficiario`.
- Confirmar que el nombre se complete con `Ana Demo Rescate`.
- Evidencia: dejar sin archivo para probar consentimiento electrónico.

Registrar cuenta receptora:

- Clave lógica: `principal`, asignada automáticamente.
- Tipo de titular: `beneficiario`.
- Nombre del titular: `Ana Demo Rescate`.
- Relación: `propia`.
- Medio: `banco_venezolano`.
- Moneda: `USD`.
- Identificador: `0102-9999-0000-0123`.
- Instrucciones: `Cuenta corriente ficticia. No realizar transferencias reales.`
- Responsable: `Coordinador Demo`.
- Correo responsable: `coordinador.demo@example.test`.

La cuenta de beneficiario debe quedar aprobada. Enviar a validación, marcar listo y publicar. Guardar:

- ID numérico de la URL administrativa como `V2_CASE_ID`.
- Identificador `ayuda-...` mostrado en el encabezado como `V2_PUBLIC_ID`.

## Registrar ayuda como donante

Cerrar sesión del coordinador, abrir `/yo-te-ayudo`, entrar al caso y pulsar `Quiero ayudar`.

- Iniciar sesión con `donante.demo@example.test`.
- Aceptar condiciones `donor-v1`.
- Verificar que aparezca exclusivamente la cuenta ficticia aprobada.
- Cuenta utilizada: `Ana Demo Rescate · banco venezolano · USD`.
- Monto enviado: `10.00`.
- Moneda: `USD`, no editable porque deriva de la cuenta.
- Fecha: fecha local actual.
- Referencia: `REF-DEMO-001`.
- Comentario: `Primera ayuda ficticia de validación.`
- Comprobante: opcional. Para probarlo, usar un PDF/JPG/PNG ficticio menor de 5 MB.

Resultado esperado:

- Estado `pendiente_confirmacion`.
- `/mis-ayudas` muestra solo la ayuda del donante autenticado.
- El progreso público continúa en `0.00 USD` hasta confirmar.
- El detalle público muestra la meta equivalente en `VES`, `USD` y `EUR` como referencia actual, con fecha y atribución BCV mediante DolarApi.
- Reintentar el mismo envío no crea otra ayuda.

## Confirmar como coordinador

Cerrar sesión del donante e ingresar con `coordinador.demo@example.test`. Abrir el mismo detalle administrativo.

En `Ayudas reportadas` debe aparecer la ayuda pendiente. Cualquier coordinador autorizado de la misma organización puede confirmar directamente; el responsable de la cuenta se muestra como contacto operativo:

- Monto recibido: `10.00`.
- Moneda recibida: `USD`.
- Fecha efectiva: la misma fecha del reporte.
- Comentario: `Recepción ficticia confirmada localmente.`

Resultado esperado:

- Ayuda en estado `confirmada`.
- Progreso público: `10.00 USD` de `25.00 USD`.
- Contador de ayudas confirmadas: `1`.
- Reintentar con la misma clave no incrementa nuevamente monto ni contador.

Para probar revisión, crear otra ayuda y pulsar `Pasar a revisión` antes de confirmar. Para probar meta alcanzada, reportar y confirmar una segunda ayuda por `15.00 USD`. El caso debe pasar a `meta_alcanzada` y continuar visible públicamente con `25.00 USD` confirmados.

Una confirmación en una moneda distinta a la meta consulta las cotizaciones actuales de `https://ve.dolarapi.com/v1/dolares/oficial` y `https://ve.dolarapi.com/v1/euros/oficial`. Solo acepta respuestas con `fuente=oficial`, atribuye la tasa al BCV y fija un snapshot inmutable con URL y hash de evidencia. La fecha efectiva de transferencia se conserva como dato operativo, pero no selecciona una tasa histórica. Si DolarApi no responde o devuelve datos inválidos, la ayuda queda `en_revision` y el progreso no cambia. Una confirmación exitosa muestra monto recibido, equivalente aplicado, tasa, fuente y fecha de cotización.

La contingencia manual solo está disponible para `super_admin`. Requiere la fecha actual de cotización, par de monedas, valor, referencia oficial y motivo. La tasa queda marcada `BCV-MANUAL`, es global e inmutable y registra actor y auditoría; después puede reintentarse la confirmación de la ayuda en revisión.

## Probar problema y rechazo

Crear otra ayuda ficticia y, desde `Ayudas reportadas`:

1. Pulsar `Reportar problema`.
2. Seleccionar `Transferencia no recibida`.
3. Escribir `Incidencia ficticia para validación local`.
4. Guardar y comprobar el estado `problema_reportado`.
5. Pulsar `Pasar a revisión` y comprobar el estado `en_revision`.
6. Pulsar `Rechazar`, escribir `No fue posible verificar la transferencia ficticia` y confirmar.

Resultado esperado:

- La ayuda termina en `rechazada`.
- El tipo, detalle y resolución permanecen visibles solo en coordinación.
- El detalle y la resolución están cifrados en la base de datos y no aparecen en auditoría.
- El monto y el contador público no cambian.
- Una ayuda rechazada ya no ofrece acciones de confirmación.

## Ejecutar Postman

Usar un caso publicado nuevo y ejecutar:

```bash
V2_ORGANIZATION_ID=org-demo \
V2_ACTOR_EMAIL=coordinador.demo@example.test \
V2_ACTOR_UID=coordinador-demo-uid \
V2_DONOR_EMAIL=donante.demo@example.test \
V2_DONOR_UID=donante-postman-uid-unico \
V2_PUBLIC_ID=ayuda-reemplazar \
V2_CASE_ID=1 \
V2_HELP_AMOUNT=10.00 \
V2_HELP_CURRENCY=USD \
V2_RECEIVED_AMOUNT=1000.00 \
V2_RECEIVED_CURRENCY=VES \
V2_EXPECTED_RATE_SOURCE=DOLARAPI-BCV \
make postman-test-yo-te-ayudo-donante
```

Reemplazar `V2_PUBLIC_ID` y `V2_CASE_ID`. Usar un `V2_DONOR_UID` nuevo para una corrida independiente. La carpeta consulta equivalencias, acepta términos, consulta cuentas, reporta, lista, pasa a revisión, confirma y reintenta la confirmación para comprobar idempotencia. Ejecutar pruebas multimoneda y contingencias sobre una copia descartable de la base local porque estos registros V2 no tienen limpieza automática.

## Probar concurrencia PostgreSQL

Las pruebas de concurrencia requieren una base PostgreSQL vacía y descartable cuyo nombre termine en `_test`. Nunca apuntar esta variable a la base local habitual ni a producción:

```bash
HELP_CONCURRENCY_TEST_DATABASE_URL=postgresql://usuario:clave@postgres/rescate_concurrency_test \
pytest -q tests/test_help_exchange_postgres.py
```

Las pruebas eliminan y recrean las tablas dentro de esa base. Verifican que conversiones simultáneas reutilicen un único snapshot y que dos confirmaciones concurrentes sobre una ayuda incrementen el progreso una sola vez.

## Albergues

`postman/ventatalk-rescate-albergues.postman_collection.json` se conserva. Sus 33 requests de sesión/CSRF, administración, responsables, reportes y órdenes no están todavía en la colección consolidada. No eliminarla hasta migrar esa cobertura y dejar una colección manual reducida para la sesión web.
