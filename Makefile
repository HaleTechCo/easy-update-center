# Local build and test entry points (see CONTRIBUTING.md#tests). Needs Docker with Compose v2 and GNU make.
export MSYS_NO_PATHCONV := 1

UC_IMAGE ?= easy-update-center/uc:local
UC_TEST_IMAGE ?= easy-update-center/uc-test:local
WEB_IMAGE ?= easy-update-center/web:local
export UC_IMAGE UC_TEST_IMAGE WEB_IMAGE
# The release the uc images report (uc --version, status.json uc_version): make build UC_VERSION=0.1.0.
UC_VERSION ?= dev

.DEFAULT_GOAL := help

# Unit tests read the Python, templates and lint tools straight from the working tree, and the
# workflows in .github/
# (VersionTool.java is compiled into the image, so changing it needs `make build-uc`).
SRC_MOUNTS = -v "$(CURDIR)/tests:/tests:ro" \
             -v "$(CURDIR)/uc/uc:/opt/uc/python/uc:ro" \
             -v "$(CURDIR)/uc/templates:/opt/uc/templates:ro" \
             -v "$(CURDIR)/tools:/tools:ro" \
             -v "$(CURDIR)/web:/web:ro" \
             $(if $(wildcard examples/pipelines),-v "$(CURDIR)/examples/pipelines:/examples/pipelines:ro") \
             $(if $(wildcard .github),-v "$(CURDIR)/.github:/github:ro") \
             -v "$(CURDIR)/.env.online.example:/examples/env.online.example:ro" \
             -v "$(CURDIR)/.env.airgapped.example:/examples/env.airgapped.example:ro"

.PHONY: help build build-uc build-web uc-image test test-unit test-web test-routing test-setup test-pipelines compose-check examples it-airgapped it-clean it-online it-full upstream-diff

# PREBUILT=1 (CI, after `docker load`): build-uc and build-web keep an image that already exists
# instead of building it again. Without it they always build.
UNLESS_PREBUILT = $(if $(PREBUILT),docker image inspect $(1) >/dev/null 2>&1 || )

##@ Build

build: build-uc build-web ## Build the uc, uc-test and web images; works before .env exists

build-uc:
	$(call UNLESS_PREBUILT,$(UC_IMAGE))docker build --target runtime --build-arg UC_VERSION=$(UC_VERSION) -t $(UC_IMAGE) uc
	$(call UNLESS_PREBUILT,$(UC_TEST_IMAGE))docker build --target test --build-arg UC_VERSION=$(UC_VERSION) -t $(UC_TEST_IMAGE) uc

build-web:
	$(call UNLESS_PREBUILT,$(WEB_IMAGE))docker build -t $(WEB_IMAGE) web

# The uc image, built only when it is missing: the targets that merely run it must also work on a
# host that loaded pre-built images and has no internet access.
uc-image:
	@docker image inspect $(UC_IMAGE) >/dev/null 2>&1 || docker build --target runtime -t $(UC_IMAGE) uc

##@ Development

test: test-unit test-web test-routing test-setup ## Run the unit, web, routing and setup tests

test-unit: build-uc ## Run the unit tests in the uc-test image (ARGS=pytest options)
	docker run --rm $(SRC_MOUNTS) $(UC_TEST_IMAGE) -q /tests/unit $(ARGS)

test-web: build-web ## Check the web image's entrypoint
	bash tests/web/entrypoint-checks.sh $(WEB_IMAGE)

ROUTING = docker compose -f tests/routing/compose.yaml

test-routing: build-uc build-web ## Run the routing tests against the web image, in their own Compose project
	$(ROUTING) run --rm tester $(ARGS); rc=$$?; \
	  $(ROUTING) down -v --remove-orphans >/dev/null 2>&1; exit $$rc

# uc-setup's checks in the uc image: the script runs as root to lay out /setup and runs uc-setup as uid 1000.
test-setup: build-uc ## Check uc-setup in the uc image
	docker run --rm --user 0:0 --entrypoint bash -v "$(CURDIR)/uc/bin:/scripts:ro" \
	  -v "$(CURDIR)/uc/uc:/opt/uc/python/uc:ro" -v "$(CURDIR)/tests/setup:/tests:ro" $(UC_IMAGE) /tests/setup-checks.sh

test-pipelines: build-uc ## Check the example pipelines run the procurement stages in order, with the approval before the push
	docker run --rm $(SRC_MOUNTS) $(UC_TEST_IMAGE) -q /tests/unit/test_pipeline_examples.py

