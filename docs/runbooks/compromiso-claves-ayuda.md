# Runbook: compromiso de claves de ayuda

Estado: borrador operativo
Propietario: responsable de incidentes
Versión: 1.0
Fecha: 2026-07-20
Clasificación: interna restringida

## Activación

Activar ante exposición confirmada o probable de secretos, acceso anómalo al gestor, backups no autorizados o evidencia de descifrado fuera de Ventatalk.

## Contención

1. Abrir incidente restringido y preservar evidencia.
2. Revocar accesos humanos y de servicio sospechosos.
3. Deshabilitar temporalmente altas, consultas bancarias y exportaciones afectadas.
4. Rotar credenciales de infraestructura relacionadas sin destruir las claves necesarias para análisis.
5. Evitar escribir secretos o datos personales en canales de coordinación.

## Recuperación

1. Generar nuevas claves de cifrado e identidad en un entorno confiable.
2. Desplegar lectores compatibles y cambiar inmediatamente la versión activa.
3. Recifrar datos y reindexar identidades siguiendo el runbook de rotación.
4. Revisar accesos, auditoría, logs, Functions, backups y artefactos de despliegue.
5. Verificar integridad, aislamiento organizacional y restauración antes de reactivar operaciones.

## Evaluación y comunicación

- Determinar datos, organizaciones, fechas y backups potencialmente expuestos.
- Consultar obligaciones legales y de notificación aplicables.
- Informar únicamente por canales aprobados y con datos mínimos.
- Documentar causa raíz, controles fallidos y acciones preventivas.

## Cierre

Requiere aprobación del responsable del incidente, evidencia de rotación, pruebas de recuperación, monitoreo reforzado y revisión posterior sin secretos ni PII en el informe general.
