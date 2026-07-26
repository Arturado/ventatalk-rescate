# ADR-001: Cifrado de datos de casos de ayuda

Estado: aprobado para implementación local
Propietario: equipo de plataforma Rescate
Versión: 1.0
Fecha: 2026-07-20
Clasificación: interna

## Contexto

Los casos V2 almacenan identidad, contacto, relatos, cuentas y evidencias cuya exposición puede causar daño. La base de datos, backups, logs y respuestas deben minimizar el impacto de una filtración sin impedir búsquedas controladas ni rotación de claves.

## Decisión

- Cifrar campos sensibles en Ventatalk con AES-256-GCM y nonce aleatorio por valor.
- Incluir la versión de clave en el sobre `enc:<version>:<base64>` y autenticar el nombre lógico del campo como AAD.
- Calcular índices de identidad con HMAC-SHA-256 y una clave independiente; nunca usar SHA-256 simple sobre cédulas.
- Mantener un llavero de descifrado y una única versión activa para escrituras nuevas.
- Resolver organización y actor en servidor. React y Firebase Functions nunca reciben claves de datos.
- No registrar plaintext, claves, ciphertext completo ni índices de identidad en auditoría o logs.

## Alternativas descartadas

- Cifrado en React: expone claves y lógica a clientes no confiables.
- Cifrado solo de disco/base: no protege frente a consultas o backups con credenciales válidas.
- Hash simple de cédula: el espacio de valores es enumerable.
- Sobrescribir una clave al rotar: vuelve irrecuperables los registros históricos.

## Consecuencias

- Perder una clave impide recuperar los valores cifrados con ella.
- La rotación HMAC requiere ventana controlada y reindexación completa.
- Los secretos deben respaldarse separadamente y gestionarse fuera de Git.
- Consultas públicas y operativas usan esquemas de lista permitida, no modelos ORM completos.

## Referencias

- `../security/proteccion-datos-ayuda.md`
- `../runbooks/rotacion-claves-ayuda.md`
- `../runbooks/compromiso-claves-ayuda.md`
