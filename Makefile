include .env
export

HOST ?= localhost
PORT ?= 8010
BASE := http://$(HOST):$(PORT)
PRETTY := python3 -m json.tool
V2_RECEIVED_AMOUNT ?= $(V2_HELP_AMOUNT)
V2_RECEIVED_CURRENCY ?= $(V2_HELP_CURRENCY)
V2_EXPECTED_RATE_SOURCE ?= DOLARAPI-BCV

.DEFAULT_GOAL := help

.PHONY: help up down build restart logs ps health notifications \
	postman-test postman-test-yo-te-ayudo-public postman-test-yo-te-ayudo-protegido postman-test-yo-te-ayudo-donante postman-test-bcv-manual test-no-auth test-solicitudes test-aprobar test-rechazar test-nuevo-centro test-admin-dual \
	backup-help-v2 backup-help-v2-host migrate-dry-run migrate-apply migrate-apply-host postman-test-spec004-fase1 \
	postman-test-spec004-fase2-salud postman-test-spec004-fase2-empleo postman-test-spec004-fase2-campana

help: ## Muestra esta ayuda
	@grep -E '^[a-zA-Z0-9_-]+:.*## ' Makefile | sort | awk 'BEGIN {FS = ":.*## "}; {printf "\033[36m%-22s\033[0m %s\n", $$1, $$2}'

## --- Docker ---

up: ## Levanta los servicios (build + detached)
	docker compose up -d --build

down: ## Detiene y elimina los contenedores
	docker compose down

restart: down up ## Reinicia todo (down + up)

build: ## Reconstruye la imagen sin levantar contenedores
	docker compose build

logs: ## Sigue los logs de la API en vivo
	docker compose logs -f rescate-api

ps: ## Estado de los contenedores
	docker compose ps

health: ## Chequea GET /health
	curl -sf $(BASE)/health && echo " OK"

notifications: ## Procesa un lote de la outbox de ayuda mediante Resend
	docker compose run --rm rescate-api python -m scripts.help_notifications --limit 25

postman-test: ## Ejecuta la suite automatizada de API con Newman
	@npx --yes newman run postman/ventatalk-rescate-api-tests.postman_collection.json \
		--env-var base_url=$(BASE) \
		--env-var api_key="$(API_KEY)"

postman-test-yo-te-ayudo-public: ## Ejecuta las lecturas públicas V2 de Yo te ayudo
	@npx --yes newman run postman/ventatalk-rescate-api-tests.postman_collection.json \
		--folder "04 - Yo te ayudo V2 publico" \
		--env-var base_url=$(BASE)

postman-test-yo-te-ayudo-protegido: ## Ejecuta protección y listado HMAC V2
	@npx --yes newman run postman/ventatalk-rescate-api-tests.postman_collection.json \
		--folder "05 - Yo te ayudo V2 protegido" \
		--env-var base_url=$(BASE) \
		--env-var api_key="$(API_KEY)" \
		--env-var actor_signing_secret="$(ACTOR_SIGNING_SECRET)" \
		--env-var v2_organization_id="$(V2_ORGANIZATION_ID)" \
		--env-var v2_actor_email="$(V2_ACTOR_EMAIL)"

postman-test-yo-te-ayudo-donante: ## Ejecuta reporte y confirmación V2 sobre un caso publicado existente
	@npx --yes newman run postman/ventatalk-rescate-api-tests.postman_collection.json \
		--folder "06 - Yo te ayudo V2 donante y confirmacion" \
		--env-var base_url=$(BASE) \
		--env-var api_key="$(API_KEY)" \
		--env-var actor_signing_secret="$(ACTOR_SIGNING_SECRET)" \
		--env-var v2_organization_id="$(V2_ORGANIZATION_ID)" \
		--env-var v2_actor_email="$(V2_ACTOR_EMAIL)" \
		--env-var v2_actor_uid="$(V2_ACTOR_UID)" \
		--env-var v2_donor_email="$(V2_DONOR_EMAIL)" \
		--env-var v2_donor_uid="$(V2_DONOR_UID)" \
		--env-var v2_public_id="$(V2_PUBLIC_ID)" \
		--env-var v2_case_id="$(V2_CASE_ID)" \
		--env-var v2_help_amount="$(V2_HELP_AMOUNT)" \
		--env-var v2_help_currency="$(V2_HELP_CURRENCY)" \
		--env-var v2_received_amount="$(V2_RECEIVED_AMOUNT)" \
		--env-var v2_received_currency="$(V2_RECEIVED_CURRENCY)" \
		--env-var v2_expected_rate_source="$(V2_EXPECTED_RATE_SOURCE)"

## --- Migraciones locales (spec 004 Fase 1, retencion, y futuras) ---

SCRIPT ?= migrate_help_spec004

backup-help-v2: ## Backup cifrado local de Help V2 antes de migrar (requiere HELP_BACKUP_PASSPHRASE en el entorno)
	@mkdir -p uploads/backups
	docker compose run --rm rescate-api python -m scripts.help_backup \
		--output /app/uploads/backups/help-v2-$$(date +%Y%m%d-%H%M%S).backup.enc

backup-help-v2-host: ## Alternativa si backup-help-v2 falla con "Backup requires a local database source": corre fuera de Docker contra LOCAL_DATABASE_URL (host=localhost). Ej: make backup-help-v2-host LOCAL_DATABASE_URL=postgresql://user:pass@localhost:5432/db (requiere pg_dump instalado localmente)
	@mkdir -p uploads/backups
	DATABASE_URL="$(LOCAL_DATABASE_URL)" .venv/bin/python -m scripts.help_backup \
		--output uploads/backups/help-v2-$$(date +%Y%m%d-%H%M%S).backup.enc

