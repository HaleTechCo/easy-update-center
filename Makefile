# Local build and test entry points (spec §12, §13). Needs Docker with Compose v2 and GNU make.
export MSYS_NO_PATHCONV := 1

UC_IMAGE ?= jenkins-uc/uc:local
UC_TEST_IMAGE ?= jenkins-uc/uc-test:local
WEB_IMAGE ?= jenkins-uc/web:local
export UC_IMAGE UC_TEST_IMAGE WEB_IMAGE

# Unit tests read the Python and templates straight from the working tree
# (VersionTool.java is compiled into the image, so changing it needs `make build-uc`).
SRC_MOUNTS = -v "$(CURDIR)/tests:/tests:ro" \
             -v "$(CURDIR)/uc/uc:/opt/uc/python/uc:ro" \
             -v "$(CURDIR)/uc/templates:/opt/uc/templates:ro"

.PHONY: build build-uc build-web test test-unit test-web test-routing pki-init secrets-placeholders compose-check it-airgapped it-clean it-online it-full upstream-diff

build: build-uc build-web

build-uc:
	docker build --target runtime -t $(UC_IMAGE) uc
	docker build --target test -t $(UC_TEST_IMAGE) uc

build-web:
	docker build -t $(WEB_IMAGE) web

test: test-unit test-web test-routing

test-unit: build-uc
	docker run --rm $(SRC_MOUNTS) $(UC_TEST_IMAGE) -q /tests/unit $(ARGS)

test-web: build-web
	bash tests/web/entrypoint-checks.sh $(WEB_IMAGE)

ROUTING = docker compose -f tests/routing/compose.yaml

test-routing: build-uc build-web
	$(ROUTING) run --rm tester $(ARGS); rc=$$?; \
	  $(ROUTING) down -v --remove-orphans >/dev/null 2>&1; exit $$rc

# Creates the PKI in ./secrets as uid 10001 (the uc user). Refuses to overwrite existing files.
pki-init: build-uc
	@mkdir -p secrets
	docker run --rm --user 0:0 --entrypoint sh -v "$(CURDIR)/secrets:/pki" $(UC_IMAGE) \
	  -c 'uc pki init --out /pki && chown -R 10001:10001 /pki'

secrets-placeholders:
	@mkdir -p secrets
	@for f in repo_username repo_password github_username github_token download_proxy_auth; do \
	  [ -e secrets/$$f ] || : > secrets/$$f; done
	@echo "secrets/: created any missing credential files (empty means 'not set')"

# UC_ENV_FILE selects the services' env_file; --env-file only feeds compose's own interpolation.
compose-check:
	UC_ENV_FILE=.env.online.example docker compose --env-file .env.online.example config --quiet
	UC_ENV_FILE=.env.airgapped.example docker compose --env-file .env.airgapped.example \
	  -f compose.yaml -f compose.airgapped.yaml config --quiet

IT_DIR = tests/integration
IT_AIR = docker compose -f $(IT_DIR)/airgapped.yaml --env-file $(IT_DIR)/.versions.env

it-airgapped: build
	docker compose -f $(IT_DIR)/airgapped.yaml --profile seed run --rm -T seed-versions > $(IT_DIR)/.versions.env
	cat $(IT_DIR)/.versions.env
	$(IT_AIR) --profile seed run --rm seed-plugins
	$(IT_AIR) --profile seed run --rm seed-repo
	$(IT_AIR) --profile seed run --rm pki
	$(IT_AIR) run --rm driver $(ARGS); rc=$$?; \
	  if [ $$rc -eq 0 ]; then $(IT_AIR) run --rm plugin-cli; rc=$$?; fi; \
	  $(IT_AIR) --profile seed down -v --remove-orphans >/dev/null 2>&1; exit $$rc

it-clean:
	docker compose -f $(IT_DIR)/airgapped.yaml --profile seed down -v --remove-orphans
	# Compose interpolates online.yaml's required (:?) vars even for `down`, which never starts
	# the pki service that actually needs them, so harmless placeholders are enough here.
	REPO_USERNAME=unused REPO_PASSWORD=unused \
	  docker compose -f $(IT_DIR)/online.yaml down -v --remove-orphans

IT_ONLINE = docker compose -f $(IT_DIR)/online.yaml

it-online: build
	@test -n "$$REPO_USERNAME" -a -n "$$REPO_PASSWORD" || \
	  { echo "export REPO_USERNAME and REPO_PASSWORD (a Jenkins community account)"; exit 2; }
	@mkdir -p $(IT_DIR)/.results
	$(IT_ONLINE) run --rm pki
	$(IT_ONLINE) run --rm driver $(ARGS); rc=$$?; \
	  $(IT_ONLINE) down -v --remove-orphans >/dev/null 2>&1; exit $$rc

# Full catalog, no plugin cap: records duration, cache and site size (spec S6). Takes hours on first run.
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
  src/main/java/io/jenkins/update_center/MavenArtifact.java

# Usage: make upstream-diff TO=3.19   (compares the pinned UC2_VERSION with TO; see docs/upgrading.md)
upstream-diff: build-uc
	@test -n "$(TO)" || { echo "usage: make upstream-diff TO=<update-center2 version>"; exit 2; }
	docker run --rm --entrypoint sh -v "$(CURDIR)/uc/patches:/patches:ro" $(UC_TEST_IMAGE) -c '\
	  set -e; git clone -q https://github.com/jenkins-infra/update-center2.git /tmp/uc2; cd /tmp/uc2; \
	  echo "== files changed between $(UC2_VERSION) and $(TO)"; \
	  git diff --stat update-center2-$(UC2_VERSION) update-center2-$(TO); \
	  echo "== files uc depends on"; \
	  git diff update-center2-$(UC2_VERSION) update-center2-$(TO) -- $(UPSTREAM_FILES); \
	  git checkout -q update-center2-$(TO); \
	  if git apply --check /patches/*.patch 2>/dev/null; then echo "== patch applies to $(TO)"; \
	  else echo "== PATCH DOES NOT APPLY to $(TO); check whether Popularities.java already reads POPULARITY_JSON_URL"; fi'
