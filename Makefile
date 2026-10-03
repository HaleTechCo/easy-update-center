# Local build and test entry points (see CONTRIBUTING.md#tests). Needs Docker with Compose v2 and GNU make.
export MSYS_NO_PATHCONV := 1

UC_IMAGE ?= jenkins-uc/uc:local
UC_TEST_IMAGE ?= jenkins-uc/uc-test:local
WEB_IMAGE ?= jenkins-uc/web:local
export UC_IMAGE UC_TEST_IMAGE WEB_IMAGE

# Unit tests read the Python, templates and lint tools straight from the working tree
# (VersionTool.java is compiled into the image, so changing it needs `make build-uc`).
SRC_MOUNTS = -v "$(CURDIR)/tests:/tests:ro" \
             -v "$(CURDIR)/uc/uc:/opt/uc/python/uc:ro" \
             -v "$(CURDIR)/uc/templates:/opt/uc/templates:ro" \
             -v "$(CURDIR)/tools:/tools:ro"              -v "$(CURDIR)/config/uc.example.toml:/examples/uc.example.toml:ro"              -v "$(CURDIR)/.env.online.example:/examples/env.online.example:ro"              -v "$(CURDIR)/.env.airgapped.example:/examples/env.airgapped.example:ro"

.PHONY: build build-uc build-web uc-image test test-unit test-web test-routing test-secrets pki-init pki-rotate secrets-placeholders secrets-permissions examples reload procure-download procure-clean procure-push compose-check it-airgapped it-clean it-online it-full upstream-diff

build: build-uc build-web

build-uc:
	docker build --target runtime -t $(UC_IMAGE) uc
	docker build --target test -t $(UC_TEST_IMAGE) uc

build-web:
	docker build -t $(WEB_IMAGE) web

# The uc image, built only when it is missing: the targets that merely run it must also work on a
# host that loaded pre-built images and has no internet access.
uc-image:
	@docker image inspect $(UC_IMAGE) >/dev/null 2>&1 || docker build --target runtime -t $(UC_IMAGE) uc

test: test-unit test-web test-routing test-secrets

test-unit: build-uc
	docker run --rm $(SRC_MOUNTS) $(UC_TEST_IMAGE) -q /tests/unit $(ARGS)

test-web: build-web
	bash tests/web/entrypoint-checks.sh $(WEB_IMAGE)

ROUTING = docker compose -f tests/routing/compose.yaml

test-routing: build-uc build-web
	$(ROUTING) run --rm tester $(ARGS); rc=$$?; \
	  $(ROUTING) down -v --remove-orphans >/dev/null 2>&1; exit $$rc

# Ownership is real only on a Linux filesystem, so the checks run inside the uc image as root.
test-secrets: uc-image
	docker run --rm --user 0:0 --entrypoint bash -v "$(CURDIR)/scripts:/scripts:ro" \
	  -v "$(CURDIR)/tests/secrets:/tests:ro" $(UC_IMAGE) /tests/permissions-checks.sh

# secrets/ stays yours; the containers read their files through their group (uc 10001, web 33).
# scripts/secrets-permissions.sh sets that up, as root in the uc image, after every target that
# writes to secrets/. Run `make secrets-permissions` yourself after replacing a file in secrets/.
HOST_UID ?= $(shell id -u)
HOST_GID ?= $(shell id -g)
# It also gets .env (when there is one) and config/, so uc pki reads the PKI_* settings the services do.
IN_SECRETS = docker run --rm --user 0:0 --entrypoint sh $(if $(wildcard .env),--env-file .env) -v "$(CURDIR)/config:/config:ro" -v "$(CURDIR)/secrets:/pki" \
  -v "$(CURDIR)/scripts/secrets-permissions.sh:/usr/local/bin/secrets-permissions.sh:ro"
FIX_SECRETS = sh /usr/local/bin/secrets-permissions.sh /pki $(HOST_UID) $(HOST_GID)
SECRET_FILES = repo_username repo_password github_username github_token download_proxy_auth repo_push_auth

# Creates the root CA and signing certificate in ./secrets. Refuses to overwrite existing files.
pki-init: uc-image
	@mkdir -p secrets
	$(IN_SECRETS) $(UC_IMAGE) -c 'uc pki init --out /pki && $(FIX_SECRETS)'

# Usage: make pki-rotate ROOT=<directory holding root-ca.key>, mounted read-only. Issues a new
# signing certificate and keeps the previous pair as *.old.
pki-rotate: uc-image
	@test -n "$(ROOT)" || { echo "usage: make pki-rotate ROOT=<directory holding root-ca.key>"; exit 2; }
	$(IN_SECRETS) -v "$(abspath $(ROOT)):/pki/root:ro" $(UC_IMAGE) -c 'uc pki rotate --dir /pki && $(FIX_SECRETS)'
	@rmdir secrets/root 2>/dev/null || true

