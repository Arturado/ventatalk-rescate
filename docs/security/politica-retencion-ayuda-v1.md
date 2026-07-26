# Matriz de retencion y anonimización de Yo te ayudo

Estado: aprobada provisionalmente para implementacion local
Version aprobada: 1.0-local
Fecha de aprobacion provisional: 2026-07-23
Propietario: equipo de plataforma Rescate
Revision obligatoria: legal, privacidad, seguridad e infraestructura antes del piloto

## Regla de interpretacion

Los cortes son inclusivos: un registro cuya fecha sea exactamente igual al corte ya es elegible. Un bloqueo legal activo suspende toda eliminacion, anonimizacion y vencimiento del caso o registro alcanzado. El bloqueo de un caso se propaga a beneficiario compartido, cuentas, documentos, comprobantes, ayudas, consentimientos, auditoria, notificaciones y sesiones con alcance sobre ese caso.

| Clase | Disparador | Periodo | Accion al vencer | Evidencia retenida | Excepcion por bloqueo | Rol autorizado | Comportamiento del backup |
| --- | --- | --- | --- | --- | --- | --- | --- |
| Identidad y contacto de beneficiario/representante | `cerrado_at` del ultimo caso vinculado | 2 anos | Eliminar ciphertext opcional; sustituir campos e indice ciego obligatorios por valores no identificables | ID interno, organizacion, estado y relacion referencial | No actuar si cualquier caso vinculado esta abierto o bloqueado | Responsable de privacidad con ejecucion DBA local | Permanece en backups previos hasta que estos expiren; restaurar obliga a reejecutar retencion |
| Relato privado y datos de verificacion | `cerrado_at` | 2 anos | Anonimizar o poner a nulo | Categoria, meta, estado y trazabilidad del caso | Bloqueo del caso | Responsable de privacidad con ejecucion DBA local | Igual que identidad; sin destruccion retroactiva no aprobada |
| Identificadores bancarios y contacto responsable | `cerrado_at` | 2 anos | Inactivar cuenta; sustituir campos obligatorios y borrar campos opcionales | ID/version de cuenta, medio, moneda, consentimiento y enlace a ayudas | Bloqueo del caso | Responsable de privacidad con ejecucion DBA local | Igual que identidad; las versiones restauradas vuelven a someterse al proceso |
| Documentos medicos o privados | `cerrado_at` | 2 anos | Eliminar archivo cifrado dentro de la raiz configurada; borrar nombre original; marcar `archivo_eliminado` | Fila, tipo, version, tamano, checksum y enlace de consentimiento | Bloqueo del caso | Responsable de privacidad y custodio documental | El archivo puede existir en backups aun vigentes; retencion posterior a restore es obligatoria |
| Comprobantes | `cerrado_at` del caso de la ayuda | 2 anos | Eliminar archivo cifrado y nombre original; marcar `archivo_eliminado` | Ayuda, version, tipo, tamano, checksum, estado y totales financieros | Bloqueo del caso | Responsable de privacidad y custodio financiero | Igual que documentos; no se elimina la FK ni la evidencia financiera minima |
| Evidencia financiera minima | `cerrado_at` | 7 anos | En esta version: conservar sin destruccion | Ayuda, montos, monedas, fecha efectiva, cuenta/version, confirmacion, tasa y checksum de comprobante | Bloqueo del caso extiende conservacion | Custodio financiero con aprobacion legal | Incluida y cifrada; restaurar conserva totales y relaciones |
| Consentimiento | `cerrado_at` | 7 anos | En esta version: conservar sin destruccion | Version del texto, alcance, firmante cifrado, fecha, registrador y referencia documental | Bloqueo del caso extiende conservacion | Privacidad/legal | Incluido y cifrado; no retirar claves sin decision separada |
| Auditoria | Fecha del evento o `cerrado_at` cuando pertenece al caso | 7 anos | En esta version: conservar inmutable | Actor, accion, entidad, organizacion, fecha, motivo y metadata minima | Bloqueo del caso o evento extiende conservacion | Seguridad/legal | Incluida y cifrada; no se muta al restaurar |
| Sesion o desafio temporal | `created_at` | 30 dias | Eliminar scopes y registro; equivale a revocacion definitiva | Auditoria de acciones de negocio, no token/hash de sesion | Bloqueo directo o de cualquier caso en scope | Operador de privacidad/seguridad | Los backups previos pueden contenerla; reejecutar retencion tras restore |
| Destinatario y payload de notificacion | `created_at` | 90 dias | Sustituir destinatario/hash y payload; cancelar entrega pendiente antigua | Evento, plantilla, estado final, intentos, proveedor, codigo de error y tiempos agregables | Bloqueo directo o del caso | Operador de privacidad/seguridad | Reejecutar retencion tras restore |
| Logs tecnicos | Emision | 30 dias | Expirar en el proveedor de logs | Metricas agregadas sin datos personales | Bloqueo excepcional documentado fuera de la aplicacion | Seguridad/SRE | No se incluyen en este backup de aplicacion |
| Bloqueo legal | Alta autorizada | Indefinido hasta liberacion expresa | No eliminar; registrar liberacion separada | Tipo/ID de entidad, codigo de motivo, autorizador y fechas | Es la excepcion dominante | Legal o responsable de privacidad designado | Incluido en PostgreSQL y aplicado inmediatamente tras restore |

## Advertencia legal obligatoria

Los plazos 2/7 anos y las clases son provisionales, no constituyen asesoria legal y no autorizan una ejecucion sobre datos reales. Antes de produccion deben validarse jurisdiccion, base legal, derechos de titulares, prescripcion, obligaciones fiscales/AML, litigios, menores, datos de salud, alcance de backups y destruccion verificable. La destruccion de evidencia financiera, consentimiento o auditoria al superar siete anos queda deliberadamente sin resolver: esta version conserva el minimo inmutable hasta recibir una decision legal y un diseno de migracion aprobado.

La retirada de claves no es un mecanismo de borrado aprobado porque podria inutilizar evidencia aun vigente y backups. Ningun rol de aplicacion puede activar esta politica automaticamente; la ejecucion local exige acceso operativo autorizado, dry-run revisado y guardas de destino.