# The example .env files are generated from the settings registry (uc/uc/settings.py), using the
# working tree's code; test-unit fails while a committed copy is out of date.
EXAMPLE = docker run --rm -v "$(CURDIR)/uc/uc:/opt/uc/python/uc:ro" $(UC_IMAGE) config --example

examples: uc-image ## Regenerate the example .env files from the settings registry
	$(EXAMPLE) --mode online > .env.online.example
	$(EXAMPLE) --mode airgapped > .env.airgapped.example

# UC_ENV_FILE selects the services' env_file; --env-file only feeds compose's own interpolation.
# check_isolation.py asserts the network and secret boundaries the repo-adapter relies on.
ISOLATION = docker run -i --rm -v "$(CURDIR)/tests:/tests:ro" --entrypoint python3 $(UC_TEST_IMAGE) \
  /tests/compose/check_isolation.py

compose-check: build-uc ## Validate compose.yaml and check the services' network and secret boundaries
	UC_ENV_FILE=.env.online.example docker compose --env-file .env.online.example config --quiet
	UC_ENV_FILE=.env.airgapped.example docker compose --env-file .env.airgapped.example \
	  -f compose.yaml -f compose.airgapped.yaml config --quiet
	UC_ENV_FILE=.env.online.example docker compose --env-file .env.online.example --profile tools --profile setup \
	  config --format json | $(ISOLATION) online
	UC_ENV_FILE=.env.airgapped.example docker compose --env-file .env.airgapped.example \
	  -f compose.yaml -f compose.airgapped.yaml --profile tools --profile setup config --format json | $(ISOLATION) airgapped
	UC_ENV_FILE=.env.online.example docker compose --env-file .env.online.example -f compose.yaml -f compose.build.yaml \
	  --profile tools --profile setup config --format json | $(ISOLATION) online build

IT_DIR = tests/integration
IT_AIR = docker compose -f $(IT_DIR)/airgapped.yaml --env-file $(IT_DIR)/.versions.env
# Each repository type gets a fresh update center (its data volumes) against the same seeded repository.
IT_TYPES ?= artifactory artifact-keeper
IT_STATE = uc-it-airgapped_uc-data uc-it-airgapped_uc-data-proxy uc-it-airgapped_uc-data-age uc-it-airgapped_uc-data-push uc-it-airgapped_push-repo uc-it-airgapped_repo-adapter-data uc-it-airgapped_repo-adapter-push-data

it-airgapped: build ## Run the air-gapped integration test against a seeded repository (IT_TYPES=, ARGS=)
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

it-clean: ## Remove the integration tests' containers and volumes
	docker compose -f $(IT_DIR)/airgapped.yaml --profile seed down -v --remove-orphans
	# Compose interpolates online.yaml's required (:?) vars even for `down`, which never starts
	# the pki service that actually needs them, so harmless placeholders are enough here.
	JENKINS_COMMUNITY_USERNAME=unused JENKINS_COMMUNITY_PASSWORD=unused \
	  docker compose -f $(IT_DIR)/online.yaml down -v --remove-orphans

IT_ONLINE = docker compose -f $(IT_DIR)/online.yaml

it-online: build ## Run the online integration test (needs JENKINS_COMMUNITY_USERNAME and _PASSWORD)
	@test -n "$$JENKINS_COMMUNITY_USERNAME" -a -n "$$JENKINS_COMMUNITY_PASSWORD" || \
	  { echo "export JENKINS_COMMUNITY_USERNAME and JENKINS_COMMUNITY_PASSWORD (a Jenkins community account for repo.jenkins-ci.org; test-only, a deployment reads secrets/repo_*)"; exit 2; }
	@mkdir -p $(IT_DIR)/.results
	$(IT_ONLINE) run --rm pki
	$(IT_ONLINE) run --rm driver $(ARGS); rc=$$?; \
	  $(IT_ONLINE) down -v --remove-orphans >/dev/null 2>&1; exit $$rc

# Full catalog, no plugin cap: records duration, cache and site size
# (docs/getting-started/requirements.md#host-sizing). Takes hours on first run.
it-full: ## Run the online integration test on the full catalog; takes hours
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
upstream-diff: build-uc ## Compare the pinned update-center2 with another version (TO=3.19)
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

help: ## List the targets
	@awk 'BEGIN { FS = ":.*## "; print "usage: make <target> [VAR=value]" } \
	  /^##@ / { printf "\n%s\n", substr($$0, 5); next } \
	  /^[a-zA-Z0-9_-]+:.*## / { printf "  %-16s %s\n", $$1, $$2 }' $(MAKEFILE_LIST)