secrets-placeholders: uc-image
	@mkdir -p secrets
	@$(IN_SECRETS) $(UC_IMAGE) -c 'for f in $(SECRET_FILES); do [ -e /pki/$$f ] || : > /pki/$$f; done; $(FIX_SECRETS)'
	@echo "secrets/: created any missing credential files (empty means 'not set')"

secrets-permissions: uc-image
	@mkdir -p secrets
	@$(IN_SECRETS) $(UC_IMAGE) -c '$(FIX_SECRETS)'
	@echo "secrets/: yours; the containers read their files through their group"

# The example settings files are generated from the settings registry (uc/uc/settings.py), using the
# working tree's code; test-unit fails while a committed copy is out of date.
EXAMPLE = docker run --rm -v "$(CURDIR)/uc/uc:/opt/uc/python/uc:ro" $(UC_IMAGE) config --example

examples: uc-image
	$(EXAMPLE) toml --mode online > config/uc.example.toml
	$(EXAMPLE) env --mode online > .env.online.example
	$(EXAMPLE) env --mode airgapped > .env.airgapped.example

# Check the settings, then recreate every service so it reads the current .env and config/uc.toml
# (docs/reference/configuration.md#when-changes-apply). A failed check stops here, leaving every
# service running as it was; web-config runs the check, needing no network and no secrets.
# A generation in progress restarts.
reload:
	docker compose run --rm web-config config --check
	docker compose up -d --force-recreate

# uc doctor --download-* stages files into ./procurement for review, and uc doctor --clean deletes the
# ones the repository already holds (docs/how-to/procure-missing-files.md). The uc container runs as
# uid 10001: it gets the folder for the run, and the host user gets it back after, also when the run
# fails. Usage: make procure-download ARGS="--fetch --download-poms --download-dependencies"
#               make procure-clean [ARGS=--dry-run]
PROCUREMENT = $(CURDIR)/procurement
IN_PROCUREMENT = docker run --rm --user 0:0 --entrypoint sh -v "$(PROCUREMENT):/procurement" $(UC_IMAGE) -c

procure-clean: DOCTOR_MODE = --clean
procure-download procure-clean: uc-image
	@mkdir -p procurement
	@$(IN_PROCUREMENT) 'chown -R 10001:10001 /procurement'
	docker compose run --rm uc doctor $(DOCTOR_MODE) $(ARGS); rc=$$?; \
	  $(IN_PROCUREMENT) 'chown -R $(HOST_UID):$(HOST_GID) /procurement'; exit $$rc

# Usage: make procure-push [ARGS=--dry-run]
procure-push:
	docker compose run --rm uc doctor --push $(ARGS)

# UC_ENV_FILE selects the services' env_file; --env-file only feeds compose's own interpolation.
# check_isolation.py asserts the network and secret boundaries the repo-adapter relies on.
ISOLATION = docker run -i --rm -v "$(CURDIR)/tests:/tests:ro" --entrypoint python3 $(UC_TEST_IMAGE) \
  /tests/compose/check_isolation.py

compose-check: build-uc
	UC_ENV_FILE=.env.online.example docker compose --env-file .env.online.example config --quiet
	UC_ENV_FILE=.env.airgapped.example docker compose --env-file .env.airgapped.example \
	  -f compose.yaml -f compose.airgapped.yaml config --quiet
	UC_ENV_FILE=.env.online.example docker compose --env-file .env.online.example --profile tools \
	  config --format json | $(ISOLATION) online
	UC_ENV_FILE=.env.airgapped.example docker compose --env-file .env.airgapped.example \
	  -f compose.yaml -f compose.airgapped.yaml --profile tools config --format json | $(ISOLATION) airgapped

IT_DIR = tests/integration
IT_AIR = docker compose -f $(IT_DIR)/airgapped.yaml --env-file $(IT_DIR)/.versions.env
# Each repository type gets a fresh update center (its data volumes) against the same seeded repository.
IT_TYPES ?= artifactory artifact-keeper
IT_STATE = uc-it-airgapped_uc-data uc-it-airgapped_uc-data-proxy uc-it-airgapped_uc-data-age uc-it-airgapped_uc-data-push uc-it-airgapped_push-repo uc-it-airgapped_web-config

