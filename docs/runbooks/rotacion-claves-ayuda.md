# Runbook: rotación de claves de ayuda

Estado: borrador operativo
Propietario: responsable de seguridad de plataforma
Versión: 1.0
Fecha: 2026-07-20
Clasificación: interna

## Precondiciones

- Ticket aprobado, responsables asignados y ventana anunciada.
- Backup cifrado reciente y restauración verificada.
- Métricas de errores de cifrado y conteos base registrados.
- Nunca copiar valores de claves al ticket o a este documento.

## Rotación de cifrado

1. Generar una clave de 32 bytes en el gestor autorizado y asignar una versión nueva.
2. Añadir la nueva clave sin retirar versiones anteriores.
3. Desplegar lectores compatibles con ambas versiones.
4. Cambiar `HELP_DATA_ACTIVE_KEY_VERSION` para cifrar escrituras nuevas.
5. Recifrar por lotes pequeños, con checkpoints, desde la versión anterior.
6. Verificar conteos, descifrado, autenticación y ausencia de plaintext.
7. Actualizar y probar backups con el llavero vigente.
8. Mantener la clave anterior durante el período aprobado de rollback.
9. Retirarla solo cuando ninguna fila, archivo o backup requerido dependa de ella.

## Rotación del índice HMAC

1. Suspender altas y cambios de identidad.
2. Respaldar y verificar restauración.
3. Descifrar cada identidad dentro del proceso controlado y calcular el HMAC nuevo.
4. Verificar colisiones y unicidad por organización antes de escribir.
5. Reindexar en una transacción o lotes reanudables con altas aún suspendidas.
6. Ejecutar pruebas de duplicados y aislamiento organizacional.
7. Reactivar altas y retirar la clave anterior después de validar backups.

## Rollback

- Volver la versión activa a la anterior, conservando ambas claves.
- Detener recifrado/reindexación y restaurar el último checkpoint consistente.
- No restaurar un backup sin confirmar que su llavero sigue disponible.

## Evidencia de cierre

Registrar versiones, conteos procesados, fallos, pruebas, responsables, timestamps y referencias al backup. No registrar claves, ciphertext ni identidades.