migrate-dry-run: ## Dry-run de una migracion, no muta nada: make migrate-dry-run SCRIPT=migrate_help_spec004
	docker compose run --rm rescate-api python -m scripts.$(SCRIPT)

migrate-apply: ## Aplica una migracion dentro de la red de Docker (destructivo segun el script): make migrate-apply SCRIPT=migrate_help_spec004
	docker compose run --rm rescate-api python -m scripts.$(SCRIPT) --apply --local-test-marker DISPOSABLE_LOCAL_TEST

migrate-apply-host: ## Alternativa si migrate-apply falla con "requires a local database target": corre fuera de Docker contra LOCAL_DATABASE_URL (host=localhost). Ej: make migrate-apply-host SCRIPT=migrate_help_spec004 LOCAL_DATABASE_URL=postgresql://user:pass@localhost:5432/db
	DATABASE_URL="$(LOCAL_DATABASE_URL)" .venv/bin/python -m scripts.$(SCRIPT) --apply --local-test-marker DISPOSABLE_LOCAL_TEST

postman-test-spec004-fase1: ## Ejecuta borradores idempotentes y coincidencias de cedula de la Fase 1 (spec 004)
	@npx --yes newman run postman/ventatalk-rescate-api-tests.postman_collection.json \
		--folder "08 - Spec 004 Fase 1 (borradores y coincidencias)" \
		--env-var base_url=$(BASE) \
		--env-var api_key="$(API_KEY)" \
		--env-var actor_signing_secret="$(ACTOR_SIGNING_SECRET)" \
		--env-var v2_organization_id="$(V2_ORGANIZATION_ID)" \
		--env-var v2_actor_email="$(V2_ACTOR_EMAIL)"

postman-test-spec004-fase2-salud: ## Ciclo completo persona/salud de la Fase 2 (spec 004)
	@npx --yes newman run postman/ventatalk-rescate-api-tests.postman_collection.json \
		--folder "09 - Spec 004 Fase 2 (persona salud - ciclo completo)" \
		--env-var base_url=$(BASE) \
		--env-var api_key="$(API_KEY)" \
		--env-var actor_signing_secret="$(ACTOR_SIGNING_SECRET)" \
		--env-var v2_organization_id="$(V2_ORGANIZATION_ID)" \
		--env-var v2_actor_email="$(V2_ACTOR_EMAIL)"

postman-test-spec004-fase2-empleo: ## Ciclo completo persona/empleo de la Fase 2 (spec 004)
	@npx --yes newman run postman/ventatalk-rescate-api-tests.postman_collection.json \
		--folder "10 - Spec 004 Fase 2 (persona empleo - ciclo completo)" \
		--env-var base_url=$(BASE) \
		--env-var api_key="$(API_KEY)" \
		--env-var actor_signing_secret="$(ACTOR_SIGNING_SECRET)" \
		--env-var v2_organization_id="$(V2_ORGANIZATION_ID)" \
		--env-var v2_actor_email="$(V2_ACTOR_EMAIL)"

postman-test-spec004-fase2-campana: ## Ciclo completo campana de organizacion de la Fase 2 (spec 004)
	@npx --yes newman run postman/ventatalk-rescate-api-tests.postman_collection.json \
		--folder "11 - Spec 004 Fase 2 (campana organizacion - ciclo completo)" \
		--env-var base_url=$(BASE) \
		--env-var api_key="$(API_KEY)" \
		--env-var actor_signing_secret="$(ACTOR_SIGNING_SECRET)" \
		--env-var v2_organization_id="$(V2_ORGANIZATION_ID)" \
		--env-var v2_actor_email="$(V2_ACTOR_EMAIL)"

postman-test-bcv-manual: ## Registra una tasa manual de contingencia como super_admin
	@npx --yes newman run postman/ventatalk-rescate-api-tests.postman_collection.json \
		--folder "07 - Contingencia manual BCV" \
		--env-var base_url=$(BASE) \
		--env-var api_key="$(API_KEY)" \
		--env-var actor_signing_secret="$(ACTOR_SIGNING_SECRET)" \
		--env-var v2_organization_id="$(V2_ORGANIZATION_ID)" \
		--env-var v2_actor_email="$(V2_ACTOR_EMAIL)" \
		--env-var v2_actor_uid="$(V2_ACTOR_UID)" \
		--env-var v2_super_admin_email="$(V2_SUPER_ADMIN_EMAIL)" \
		--env-var v2_super_admin_uid="$(V2_SUPER_ADMIN_UID)" \
		--env-var v2_manual_rate_date="$(V2_MANUAL_RATE_DATE)" \
		--env-var v2_manual_rate_source_currency="$(V2_MANUAL_RATE_SOURCE_CURRENCY)" \
		--env-var v2_manual_rate_target_currency="$(V2_MANUAL_RATE_TARGET_CURRENCY)" \
		--env-var v2_manual_rate_value="$(V2_MANUAL_RATE_VALUE)" \
		--env-var v2_manual_rate_reference="$(V2_MANUAL_RATE_REFERENCE)" \
		--env-var v2_manual_rate_reason="$(V2_MANUAL_RATE_REASON)"
