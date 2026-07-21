# Estándar de protección de datos de ayuda

Estado: aprobado para implementación local
Propietario: equipo de plataforma Rescate
Versión: 1.0
Fecha: 2026-07-20
Próxima revisión: antes del piloto
Clasificación: interna

## Alcance

Aplica a Ventatalk, Firebase Functions, frontend, archivos, auditoría, métricas y backups del módulo “Yo te ayudo”.

## Clasificación

| Categoría | Ejemplos | Protección | Índice permitido |
| --- | --- | --- | --- |
| Restringida | Cédula, cuenta bancaria, comprobantes | AES-256-GCM / archivo privado | HMAC cuando sea imprescindible |
| Confidencial | Nombre legal, teléfono, correo, dirección, relato | AES-256-GCM | Hash/HMAC específico y justificado |
| Operativa interna | Título interno, estado, categoría, meta | Control de acceso organizacional | Índices SQL |
| Pública autorizada | Nombre mostrado, descripción aprobada, progreso | Lista permitida y consentimiento vigente | Índices de lectura pública |

## Gestión de claves

- Claves de cifrado e identidad son independientes, aleatorias y de 32 bytes.
- Variables esperadas: `HELP_DATA_ENCRYPTION_KEY_<VERSION>`, `HELP_DATA_ACTIVE_KEY_VERSION` y `HELP_IDENTITY_HASH_KEY`.
- Solo Ventatalk accede a estas variables. No se copian a React, Firestore ni Functions.
- Los valores reales no aparecen en documentación, tickets, comandos registrados ni commits.
- Producción usa un gestor de secretos y control de acceso por rol; local usa `.env` ignorado por Git.

## Reglas de implementación

- Cada cifrado usa nonce nuevo y AAD estable por tabla/campo.
- Toda entrada sensible se cifra antes del primer `flush`.
- La cédula se normaliza antes del HMAC; el plaintext no se persiste.
- Las respuestas usan DTO explícito y nunca devuelven campos `*_cifrado` o hashes.
- Auditoría conserva actor, acción, entidad, estado y versión, sin contenido sensible.
- Los errores externos son genéricos y no incluyen payloads ni excepciones de base de datos.
- Backups y restauraciones conservan el mismo nivel de acceso que datos activos.

## Verificación mínima

- Buscar marcadores plaintext en filas, respuestas, auditoría y logs.
- Alterar ciphertext y comprobar que AES-GCM rechaza la autenticación.
- Cifrar con dos versiones y verificar descifrado histórico.
- Confirmar duplicado de identidad dentro de una organización sin revelar la cédula.
- Probar actor permitido y otra organización denegada.

## Retención

La eliminación o anonimización seguirá la matriz de retención aprobada. Retirar claves como mecanismo de destrucción criptográfica requiere aprobación específica porque también afecta backups e investigación financiera.
