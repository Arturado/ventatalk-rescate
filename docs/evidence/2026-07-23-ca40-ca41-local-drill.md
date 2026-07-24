# Evidencia sanitizada CA-40/CA-41

Fecha UTC: 2026-07-23
Alcance: simulacro local descartable con datos exclusivamente sinteticos
Resultado final: satisfactorio para el corte local
Tiempo transcurrido final: 2.54 segundos (`pytest`: 2.38 segundos)

## Entorno seguro

- PostgreSQL 16 Alpine en un contenedor nuevo y aislado, publicado solo en localhost durante la prueba.
- Base fuente nueva y base destino nueva; ambas terminaban en `_test`.
- Dos arboles privados creados por `tmp_path`; destino vacio antes del restore.
- Claves sinteticas de una sola ejecucion y passphrase sintetica suministradas en el entorno/proceso, no en archivos de configuracion.
- `pg_dump` y `pg_restore` 16 ejecutados mediante contenedores cliente locales; el dump viajo por pipes.
- No se consulto, modifico ni desplego infraestructura de produccion.

## Datos sembrados

Se creo un caso V2 cerrado exactamente siete anos antes del reloj fijo del simulacro. Se sembraron once clases relacionales con una fila cada una: beneficiario, caso, cuenta, ayuda, consentimiento, documento medico privado, comprobante, auditoria, notificacion, sesion temporal y scope de sesion. Los dos archivos eran ciphertext AES-GCM de contenido sintetico.

Valores financieros de control:

- Ayuda reportada total: USD 25.00.
- Monto confirmado del caso: USD 25.00.
- Confirmaciones del caso: 1.
- Eventos de auditoria: 1.
- Archivos privados cifrados: 2.

## Secuencia verificada

1. Se capturaron conteos, totales, acciones de auditoria y checksums de ciphertext.
2. Se genero un artefacto autenticado y cifrado con dump, archivos y manifiesto.
3. Se destruyeron solo el esquema y el arbol fuente descartables.
4. Se restauro en la base `_test` nueva y la raiz vacia.
5. Los once conteos, tres controles financieros, accion de auditoria y dos checksums coincidieron exactamente.
6. Ambos archivos restaurados se descifraron y autenticaron contra sus plaintext sinteticos esperados.
7. Al retirar la version de clave declarada, restore y `HelpDataCipher` fallaron cerrados; no se escribio contenido adicional.
8. Dry-run de retencion informo 7 mutaciones potenciales: un caso, un beneficiario, una cuenta, dos archivos, una sesion y una notificacion. Informo ademas 3 evidencias minimas conservadas: ayuda, consentimiento y auditoria.
9. Apply produjo las mismas 7 acciones, conservo ayuda/auditoria/totales, elimino los dos archivos y la sesion, y anonimizo los campos previstos.
10. El segundo apply informo `mutation_count=0`; la evidencia minima siguio conservada.
11. El test elimino ambos esquemas al finalizar. Los artefactos de backup generados por pytest se eliminaron y no forman parte del repositorio.

## Comandos de evidencia

El comando final fue el test PostgreSQL `tests/test_help_backup_postgres.py` con URLs y binarios suministrados mediante variables de entorno de una sola ejecucion. Resultado exacto:

```text
.                                                                        [100%]
1 passed in 2.38s
real 2.54
user 0.71
sys 0.09
```

Pruebas PostgreSQL preexistentes, sobre otra base descartable del mismo contenedor y con claves sinteticas:

```text
.......                                                                  [100%]
7 passed in 2.70s
```

El primer intento del simulacro no llego a conectarse porque el virtualenv Python 3.13 no tenia instalado el driver PostgreSQL declarado por el proyecto. Se instalo `psycopg2-binary 2.9.10` solo en ese virtualenv local; no se modifico ningun lockfile ni archivo de entorno. El intento exitoso anterior a la evidencia final tambien paso, y la corrida final incorporo comprobaciones adicionales de totales y sesion temporal.

## Alcance de la afirmacion

Esta evidencia soporta que el codigo local puede crear y autenticar un backup, restaurar relaciones y archivos sinteticos en un destino descartable, validar claves/checksums/totales y reaplicar retencion idempotente. No demuestra RPO 24h ni RTO 4h en produccion, PITR, backups del proveedor, IAM, KMS, storage, regiones, capacidad, scheduler, alertas, Firestore real, legal hold operativo por personal real ni destruccion juridicamente valida. Todos esos puntos siguen bloqueando CA-40/CA-41 de produccion y el piloto.
