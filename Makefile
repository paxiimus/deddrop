.DEFAULT_GOAL := help
COMPOSE := docker compose

# Bold amber/orange approximations of the brand accent — standard 8-color
# codes, not 256-color, so this stays legible on basically any terminal
# rather than degrading badly on the ones that don't support truecolor.
ACCENT := \033[1;33m
DIM    := \033[0;90m
RESET  := \033[0m

.PHONY: help
help:
	@printf "$(ACCENT)"
	@echo "        |"
	@echo "   ---- o ----   DEAD DROP"
	@echo "        |"
	@printf "$(RESET)"
	@echo ""
	@printf "$(DIM)Development$(RESET)\n"
	@echo "  make up             Start the full containerized stack (db, redis, api, celery, web)"
	@echo "  make down           Stop everything"
	@echo "  make build          Rebuild all images"
	@echo "  make restart        Restart all services"
	@echo "  make logs           Tail logs for everything"
	@echo "  make logs-api       Tail API logs only"
	@echo "  make ps             Show service status"
	@echo "  make web-dev        Run the frontend dev server locally (hot reload — not Docker)"
	@echo "  make web-install    npm install in web/"
	@echo ""
	@printf "$(DIM)Maintenance$(RESET)\n"
	@echo "  make migrate        Apply database migrations"
	@echo "  make makemigrations Generate new migrations from model changes"
	@echo "  make superuser      Create a Django admin superuser (interactive)"
	@echo "  make shell          Open a Django shell"
	@echo "  make dbshell        Open a psql shell on the database"
	@echo "  make check          Run Django's system checks"
	@echo "  make verify         Static cross-reference + migration-drift check (no Django needed to run it)"
	@echo "  make backup         Dump the database to backups/"
	@echo "  make restore FILE=backups/xxx.sql   Restore a database dump"
	@echo ""
	@printf "$(DIM)Deployment$(RESET)\n"
	@echo "  make secrets            Generate a fresh DJANGO_SECRET_KEY + DB_PASSWORD pair"
	@echo "  make check-placeholders Scan for unfilled placeholder values before a real launch"
	@echo "  make deploy             git pull, rebuild, migrate, restart (assumes a git-based server checkout)"
	@echo "  See DEPLOYING.md for a full walkthrough (single instance, public or private)."
	@echo ""
	@printf "$(DIM)Danger zone$(RESET)\n"
	@echo "  make clean          Stop everything and DELETE all volumes (database, media). Asks first."

# --- Development -------------------------------------------------------

.PHONY: up
up:
	$(COMPOSE) up -d --build

.PHONY: down
down:
	$(COMPOSE) down

.PHONY: build
build:
	$(COMPOSE) build

.PHONY: restart
restart:
	$(COMPOSE) restart

.PHONY: logs
logs:
	$(COMPOSE) logs -f

.PHONY: logs-api
logs-api:
	$(COMPOSE) logs -f api

.PHONY: ps
ps:
	$(COMPOSE) ps

.PHONY: web-dev
web-dev:
	cd web && npm run dev

.PHONY: web-install
web-install:
	cd web && npm install

# --- Maintenance ---------------------------------------------------------

.PHONY: migrate
migrate:
	$(COMPOSE) exec api python manage.py migrate

.PHONY: makemigrations
makemigrations:
	$(COMPOSE) exec api python manage.py makemigrations

.PHONY: superuser
superuser:
	$(COMPOSE) exec api python manage.py createsuperuser

.PHONY: shell
shell:
	$(COMPOSE) exec api python manage.py shell

.PHONY: dbshell
dbshell:
	$(COMPOSE) exec db psql -U deaddrop -d deaddrop

.PHONY: check
check:
	$(COMPOSE) exec api python manage.py check

.PHONY: backup
backup:
	mkdir -p backups
	$(COMPOSE) exec -T db pg_dump --clean --if-exists -U deaddrop deaddrop > backups/deaddrop-$$(date +%Y%m%d-%H%M%S).sql
	@echo "Backed up to backups/"

.PHONY: restore
restore:
	@test -n "$(FILE)" || (echo "Usage: make restore FILE=backups/xxx.sql" && exit 1)
	$(COMPOSE) exec -T db psql -U deaddrop -d deaddrop < $(FILE)

# --- Deployment ----------------------------------------------------------

.PHONY: verify
verify:
	python3 api/scripts/verify.py

.PHONY: secrets
secrets:
	@echo "DJANGO_SECRET_KEY=$$(openssl rand -base64 50 | tr -d '\n')"
	@echo "DB_PASSWORD=$$(openssl rand -hex 24)"
	@echo ""
	@echo "Paste both into .env. Never reuse a pair across two deployments —"
	@echo "a leaked secret on one instance should never be useful against another."
	@echo ""
	@echo "Only if you want a private-deployment access gate (see README.md's"
	@echo "\"The private-access gate\" section) — otherwise leave PRIVATE_ACCESS_CODE"
	@echo "blank in .env, which is the default, and this is never used at all:"
	@echo "PRIVATE_ACCESS_CODE=$$(openssl rand -hex 16)"

.PHONY: check-placeholders
check-placeholders:
	@echo "Scanning for unfilled placeholder values..."
	@FOUND=0; \
	if [ -f LICENSE ]; then echo "  [ok]   LICENSE present"; else echo "  [MISSING] LICENSE — see README.md for how to get it"; FOUND=1; fi; \
	if [ -f .env ]; then \
		if grep -qE "change_me|change-me-in-production" .env; then \
			echo "  [UNSET] .env still has the unsafe default DJANGO_SECRET_KEY or DB_PASSWORD — run 'make secrets'"; FOUND=1; \
		fi; \
		if grep -qE "^(DJANGO_SECRET_KEY|DB_PASSWORD)=[[:space:]]*$$" .env; then \
			echo "  [UNSET] .env has DJANGO_SECRET_KEY or DB_PASSWORD left blank (the actual .env.example default) — run 'make secrets' and paste the output in"; FOUND=1; \
		fi; \
	fi; \
	for f in README.md SECURITY.md web/index.html; do \
		if [ -f "$$f" ] && grep -qE "your-domain\.example|<your |\[your-domain\]" "$$f"; then \
			echo "  [TODO]  $$f still has an unfilled placeholder — grep it for 'your-domain.example' / '<your ' / '[your-domain]'"; FOUND=1; \
		fi; \
	done; \
	if [ "$$FOUND" = "0" ]; then echo "  Nothing found — but this only catches known placeholder patterns, not everything worth checking before launch."; fi

.PHONY: deploy
deploy:
	git pull
	$(COMPOSE) build
	$(COMPOSE) up -d
	$(COMPOSE) exec api python manage.py migrate

# --- Danger zone -----------------------------------------------------

.PHONY: clean
clean:
	@echo "This stops everything and DELETES all volumes — database and uploaded photos included."
	@echo "Ctrl+C now to cancel. Continuing in 5 seconds..."
	@sleep 5
	$(COMPOSE) down -v
