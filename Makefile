include .env
export

HOST ?= localhost
PORT ?= 8010
BASE := http://$(HOST):$(PORT)
PRETTY := python3 -m json.tool

.DEFAULT_GOAL := help

.PHONY: help up down build restart logs ps health \
	postman-test postman-test-casos-ayuda postman-test-yo-te-ayudo-public postman-test-yo-te-ayudo-protegido postman-test-yo-te-ayudo-donante postman-test-bcv-manual test-no-auth test-solicitudes test-aprobar test-rechazar test-nuevo-centro test-admin-dual

help: ## Muestra esta ayuda
	@grep -E '^[a-zA-Z_-]+:.*## ' Makefile | sort | awk 'BEGIN {FS = ":.*## "}; {printf "\033[36m%-22s\033[0m %s\n", $$1, $$2}'

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

postman-test: ## Ejecuta la suite automatizada de API con Newman
	@npx --yes newman run postman/ventatalk-rescate-api-tests.postman_collection.json \
		--env-var base_url=$(BASE) \
		--env-var api_key="$(API_KEY)"

postman-test-casos-ayuda: ## Ejecuta solo la integración de casos de ayuda
	@npx --yes newman run postman/ventatalk-rescate-api-tests.postman_collection.json \
		--folder "03 - Casos de ayuda" \
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
		--env-var v2_help_currency="$(V2_HELP_CURRENCY)"

postman-test-bcv-manual: ## Registra una tasa manual de contingencia como super_admin
	@npx --yes newman run postman/ventatalk-rescate-api-tests.postman_collection.json \
		--folder "07 - Contingencia manual BCV" \
		--env-var base_url=$(BASE) \
		--env-var api_key="$(API_KEY)" \
		--env-var actor_signing_secret="$(ACTOR_SIGNING_SECRET)" \
		--env-var v2_super_admin_email="$(V2_SUPER_ADMIN_EMAIL)" \
		--env-var v2_super_admin_uid="$(V2_SUPER_ADMIN_UID)" \
		--env-var v2_manual_rate_date="$(V2_MANUAL_RATE_DATE)" \
		--env-var v2_manual_rate_source_currency="$(V2_MANUAL_RATE_SOURCE_CURRENCY)" \
		--env-var v2_manual_rate_target_currency="$(V2_MANUAL_RATE_TARGET_CURRENCY)" \
		--env-var v2_manual_rate_value="$(V2_MANUAL_RATE_VALUE)" \
		--env-var v2_manual_rate_reference="$(V2_MANUAL_RATE_REFERENCE)" \
		--env-var v2_manual_rate_reason="$(V2_MANUAL_RATE_REASON)"
