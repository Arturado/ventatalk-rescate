include .env
export

HOST ?= localhost
PORT ?= 8010
BASE := http://$(HOST):$(PORT)
PRETTY := python3 -m json.tool

.DEFAULT_GOAL := help

.PHONY: help up down build restart logs ps health \
	test-no-auth test-solicitudes test-aprobar test-rechazar test-nuevo-centro test-admin-dual

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