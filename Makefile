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

.PHONY: build build-uc build-web test test-unit test-web test-routing

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