it-airgapped: build
	docker compose -f $(IT_DIR)/airgapped.yaml --profile seed run --rm -T seed-versions > $(IT_DIR)/.versions.env
	cat $(IT_DIR)/.versions.env
	$(IT_AIR) --profile seed run --rm seed-plugins
	$(IT_AIR) --profile seed run --rm seed-repo
	$(IT_AIR) --profile seed run --rm pki
	rc=0; for type in $(IT_TYPES); do \
	  echo "== air-gapped run against $$type"; \
	  env="--env-file $(IT_DIR)/types/$$type.env"; \
	  $(IT_AIR) $$env rm --stop --force >/dev/null 2>&1; docker volume rm -f $(IT_STATE) >/dev/null; \
	  $(IT_AIR) $$env --profile seed run --rm push-init && $(IT_AIR) $$env run --rm driver $(ARGS) && $(IT_AIR) $$env run --rm plugin-cli || { rc=1; break; }; \
	done; \
	$(IT_AIR) --profile seed down -v --remove-orphans >/dev/null 2>&1; exit $$rc

it-clean:
	docker compose -f $(IT_DIR)/airgapped.yaml --profile seed down -v --remove-orphans
	# Compose interpolates online.yaml's required (:?) vars even for `down`, which never starts
	# the pki service that actually needs them, so harmless placeholders are enough here.
	JENKINS_COMMUNITY_USERNAME=unused JENKINS_COMMUNITY_PASSWORD=unused \
	  docker compose -f $(IT_DIR)/online.yaml down -v --remove-orphans

IT_ONLINE = docker compose -f $(IT_DIR)/online.yaml

it-online: build
	@test -n "$$JENKINS_COMMUNITY_USERNAME" -a -n "$$JENKINS_COMMUNITY_PASSWORD" || \
	  { echo "export JENKINS_COMMUNITY_USERNAME and JENKINS_COMMUNITY_PASSWORD (a Jenkins community account for repo.jenkins-ci.org; test-only, a deployment reads secrets/repo_*)"; exit 2; }
	@mkdir -p $(IT_DIR)/.results
	$(IT_ONLINE) run --rm pki
	$(IT_ONLINE) run --rm driver $(ARGS); rc=$$?; \
	  $(IT_ONLINE) down -v --remove-orphans >/dev/null 2>&1; exit $$rc

# Full catalog, no plugin cap: records duration, cache and site size
# (docs/getting-started/requirements.md#host-sizing). Takes hours on first run.
it-full:
	$(MAKE) it-online UC_EXTRA_ARGS= JAVA_TOOL_OPTIONS=-Xmx6g

UC2_VERSION ?= 3.18.4
UPSTREAM_FILES = site/generate.sh site/generate-htaccess.sh site/test/test.sh \
  src/main/java/io/jenkins/update_center/Main.java \
  src/main/java/io/jenkins/update_center/DirectoryTreeBuilder.java \
  src/main/java/io/jenkins/update_center/MetadataWriter.java \
  src/main/java/io/jenkins/update_center/Signer.java \
  src/main/java/io/jenkins/update_center/Popularities.java \
  src/main/java/io/jenkins/update_center/ArtifactoryRepositoryImpl.java \
  src/main/java/io/jenkins/update_center/MavenArtifact.java \
  src/main/java/io/jenkins/update_center/HPI.java \
  src/main/java/io/jenkins/update_center/json/TieredUpdateSitesGenerator.java

# Usage: make upstream-diff TO=3.19   (compares the pinned UC2_VERSION with TO; see docs/development/upgrading-upstream.md)
upstream-diff: build-uc
	@test -n "$(TO)" || { echo "usage: make upstream-diff TO=<update-center2 version>"; exit 2; }
	docker run --rm --entrypoint sh -v "$(CURDIR)/uc/patches:/patches:ro" $(UC_TEST_IMAGE) -c '\
	  set -e; git clone -q https://github.com/jenkins-infra/update-center2.git /tmp/uc2; cd /tmp/uc2; \
	  echo "== files changed between $(UC2_VERSION) and $(TO)"; \
	  git diff --stat update-center2-$(UC2_VERSION) update-center2-$(TO); \
	  echo "== files uc depends on"; \
	  git diff update-center2-$(UC2_VERSION) update-center2-$(TO) -- $(UPSTREAM_FILES); \
	  git checkout -q update-center2-$(TO); \
	  if git apply --check /patches/*.patch 2>/dev/null; then echo "== patches apply to $(TO)"; \
	  else echo "== PATCHES DO NOT APPLY to $(TO); check whether upstream already reads POPULARITY_JSON_URL (Popularities.java) and CORE_AGE_DAYS (TieredUpdateSitesGenerator.java)"; fi'
