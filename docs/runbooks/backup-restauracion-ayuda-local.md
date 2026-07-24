# Backup y restauracion local de Yo te ayudo

Estado: aprobado solo para simulacros locales descartables
Version: 1.0-local
Fecha: 2026-07-23

## Objetivos provisionales

- RPO local: 24 horas.
- RTO local: 4 horas.
- Estos objetivos no son un compromiso de produccion.

## Contenido y formato

El backup contiene un dump custom de PostgreSQL, el arbol de `HELP_PRIVATE_UPLOAD_ROOT`, manifiesto con checksums, versiones de herramientas, identificador de esquema y versiones de claves. Nunca contiene material de claves. Opcionalmente acepta un export de emulador Firestore que incluya `firebase-export-metadata.json`; no implementa ni permite exportar Firestore de produccion.

El contenedor completo se cifra y autentica con AES-256-GCM. La clave se deriva con scrypt de `HELP_BACKUP_PASSPHRASE`, suministrada solo en el entorno del proceso, o de entrada interactiva. No existe modo de salida sin cifrar. El dump se transporta por pipes y no se escribe en claro en el directorio de trabajo.

## Backup local

La URL PostgreSQL, raiz privada y claves de datos deben existir en el entorno de ejecucion. El destino debe ser nuevo; el comando no sobrescribe artefactos.

```bash
python3 -m scripts.help_backup --output /ruta/local-autorizada/help-v2.backup.enc
```

Export opcional de emulador, nunca de produccion:

```bash
python3 -m scripts.help_backup \
  --output /ruta/local-autorizada/help-v2.backup.enc \
  --firestore-emulator-root /ruta/al/export-del-emulador
```

## Restauracion local

Crear fuera de estos scripts una base nueva cuyo nombre termine exactamente en `_test` y una raiz existente y vacia. El restore rechaza bases con tablas, raices no vacias, passphrase incorrecta, corrupcion, checksums invalidos, rutas inseguras, symlinks y versiones de claves de datos ausentes.

```bash
python3 -m scripts.help_restore \
  --artifact /ruta/local-autorizada/help-v2.backup.enc \
  --destination-root /ruta/vacia/restore
```

Despues de restaurar:

1. Comparar conteos por tabla, montos y numero de auditorias contra la evidencia sanitizada del backup.
2. Comparar checksums de archivos cifrados y descifrar una muestra sintetica con la version de clave declarada.
3. Probar ausencia de la clave y confirmar fallo cerrado.
4. Ejecutar `python3 -m scripts.help_retention` y revisar solo agregados.
5. Aplicar exclusivamente sobre `_test` con `python3 -m scripts.help_retention --apply` y volver a ejecutar para demostrar cero mutaciones.

## Firestore emulator

El archivo opcional es solo una copia de un export previamente generado por Firebase Emulator Suite. La restauracion de ese subarbol es manual y local; este script no llama `firebase firestore:delete`, Admin SDK, `gcloud` ni APIs de produccion.

## Bloqueos antes de produccion

- Proveedor administrado de PostgreSQL: PITR, snapshots, cifrado, region, replica y prueba de restauracion no evaluados.
- IAM: roles separados, doble control, MFA, cuentas de servicio, acceso de emergencia y auditoria no definidos.
- Storage: versionado, object lock, lifecycle, replicacion, KMS/HSM, borrado de backups y capacidad no definidos.
- Claves: custodia, recuperacion, rotacion coordinada y acceso durante desastre no validados.
- Operacion: scheduler, alertas de fallo, inventario, off-site y evidencia periodica no implementados.
- Legal/privacidad: plazos, legal hold, solicitudes de titulares y destruccion a siete anos pendientes de revision jurisdiccional.
- Firestore real: no existe procedimiento aprobado de export/restore de produccion en este corte.

CA-41 solo puede apoyarse en un simulacro local fresco con datos sinteticos. No demuestra RPO/RTO, permisos, cifrado, restaurabilidad ni retencion de la infraestructura de produccion.
