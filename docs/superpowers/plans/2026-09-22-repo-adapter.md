# Repository adapter Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let the update center read plugins from Artifactory or Artifact Keeper, filling in the POMs, descriptions and release dates that procurement leaves out, with generic `REPO_*` settings and a configurable tier age.

**Architecture:** A new `repo-adapter` service (the `uc` image running `uc repo-adapter`) answers the three Artifactory requests upstream update-center2 makes (AQL listing, POM, archive entry) from either repository type, deriving missing files from inside each vetted archive. The generator, unmodified apart from two small patches, reads through it on an internal network; downloads still go straight to the repository through the web tier.

**Tech Stack:** Python 3.14 standard library (`http.server`, `urllib`, `zipfile`), upstream update-center2 3.18.4 with patches, Docker Compose v2, Apache httpd (unchanged), pytest in the `uc-test` image.

**Spec:** `docs/superpowers/specs/2026-09-22-repo-adapter-design.md` (approved 2026-09-22), which extends `docs/superpowers/specs/2026-09-22-jenkins-update-center-design.md`. Read both before starting.

**How the code in this plan was produced:** every task was implemented and run before this plan was written. The unit suite passed after each task in the order below, and `make test`, `make it-airgapped` (once per repository type, with real Jenkins controllers) and `make it-online` passed at the end. Code blocks are those commits, verbatim. Patch blocks apply with `git apply`: save the block to a file and run `git apply <file>`, or edit by hand.

## Global Constraints

- Python 3.14, standard library only; no new dependencies in either image.
- update-center2 stays at 3.18.4 (`b2c8ad2b2feb425b471e6dcc9174c50dc3b3c1dd`), modified only by `uc/patches/0001-popularity-configurable-and-optional.patch` and `uc/patches/0002-core-age-configurable.patch`.
- Configuration keys are generic: `REPO_TYPE`, `REPO_URL`, `REPO_KEY`, `REPO_API_URL`, `REPO_ADAPTER`, `REPO_CA_FILE`, `REPO_MAX_ARCHIVE_MB`, `CORE_AGE_DAYS`; secrets `repo_username` and `repo_password`. Nothing has been released, so there are no compatibility aliases (spec §6).
- Each deployment reads one repository, of either type (spec §1).
- The adapter never writes, moves or fixes files in the repository, and never serves a whole archive (spec §4.1, §13).
- `status.json` is public: counts only, never paths or URLs (spec §7).
- Every fetched file must return HTTP 200 and pass its own check, so an error page is never cached as content; archives are untrusted input with the §5.2 caps.
- Upstream sends credentials only with the AQL listing and cannot answer an authentication challenge on file reads (spec §2); the adapter's GETs therefore need none, and it listens only on the internal `repo` network.
- Tests run in containers through `make`. Files use LF line endings.
- Stage explicit paths; never `git add -A`. Never touch `.env`, `todo.txt`, `data/`, or anything in `secrets/` except `secrets/README.md`.

## Review Focus

Inputs the spec implies that are most likely to bite an operator, and the test that pins each:

1. **An Artifact Keeper repository with more than 100 files**: the listing must follow cursors to the end. `tests/unit/test_fake_artifact_keeper.py::test_the_backend_pages_through_the_fake` (Task 12) and `test_keeper_listing_follows_cursors_with_a_bearer_token` (Task 7).
2. **Error pages where files should be**: an HTML page saved as a `.pom`, a `.sha1` side-file, or an `.hpi`. `test_the_operators_test_repository`, `test_not_an_archive_is_skipped` (Task 8) and `test_keeper_files_use_basic_auth_and_sha1_side_files` (Task 7).
3. **An adapter restart in the middle of a generation**: GETs before any listing must still work. `test_a_get_before_any_listing_builds_one` (Task 9).
4. **An Artifactory that ignores `Range`**: dates fall back to the repository's time rather than downloading whole archives. `test_artifactory_without_range_support_gives_no_date` (Task 7).
5. **A POM whose groupId or version comes from `<parent>`**, as many real plugin POMs do: it must still count as usable. `test_group_and_version_may_come_from_the_parent` (Task 4).

## File Structure

| File | Responsibility |
|---|---|
| `uc/uc/config.py` | generic `REPO_*` settings, per-mode defaults, `source_url` (what uc and the jar read) |
| `uc/uc/preflight.py` | repository credentials, or the adapter token when the adapter is on |
| `uc/uc/pom.py` | safe XML parsing of untrusted POMs; does a POM belong to a path (rule 3) |
| `uc/uc/repo_adapter/archive.py` | bounded reads from untrusted zips; archive dates; the three `index.jelly` locations |
| `uc/uc/repo_adapter/cache.py` | content-addressed cache of derived files under `/data/repo-adapter/cache` |
| `uc/uc/repo_adapter/backends.py` | HTTP client, Range reader, Artifactory and Artifact Keeper backends |
| `uc/uc/repo_adapter/listing.py` | the gap-filled listing (rules 1-9) and `report.json` |
| `uc/uc/repo_adapter/server.py` | the HTTP service, its token, and `uc repo-adapter` |
| `uc/uc/doctor.py`, `uc/uc/generate.py` | the adapter's report in `uc doctor` and (as counts) in `status.json` |
| `uc/patches/0002-core-age-configurable.patch` | `CORE_AGE_DAYS` for upstream's tier generator |
| `web/entrypoint.sh` | `REPO_*` names and Artifact Keeper's `/maven/<key>` download URL |
| `compose.yaml`, `compose.airgapped.yaml` | the `repo-adapter` service, the `repo` network, credentials for the adapter only |
| `tests/integration/fake_artifact_keeper.py`, `types/*.env` | the air-gapped suite against both repository types |

---

### Task 1: Generic repository settings

Rename the product-named settings to `REPO_*`, add the adapter and core-age settings, and route uc's and the jar's repository access through `Settings.source_url`, which is the adapter when it is on.

**Files:**
- Modify: `uc/uc/config.py`, `uc/uc/model.py`, `uc/uc/preflight.py`, `uc/uc/jar.py`, `uc/uc/doctor.py`, `uc/uc/generate.py`
- Test: `tests/support/ucsupport.py`, `tests/unit/conftest.py`, `tests/unit/test_config.py`, `tests/unit/test_jar.py`, `tests/unit/test_preflight.py`, `tests/unit/test_generate.py`, `tests/unit/test_cli.py`, `tests/unit/test_doctor.py`

**Interfaces:**
- Produces `Settings` fields `repo_type`, `repo_url`, `repo_url_with_port`, `repo_api_url`, `repo_key`, `repo_adapter: bool`, `repo_adapter_url`, `repo_adapter_dir: Path`, `repo_ca_file: Path | None`, `repo_max_archive_mb: int`, `core_age_days: int`, `repo_username_file`, `repo_password_file`; properties `source_url`, `source_api_url`, `repo_adapter_token_file`, `repo_adapter_report_file`. The `artifactory_*` fields are removed.
- Produces `Credentials(repo_username, repo_password, github_username, github_password, github_placeholder)`.
- Produces `preflight.ADAPTER_USERNAME = "uc"`, `load_repository_credentials(settings) -> tuple[str, str]`, `load_adapter_token(settings) -> str`; `load_credentials` returns the adapter token when `settings.repo_adapter`.
- `jar_environment` now sets `ARTIFACTORY_*` from `source_url`/`source_api_url`/`repo_key` and adds `CORE_AGE_DAYS`.
- `tests/support/ucsupport.make_settings` defaults to `REPO_ADAPTER=off`, so existing tests read the repository directly.

- [ ] **Step 1: Update the tests and test support**

```diff
diff --git a/tests/support/ucsupport.py b/tests/support/ucsupport.py
index 19203a3..9ebdc91 100644
--- a/tests/support/ucsupport.py
+++ b/tests/support/ucsupport.py
@@ -1,4 +1,6 @@
-"""Settings for tests: air-gapped against a fake Artifactory, all state under tmp_path."""
+"""Settings for tests: air-gapped against a fake Artifactory, all state under tmp_path.
+
+The repo-adapter is off unless a test turns it on, so tests read the repository directly."""
 from __future__ import annotations
 
 from pathlib import Path
@@ -10,8 +12,9 @@ def make_settings(tmp_path: Path, secrets: Path, **env: str) -> Settings:
     base = {
         "MODE": "airgapped",
         "PUBLIC_URL": "https://uc.example.com",
-        "ARTIFACTORY_URL": "http://artifactory:8081/artifactory/",
-        "ARTIFACTORY_REPOSITORY": "jenkins",
+        "REPO_URL": "http://artifactory:8081/artifactory/",
+        "REPO_KEY": "jenkins",
+        "REPO_ADAPTER": "off",
         "SECRETS_DIR": str(secrets),
         "UC_DATA_DIR": str(tmp_path / "data"),
         "UC_OVERLAY_DIR": str(tmp_path / "overlay"),
diff --git a/tests/unit/conftest.py b/tests/unit/conftest.py
index 1eaf30a..111e577 100644
--- a/tests/unit/conftest.py
+++ b/tests/unit/conftest.py
@@ -22,6 +22,6 @@ def secrets_dir(tmp_path, pki_material):
     directory.mkdir()
     for name in ("update-center.key", "update-center.cert", "root-ca.crt"):
         shutil.copy(pki_material / name, directory / name)
-    (directory / "artifactory_username").write_text("uc\n")
-    (directory / "artifactory_password").write_text("secret\n")
+    (directory / "repo_username").write_text("uc\n")
+    (directory / "repo_password").write_text("secret\n")
     return directory
diff --git a/tests/unit/test_cli.py b/tests/unit/test_cli.py
index 2289489..20340db 100644
--- a/tests/unit/test_cli.py
+++ b/tests/unit/test_cli.py
@@ -11,7 +11,7 @@ from uc.status import read_status, write_status
 def use_env(monkeypatch, tmp_path, secrets_dir):
     values = {
         "MODE": "airgapped", "PUBLIC_URL": "https://uc.example.com",
-        "ARTIFACTORY_URL": "http://artifactory:8081/artifactory/", "ARTIFACTORY_REPOSITORY": "jenkins",
+        "REPO_URL": "http://artifactory:8081/artifactory/", "REPO_KEY": "jenkins", "REPO_ADAPTER": "off",
         "SECRETS_DIR": str(secrets_dir), "UC_DATA_DIR": str(tmp_path / "data"),
         "UC_OVERLAY_DIR": str(tmp_path / "overlay"),
     }
diff --git a/tests/unit/test_config.py b/tests/unit/test_config.py
index d4d0547..265c6e2 100644
--- a/tests/unit/test_config.py
+++ b/tests/unit/test_config.py
@@ -8,19 +8,26 @@ from uc.errors import ConfigError
 AIRGAPPED = {
     "MODE": "airgapped",
     "PUBLIC_URL": "https://uc.example.com",
-    "ARTIFACTORY_URL": "https://artifactory.corp/artifactory",
-    "ARTIFACTORY_REPOSITORY": "jenkins-local",
+    "REPO_URL": "https://artifactory.corp/artifactory",
+    "REPO_KEY": "jenkins-local",
 }
+KEEPER = {**AIRGAPPED, "REPO_TYPE": "artifact-keeper", "REPO_URL": "https://ak.corp", "REPO_KEY": "jenkins-prod-local"}
 
 
 def test_online_defaults():
     s = load_settings({"MODE": "online", "PUBLIC_URL": "https://uc.example.com/"})
     assert s.public_url == "https://uc.example.com"
     assert s.downloads_root_url == "https://uc.example.com/download"
-    assert s.artifactory_url == "https://repo.jenkins-ci.org/"
-    assert s.artifactory_url_with_port == "https://repo.jenkins-ci.org:443/"
-    assert s.artifactory_api_url == "https://repo.jenkins-ci.org:443/api/"
-    assert s.artifactory_repository == "releases"
+    assert s.repo_type == "artifactory"
+    assert s.repo_url == "https://repo.jenkins-ci.org/"
+    assert s.repo_url_with_port == "https://repo.jenkins-ci.org:443/"
+    assert s.repo_api_url == "https://repo.jenkins-ci.org:443/api/"
+    assert s.repo_key == "releases"
+    assert s.repo_adapter is False
+    assert (s.source_url, s.source_api_url) == ("https://repo.jenkins-ci.org:443/", "https://repo.jenkins-ci.org:443/api/")
+    assert s.core_age_days == 400
+    assert s.repo_max_archive_mb == 512
+    assert s.repo_ca_file is None
     assert s.download_backend == "mirror"
     assert s.download_backend_url == "https://get.jenkins.io"
     assert s.download_access == "redirect"
@@ -38,12 +45,21 @@ def test_online_defaults():
     assert s.data_dir == Path("/data")
     assert s.jar == Path("/opt/uc/generator/update-center2.jar")
     assert s.cache_dir == Path("/data/cache/artifactory")
+    assert s.repo_adapter_dir == Path("/data/repo-adapter")
+    assert s.repo_adapter_token_file == Path("/data/repo-adapter/token")
+    assert s.repo_adapter_report_file == Path("/data/repo-adapter/report.json")
+    assert s.repo_username_file == Path("/run/secrets/repo_username")
 
 
 def test_airgapped_defaults():
     s = load_settings(AIRGAPPED)
-    assert s.artifactory_url == "https://artifactory.corp/artifactory/"
-    assert s.artifactory_url_with_port == "https://artifactory.corp:443/artifactory/"
+    assert s.repo_url == "https://artifactory.corp/artifactory/"
+    assert s.repo_url_with_port == "https://artifactory.corp:443/artifactory/"
+    assert s.repo_adapter is True
+    assert s.repo_adapter_url == "http://repo-adapter:8081/artifactory/"
+    assert (s.source_url, s.source_api_url) == (
+        "http://repo-adapter:8081/artifactory/", "http://repo-adapter:8081/artifactory/api/")
+    assert s.core_age_days == 0
     assert s.download_backend == "artifactory"
     assert s.download_backend_url == "https://artifactory.corp/artifactory/jenkins-local"
     assert s.resources_source == "bundled"
@@ -51,11 +67,50 @@ def test_airgapped_defaults():
     assert s.tool_installers_url == ""
 
 
-def test_airgapped_requires_artifactory():
-    with pytest.raises(ConfigError, match="ARTIFACTORY_URL and ARTIFACTORY_REPOSITORY"):
+def test_airgapped_requires_a_repository():
+    with pytest.raises(ConfigError, match="REPO_URL and REPO_KEY must be set"):
         load_settings({"MODE": "airgapped", "PUBLIC_URL": "https://uc.example.com"})
 
 
+def test_artifact_keeper_defaults():
+    s = load_settings(KEEPER)
+    assert s.repo_type == "artifact-keeper"
+    assert s.repo_url == "https://ak.corp/"
+    assert s.repo_adapter is True
+    assert s.download_backend_url == "https://ak.corp/maven/jenkins-prod-local"
+
+
+def test_artifact_keeper_online_needs_its_own_url_and_forces_the_adapter():
+    with pytest.raises(ConfigError, match="REPO_URL and REPO_KEY must be set"):
+        load_settings({"MODE": "online", "PUBLIC_URL": "https://uc", "REPO_TYPE": "artifact-keeper"})
+    s = load_settings({**KEEPER, "MODE": "online"})
+    assert s.repo_adapter is True
+    assert s.core_age_days == 400
+
+
+def test_artifact_keeper_refuses_the_adapter_off():
+    with pytest.raises(ConfigError, match="REPO_TYPE=artifact-keeper needs REPO_ADAPTER=on"):
+        load_settings({**KEEPER, "REPO_ADAPTER": "off"})
+
+
+def test_adapter_settings_can_be_overridden():
+    s = load_settings({
+        **AIRGAPPED, "REPO_ADAPTER_URL": "http://127.0.0.1:9999/artifactory", "REPO_ADAPTER_DIR": "/state",
+        "REPO_CA_FILE": "/ca/corp.pem", "REPO_MAX_ARCHIVE_MB": "64", "CORE_AGE_DAYS": "400",
+    })
+    assert s.source_url == "http://127.0.0.1:9999/artifactory/"
+    assert s.repo_adapter_token_file == Path("/state/token")
+    assert s.repo_ca_file == Path("/ca/corp.pem")
+    assert (s.repo_max_archive_mb, s.core_age_days) == (64, 400)
+
+
+def test_online_private_artifactory_can_turn_the_adapter_on():
+    s = load_settings({"MODE": "online", "PUBLIC_URL": "https://uc", "REPO_URL": "https://art.corp/artifactory/",
+                       "REPO_KEY": "jenkins", "REPO_ADAPTER": "on"})
+    assert s.repo_adapter is True
+    assert s.source_url == "http://repo-adapter:8081/artifactory/"
+
+
 @pytest.mark.parametrize("mode", ["", "offline"])
 def test_mode_is_required_and_validated(mode):
     with pytest.raises(ConfigError, match="MODE must be one of online, airgapped"):
@@ -86,7 +141,12 @@ def test_resources_url_required_for_url_source():
         ("DOWNLOAD_BACKEND", "s3", "DOWNLOAD_BACKEND must be one of"),
         ("DOWNLOAD_ACCESS", "cache", "DOWNLOAD_ACCESS must be one of"),
         ("ALLOW_LOCAL_PUBLIC_URL", "maybe", "true or false"),
-        ("ARTIFACTORY_REPOSITORY", 'a"b', "unsupported characters"),
+        ("REPO_KEY", 'a"b', "unsupported characters"),
+        ("REPO_TYPE", "nexus", "REPO_TYPE must be one of artifactory, artifact-keeper"),
+        ("REPO_ADAPTER", "sometimes", "true or false"),
+        ("CORE_AGE_DAYS", "-1", "must be >= 0"),
+        ("CORE_AGE_DAYS", "ten", "must be an integer"),
+        ("REPO_MAX_ARCHIVE_MB", "0", "must be >= 1"),
     ],
 )
 def test_invalid_values_are_rejected(name, value, message):
@@ -107,7 +167,7 @@ def test_backend_url_override_and_extra_args():
 def test_secret_paths_follow_secrets_dir_and_overrides():
     s = load_settings({**AIRGAPPED, "SECRETS_DIR": "/pki", "GITHUB_TOKEN_FILE": "/other/token"})
     assert s.signing_cert_file == Path("/pki/update-center.cert")
-    assert s.artifactory_password_file == Path("/pki/artifactory_password")
+    assert s.repo_password_file == Path("/pki/repo_password")
     assert s.github_token_file == Path("/other/token")
 
 
@@ -124,9 +184,9 @@ def test_with_explicit_port(url, expected):
     assert with_explicit_port(url) == expected
 
 
-def test_credentials_in_artifactory_url_are_rejected():
+def test_credentials_in_repo_url_are_rejected():
     with pytest.raises(ConfigError, match="credentials"):
-        load_settings({**AIRGAPPED, "ARTIFACTORY_URL": "https://u:p@artifactory.corp/"})  # pragma: allowlist secret
+        load_settings({**AIRGAPPED, "REPO_URL": "https://u:p@artifactory.corp/"})  # pragma: allowlist secret
 
 
 def test_read_secret(tmp_path):
diff --git a/tests/unit/test_doctor.py b/tests/unit/test_doctor.py
index 30f6956..4a8b10b 100644
--- a/tests/unit/test_doctor.py
+++ b/tests/unit/test_doctor.py
@@ -144,7 +144,8 @@ def test_format_report(tmp_path, secrets_dir):
     settings = make_settings(tmp_path, secrets_dir)
     listing = classify(plugin("orphan", "1.0", "hpi") + plugin("git", "5.2.0", "hpi", "pom"))
     text = format_report(analyze(listing, versions(), FakeSource({}, {})), settings)
-    assert "Source: http://artifactory:8081/artifactory/jenkins" in text
+    assert "Source: artifactory repository jenkins at http://artifactory:8081/artifactory/" in text
+    assert "through repo-adapter" not in text
     assert "Jenkins cores with a POM: none" in text
     assert "Dropped by the generator: 1" in text
     assert "org/jenkins-ci/plugins/orphan/1.0/orphan-1.0.hpi" in text
diff --git a/tests/unit/test_generate.py b/tests/unit/test_generate.py
index 4775bcb..5e21e0d 100644
--- a/tests/unit/test_generate.py
+++ b/tests/unit/test_generate.py
@@ -129,7 +129,7 @@ def test_plugin_count_drop_needs_force(tmp_path, secrets_dir):
 
 
 def test_online_generation_uses_mirror_and_tool_installers(tmp_path, secrets_dir):
-    settings = make_settings(tmp_path, secrets_dir, MODE="online", ARTIFACTORY_URL="", ARTIFACTORY_REPOSITORY="",
+    settings = make_settings(tmp_path, secrets_dir, MODE="online", REPO_URL="", REPO_KEY="", REPO_ADAPTER="",
                              RESOURCES_SOURCE="bundled", TOOL_INSTALLERS_URL="https://updates.example/updates/")
     files = {
         "https://updates.example/updates/": b'<a href="hudson.tasks.Maven.MavenInstaller.json">m</a>',
diff --git a/tests/unit/test_jar.py b/tests/unit/test_jar.py
index ebdc89d..efce4d9 100644
--- a/tests/unit/test_jar.py
+++ b/tests/unit/test_jar.py
@@ -24,13 +24,24 @@ def test_jar_environment(tmp_path, secrets_dir):
     assert (env["GITHUB_USERNAME"], env["GITHUB_PASSWORD"]) == ("uc", "uc")
     assert env["POPULARITY_JSON_URL"] == ""
     assert env["CERTIFICATE_MINIMUM_VALID_DAYS"] == "30"
+    assert env["CORE_AGE_DAYS"] == "0"
 
 
 def test_jar_environment_uses_explicit_port(tmp_path, secrets_dir):
-    settings = make_settings(tmp_path, secrets_dir, ARTIFACTORY_URL="https://art.corp/artifactory")
+    settings = make_settings(tmp_path, secrets_dir, REPO_URL="https://art.corp/artifactory")
     assert jar_environment(settings, CREDS, {})["ARTIFACTORY_URL"] == "https://art.corp:443/artifactory/"
 
 
+def test_jar_environment_points_at_the_adapter_when_it_is_on(tmp_path, secrets_dir):
+    settings = make_settings(tmp_path, secrets_dir, REPO_ADAPTER="on", CORE_AGE_DAYS="400")
+    env = jar_environment(settings, Credentials("uc", "token", "uc", "uc", True), {})
+    assert env["ARTIFACTORY_URL"] == "http://repo-adapter:8081/artifactory/"
+    assert env["ARTIFACTORY_API_URL"] == "http://repo-adapter:8081/artifactory/api/"
+    assert env["ARTIFACTORY_REPOSITORY"] == "jenkins"
+    assert (env["ARTIFACTORY_USERNAME"], env["ARTIFACTORY_PASSWORD"]) == ("uc", "token")
+    assert env["CORE_AGE_DAYS"] == "400"
+
+
 def test_java_command(tmp_path, secrets_dir):
     assert java_command(make_settings(tmp_path, secrets_dir), "--x") == [
         "java", "-Dfile.encoding=UTF-8", "-DCERTIFICATE_MINIMUM_VALID_DAYS=30",
diff --git a/tests/unit/test_preflight.py b/tests/unit/test_preflight.py
index e6d26ed..9c9990e 100644
--- a/tests/unit/test_preflight.py
+++ b/tests/unit/test_preflight.py
@@ -5,7 +5,13 @@ import pytest
 from ucsupport import make_settings
 
 from uc.pki import init_pki
-from uc.preflight import PreflightError, check_public_url, check_signing, load_credentials
+from uc.preflight import (
+    PreflightError,
+    check_public_url,
+    check_signing,
+    load_credentials,
+    load_repository_credentials,
+)
 
 NOW = dt.datetime.now(dt.timezone.utc)
 
@@ -77,7 +83,7 @@ def test_check_signing_wrong_root(tmp_path, secrets_dir):
 
 def test_load_credentials_with_github_placeholder(tmp_path, secrets_dir):
     creds = load_credentials(make_settings(tmp_path, secrets_dir))
-    assert (creds.artifactory_username, creds.artifactory_password) == ("uc", "secret")
+    assert (creds.repo_username, creds.repo_password) == ("uc", "secret")
     assert (creds.github_username, creds.github_password, creds.github_placeholder) == ("uc", "uc", True)
 
 
@@ -88,7 +94,26 @@ def test_load_credentials_with_github_token(tmp_path, secrets_dir):
     assert (creds.github_username, creds.github_password, creds.github_placeholder) == ("bot", "ghp_x", False)
 
 
-def test_load_credentials_requires_artifactory(tmp_path, secrets_dir):
-    (secrets_dir / "artifactory_password").write_text("")
-    with pytest.raises(PreflightError, match="Artifactory credentials missing"):
+def test_load_credentials_requires_the_repository_account(tmp_path, secrets_dir):
+    (secrets_dir / "repo_password").write_text("")
+    with pytest.raises(PreflightError, match="Repository credentials missing"):
         load_credentials(make_settings(tmp_path, secrets_dir))
+
+
+def test_load_credentials_uses_the_adapter_token_when_the_adapter_is_on(tmp_path, secrets_dir):
+    settings = make_settings(tmp_path, secrets_dir, REPO_ADAPTER="on")
+    (secrets_dir / "repo_password").write_text("")  # the adapter holds these, not uc
+    settings.repo_adapter_dir.mkdir(parents=True)
+    settings.repo_adapter_token_file.write_text("adapter-token\n")
+    creds = load_credentials(settings)
+    assert (creds.repo_username, creds.repo_password) == ("uc", "adapter-token")
+
+
+def test_load_credentials_explains_a_missing_adapter_token(tmp_path, secrets_dir):
+    settings = make_settings(tmp_path, secrets_dir, REPO_ADAPTER="on")
+    with pytest.raises(PreflightError, match="repo-adapter token missing: .*repo-adapter.token"):
+        load_credentials(settings)
+
+
+def test_load_repository_credentials(tmp_path, secrets_dir):
+    assert load_repository_credentials(make_settings(tmp_path, secrets_dir)) == ("uc", "secret")
```

- [ ] **Step 2: Run the unit tests and watch them fail**

Run: `make test-unit`
Expected: FAIL. Most tests error in `make_settings` with `ConfigError: ARTIFACTORY_URL and ARTIFACTORY_REPOSITORY must be set in airgapped mode`, because the settings still read the old names.

- [ ] **Step 3: Implement the settings and their consumers**

```diff
diff --git a/uc/uc/config.py b/uc/uc/config.py
index e542199..ffea01a 100644
--- a/uc/uc/config.py
+++ b/uc/uc/config.py
@@ -11,6 +11,7 @@ from urllib.parse import urlsplit, urlunsplit
 from .errors import ConfigError
 
 MODES = ("online", "airgapped")
+REPO_TYPES = ("artifactory", "artifact-keeper")
 BACKENDS = ("mirror", "artifactory")
 ACCESS_MODES = ("redirect", "proxy")
 RESOURCE_SOURCES = ("github", "url", "bundled")
@@ -22,6 +23,8 @@ UPSTREAM_POPULARITY_URL = (
     "plugin-installation-trend/latestNumbers.json"
 )
 UPSTREAM_TOOL_INSTALLERS_URL = "https://updates.jenkins.io/updates/"
+DEFAULT_ADAPTER_URL = "http://repo-adapter:8081/artifactory/"
+UPSTREAM_CORE_AGE_DAYS = 400
 _REPOSITORY_RE = re.compile(r"^[A-Za-z0-9._-]+$")
 
 
@@ -37,10 +40,17 @@ class Settings:
     resources_sha256: str
     popularity_json_url: str
     tool_installers_url: str
-    artifactory_url: str
-    artifactory_url_with_port: str
-    artifactory_api_url: str
-    artifactory_repository: str
+    repo_type: str
+    repo_url: str
+    repo_url_with_port: str
+    repo_api_url: str
+    repo_key: str
+    repo_adapter: bool
+    repo_adapter_url: str
+    repo_adapter_dir: Path
+    repo_ca_file: Path | None
+    repo_max_archive_mb: int
+    core_age_days: int
     download_backend: str
     download_backend_url: str
     download_access: str
@@ -58,8 +68,8 @@ class Settings:
     signing_key_file: Path
     signing_cert_file: Path
     root_ca_file: Path
-    artifactory_username_file: Path
-    artifactory_password_file: Path
+    repo_username_file: Path
+    repo_password_file: Path
     github_username_file: Path
     github_token_file: Path
 
@@ -95,6 +105,23 @@ class Settings:
     def cache_dir(self) -> Path:
         return self.data_dir / "cache" / "artifactory"
 
+    @property
+    def source_url(self) -> str:
+        """Artifactory-style base URL that uc and the jar read: the adapter, or the repository itself."""
+        return self.repo_adapter_url if self.repo_adapter else self.repo_url_with_port
+
+    @property
+    def source_api_url(self) -> str:
+        return self.repo_adapter_url + "api/" if self.repo_adapter else self.repo_api_url
+
+    @property
+    def repo_adapter_token_file(self) -> Path:
+        return self.repo_adapter_dir / "token"
+
+    @property
+    def repo_adapter_report_file(self) -> Path:
+        return self.repo_adapter_dir / "report.json"
+
 
 def _get(env: Mapping[str, str], name: str, default: str = "") -> str:
     value = env.get(name, "").strip()
@@ -175,19 +202,36 @@ def load_settings(env: Mapping[str, str] | None = None) -> Settings:
     if not public_url:
         raise ConfigError("PUBLIC_URL must be set")
 
-    artifactory_url = _get(env, "ARTIFACTORY_URL", PUBLIC_ARTIFACTORY_URL if online else "")
-    repository = _get(env, "ARTIFACTORY_REPOSITORY", "releases" if online else "")
-    if not artifactory_url or not repository:
-        raise ConfigError("ARTIFACTORY_URL and ARTIFACTORY_REPOSITORY must be set in airgapped mode")
-    if not _REPOSITORY_RE.match(repository):
-        raise ConfigError(f"ARTIFACTORY_REPOSITORY contains unsupported characters: {repository!r}")
-    artifactory_url = with_trailing_slash(artifactory_url)
-    artifactory_url_with_port = with_explicit_port(artifactory_url)
-    api_url = with_trailing_slash(_get(env, "ARTIFACTORY_API_URL", artifactory_url_with_port + "api/"))
+    repo_type = _choice(env, "REPO_TYPE", "artifactory", REPO_TYPES)
+    public_repo = online and repo_type == "artifactory"
+    repo_url = _get(env, "REPO_URL", PUBLIC_ARTIFACTORY_URL if public_repo else "")
+    repo_key = _get(env, "REPO_KEY", "releases" if public_repo else "")
+    if not repo_url or not repo_key:
+        raise ConfigError(
+            "REPO_URL and REPO_KEY must be set (they default to repo.jenkins-ci.org "
+            "only in online mode with REPO_TYPE=artifactory)"
+        )
+    if not _REPOSITORY_RE.match(repo_key):
+        raise ConfigError(f"REPO_KEY contains unsupported characters: {repo_key!r}")
+    repo_url = with_trailing_slash(repo_url)
+    repo_url_with_port = with_explicit_port(repo_url)
+    api_url = with_trailing_slash(_get(env, "REPO_API_URL", repo_url_with_port + "api/"))
+
+    adapter_default = not online or repo_type == "artifact-keeper"
+    repo_adapter = _bool(env, "REPO_ADAPTER", adapter_default)
+    if repo_type == "artifact-keeper" and not repo_adapter:
+        raise ConfigError("REPO_TYPE=artifact-keeper needs REPO_ADAPTER=on")
+    repo_ca = _get(env, "REPO_CA_FILE")
 
     backend = _choice(env, "DOWNLOAD_BACKEND", "mirror" if online else "artifactory", BACKENDS)
-    default_backend_url = UPSTREAM_MIRROR_URL if backend == "mirror" else artifactory_url + repository
+    if backend == "mirror":
+        default_backend_url = UPSTREAM_MIRROR_URL
+    elif repo_type == "artifact-keeper":
+        default_backend_url = f"{repo_url.rstrip('/')}/maven/{repo_key}"
+    else:
+        default_backend_url = repo_url + repo_key
     backend_url = _get(env, "DOWNLOAD_BACKEND_URL", default_backend_url).rstrip("/")
+    data_dir = Path(_get(env, "UC_DATA_DIR", "/data"))
 
     resources_source = _choice(
         env, "RESOURCES_SOURCE", "github" if online else "bundled", RESOURCE_SOURCES
@@ -217,10 +261,17 @@ def load_settings(env: Mapping[str, str] | None = None) -> Settings:
         tool_installers_url=env.get(
             "TOOL_INSTALLERS_URL", UPSTREAM_TOOL_INSTALLERS_URL if online else ""
         ).strip(),
-        artifactory_url=artifactory_url,
-        artifactory_url_with_port=artifactory_url_with_port,
-        artifactory_api_url=api_url,
-        artifactory_repository=repository,
+        repo_type=repo_type,
+        repo_url=repo_url,
+        repo_url_with_port=repo_url_with_port,
+        repo_api_url=api_url,
+        repo_key=repo_key,
+        repo_adapter=repo_adapter,
+        repo_adapter_url=with_trailing_slash(_get(env, "REPO_ADAPTER_URL", DEFAULT_ADAPTER_URL)),
+        repo_adapter_dir=Path(_get(env, "REPO_ADAPTER_DIR", str(data_dir / "repo-adapter"))),
+        repo_ca_file=Path(repo_ca) if repo_ca else None,
+        repo_max_archive_mb=_int(env, "REPO_MAX_ARCHIVE_MB", 512, minimum=1),
+        core_age_days=_int(env, "CORE_AGE_DAYS", UPSTREAM_CORE_AGE_DAYS if online else 0),
         download_backend=backend,
         download_backend_url=backend_url,
         download_access=_choice(env, "DOWNLOAD_ACCESS", "redirect", ACCESS_MODES),
@@ -232,14 +283,14 @@ def load_settings(env: Mapping[str, str] | None = None) -> Settings:
         stale_after_days=_int(env, "STALE_AFTER_DAYS", 30),
         allow_local_public_url=_bool(env, "ALLOW_LOCAL_PUBLIC_URL", False),
         extra_args=tuple(_get(env, "UC_EXTRA_ARGS").split()),
-        data_dir=Path(_get(env, "UC_DATA_DIR", "/data")),
+        data_dir=data_dir,
         overlay_dir=Path(_get(env, "UC_OVERLAY_DIR", "/config/overlay")),
         app_dir=Path(_get(env, "UC_APP_DIR", "/opt/uc")),
         signing_key_file=secret("SIGNING_KEY_FILE", "update-center.key"),
         signing_cert_file=secret("SIGNING_CERT_FILE", "update-center.cert"),
         root_ca_file=secret("ROOT_CA_FILE", "root-ca.crt"),
-        artifactory_username_file=secret("ARTIFACTORY_USERNAME_FILE", "artifactory_username"),
-        artifactory_password_file=secret("ARTIFACTORY_PASSWORD_FILE", "artifactory_password"),
+        repo_username_file=secret("REPO_USERNAME_FILE", "repo_username"),
+        repo_password_file=secret("REPO_PASSWORD_FILE", "repo_password"),
         github_username_file=secret("GITHUB_USERNAME_FILE", "github_username"),
         github_token_file=secret("GITHUB_TOKEN_FILE", "github_token"),
     )
diff --git a/uc/uc/doctor.py b/uc/uc/doctor.py
index 8c2d80d..9978bc3 100644
--- a/uc/uc/doctor.py
+++ b/uc/uc/doctor.py
@@ -74,8 +74,8 @@ class ArtifactSource:
     @classmethod
     def from_settings(cls, settings: Settings, creds: Credentials, *, allow_network: bool) -> "ArtifactSource":
         # Same URL the jar uses, so cache keys (sha256 of the URL path) match.
-        return cls(settings.artifactory_url_with_port, settings.artifactory_repository,
-                   creds.artifactory_username, creds.artifactory_password, settings.cache_dir, allow_network)
+        return cls(settings.source_url, settings.repo_key,
+                   creds.repo_username, creds.repo_password, settings.cache_dir, allow_network)
 
     def pom(self, gav: Gav) -> bytes | None:
         return self._get(self.base + gav.with_extension("pom").maven_path)
@@ -198,8 +198,7 @@ def run_doctor(
     settings: Settings, creds: Credentials, *, fetch: bool, versions: VersionTool,
     query: Callable[..., list[aql.RepoFile]] = aql.query_repository,
 ) -> DoctorReport:
-    files = query(settings.artifactory_api_url, settings.artifactory_repository,
-                  creds.artifactory_username, creds.artifactory_password)
+    files = query(settings.source_api_url, settings.repo_key, creds.repo_username, creds.repo_password)
     report = analyze(aql.classify(files), versions,
                      ArtifactSource.from_settings(settings, creds, allow_network=fetch))
     tiers_file = settings.data_dir / "live" / "tiers.json"
@@ -213,7 +212,8 @@ def run_doctor(
 
 def format_report(report: DoctorReport, settings: Settings) -> str:
     lines = [
-        f"Source: {settings.artifactory_url}{settings.artifactory_repository}",
+        f"Source: {settings.repo_type} repository {settings.repo_key} at {settings.repo_url}"
+        + (" (through repo-adapter)" if settings.repo_adapter else ""),
         f"Plugins with a POM: {report.plugins}",
         "Jenkins cores with a POM: "
         + (", ".join(report.cores) or "none (the site will offer no core and no tiers)"),
diff --git a/uc/uc/generate.py b/uc/uc/generate.py
index 97c2bd3..855b851 100644
--- a/uc/uc/generate.py
+++ b/uc/uc/generate.py
@@ -108,8 +108,7 @@ def _build_and_publish(
     mapping: dict[str, str] | None = None
     if settings.download_backend == "artifactory":
         listing = aql.classify(tools.aql_query(
-            settings.artifactory_api_url, settings.artifactory_repository,
-            creds.artifactory_username, creds.artifactory_password,
+            settings.source_api_url, settings.repo_key, creds.repo_username, creds.repo_password,
         ))
         current = validate.load_site_json(staging / "current" / "update-center.actual.json")
         mapping, map_warnings = downloadmap.build_download_map(listing, downloadmap.preferred_groups(current))
diff --git a/uc/uc/jar.py b/uc/uc/jar.py
index 226fef7..e9dbb8a 100644
--- a/uc/uc/jar.py
+++ b/uc/uc/jar.py
@@ -22,16 +22,17 @@ def jar_environment(settings: Settings, creds: Credentials, base_env: Mapping[st
     env = dict(base_env)
     env.update({
         "DOWNLOADS_ROOT_URL": settings.downloads_root_url,
-        "ARTIFACTORY_URL": settings.artifactory_url_with_port,
-        "ARTIFACTORY_API_URL": settings.artifactory_api_url,
-        "ARTIFACTORY_REPOSITORY": settings.artifactory_repository,
-        "ARTIFACTORY_USERNAME": creds.artifactory_username,
-        "ARTIFACTORY_PASSWORD": creds.artifactory_password,
+        "ARTIFACTORY_URL": settings.source_url,
+        "ARTIFACTORY_API_URL": settings.source_api_url,
+        "ARTIFACTORY_REPOSITORY": settings.repo_key,
+        "ARTIFACTORY_USERNAME": creds.repo_username,
+        "ARTIFACTORY_PASSWORD": creds.repo_password,
         "ARTIFACTORY_CACHEDIR": str(settings.cache_dir),
         "GITHUB_USERNAME": creds.github_username,
         "GITHUB_PASSWORD": creds.github_password,
         "POPULARITY_JSON_URL": settings.popularity_json_url,
         "CERTIFICATE_MINIMUM_VALID_DAYS": str(settings.certificate_minimum_valid_days),
+        "CORE_AGE_DAYS": str(settings.core_age_days),
     })
     return env
 
diff --git a/uc/uc/model.py b/uc/uc/model.py
index 020169e..3bbe18e 100644
--- a/uc/uc/model.py
+++ b/uc/uc/model.py
@@ -18,8 +18,10 @@ class Tiers:
 
 @dataclass(frozen=True)
 class Credentials:
-    artifactory_username: str
-    artifactory_password: str
+    """What uc and the jar present to Settings.source_url: the repository's account, or the adapter token."""
+
+    repo_username: str
+    repo_password: str
     github_username: str
     github_password: str
     github_placeholder: bool
diff --git a/uc/uc/preflight.py b/uc/uc/preflight.py
index 6ef147d..5c88eb6 100644
--- a/uc/uc/preflight.py
+++ b/uc/uc/preflight.py
@@ -11,6 +11,7 @@ from .model import Credentials
 from .pki import CertInfo, cert_info, key_matches_cert, verify_chain
 
 GITHUB_PLACEHOLDER = "uc"
+ADAPTER_USERNAME = "uc"
 
 
 class PreflightError(UcError):
@@ -69,14 +70,33 @@ def check_signing(settings: Settings, now: dt.datetime) -> CertInfo:
     return info
 
 
-def load_credentials(settings: Settings) -> Credentials:
-    user = read_secret(settings.artifactory_username_file)
-    password = read_secret(settings.artifactory_password_file)
+def load_repository_credentials(settings: Settings) -> tuple[str, str]:
+    """The repository account; with the adapter on, only the adapter needs it."""
+    user = read_secret(settings.repo_username_file)
+    password = read_secret(settings.repo_password_file)
     if not user or not password:
         raise PreflightError(
-            "Artifactory credentials missing: provide "
-            f"{settings.artifactory_username_file} and {settings.artifactory_password_file}"
+            "Repository credentials missing: provide "
+            f"{settings.repo_username_file} and {settings.repo_password_file}"
+        )
+    return user, password
+
+
+def load_adapter_token(settings: Settings) -> str:
+    token = read_secret(settings.repo_adapter_token_file)
+    if not token:
+        raise PreflightError(
+            f"repo-adapter token missing: {settings.repo_adapter_token_file}; "
+            "the repo-adapter service creates it when it starts"
         )
+    return token
+
+
+def load_credentials(settings: Settings) -> Credentials:
+    if settings.repo_adapter:
+        user, password = ADAPTER_USERNAME, load_adapter_token(settings)
+    else:
+        user, password = load_repository_credentials(settings)
     gh_user = read_secret(settings.github_username_file)
     gh_token = read_secret(settings.github_token_file)
     if gh_user and gh_token:
```

- [ ] **Step 4: Run the unit tests**

Run: `make test-unit`
Expected: PASS, `192 passed`.

- [ ] **Step 5: Commit**

```bash
git add uc/uc/config.py uc/uc/model.py uc/uc/preflight.py uc/uc/jar.py uc/uc/doctor.py uc/uc/generate.py \
  tests/support/ucsupport.py tests/unit/conftest.py tests/unit/test_config.py tests/unit/test_jar.py \
  tests/unit/test_preflight.py tests/unit/test_generate.py tests/unit/test_cli.py tests/unit/test_doctor.py
git commit -m "Generic REPO_* settings, adapter and core-age settings"
```

### Task 2: The rename outside Python, and Artifact Keeper downloads in the web tier

Carry the new names through the web entrypoint, Compose, the Makefile, the test stacks and the docs, and teach the web entrypoint Artifact Keeper's `/maven/<key>` download path. The air-gapped test driver sets `REPO_ADAPTER: "off"` until Task 12 adds the adapter service.

**Files:**
- Modify: `web/entrypoint.sh`, `compose.yaml`, `Makefile`, `tests/integration/online.yaml`, `tests/integration/airgapped.yaml`, `.env.airgapped.example`, `secrets/README.md`, `README.md`, `docs/operations.md`, `docs/spikes/README.md`
- Test: `tests/web/entrypoint-checks.sh`, `tests/routing/compose.yaml`, `tests/routing/test_downloads.py`

**Interfaces:**
- The web entrypoint reads `REPO_TYPE` (`artifactory` | `artifact-keeper`), `REPO_URL`, `REPO_KEY`; for Artifact Keeper the default `DOWNLOAD_BACKEND_URL` is `${REPO_URL%/}/maven/${REPO_KEY}`, which must agree with `uc/uc/config.py`. It exits 64 on an unknown `REPO_TYPE` and on `artifact-keeper` with `REPO_ADAPTER=off`.
- `make it-online` reads `REPO_USERNAME` and `REPO_PASSWORD`; `make secrets-placeholders` creates `secrets/repo_username` and `secrets/repo_password`.

- [ ] **Step 1: Add the entrypoint checks and the routing test**

```diff
diff --git a/tests/routing/compose.yaml b/tests/routing/compose.yaml
index f2a7c00..549ab88 100644
--- a/tests/routing/compose.yaml
+++ b/tests/routing/compose.yaml
@@ -47,8 +47,19 @@ services:
     environment:
       MODE: airgapped
       PUBLIC_URL: "http://web-artifactory:8080"
-      ARTIFACTORY_URL: "http://artifactory.internal/artifactory/"
-      ARTIFACTORY_REPOSITORY: jenkins
+      REPO_URL: "http://artifactory.internal/artifactory/"
+      REPO_KEY: jenkins
+    volumes:
+      - {type: volume, source: fixtures, target: /data, read_only: true, volume: {subpath: artifactory}}
+
+  web-keeper:
+    <<: *web
+    environment:
+      MODE: airgapped
+      PUBLIC_URL: "http://web-keeper:8080"
+      REPO_TYPE: artifact-keeper
+      REPO_URL: "http://keeper.internal/"
+      REPO_KEY: jenkins
     volumes:
       - {type: volume, source: fixtures, target: /data, read_only: true, volume: {subpath: artifactory}}
 
@@ -89,6 +100,7 @@ services:
       backend: {condition: service_started}
       web-full: {condition: service_healthy}
       web-artifactory: {condition: service_healthy}
+      web-keeper: {condition: service_healthy}
       web-proxy: {condition: service_healthy}
       web-weekly: {condition: service_healthy}
       web-stable: {condition: service_healthy}
diff --git a/tests/routing/test_downloads.py b/tests/routing/test_downloads.py
index 29718b1..d607feb 100644
--- a/tests/routing/test_downloads.py
+++ b/tests/routing/test_downloads.py
@@ -5,6 +5,7 @@ import pytest
 from helpers import fetch
 
 ARTIFACTORY = "http://artifactory.internal/artifactory/jenkins"
+KEEPER = "http://keeper.internal/maven/jenkins"
 
 
 def test_mirror_plugin_redirect():
@@ -42,6 +43,14 @@ def test_artifactory_map_hit_and_miss():
     assert fetch("web-artifactory", "/download/war/2.540/jenkins.war").status == 404
 
 
+def test_artifact_keeper_downloads_use_its_maven_path():
+    response = fetch("web-keeper", "/download/plugins/git/5.2.0/git.hpi")
+    assert response.status == 302
+    assert response.location == f"{KEEPER}/org/jenkins-ci/plugins/git/5.2.0/git-5.2.0.hpi"
+    war = fetch("web-keeper", "/download/war/2.528.1/jenkins.war")
+    assert war.location == f"{KEEPER}/org/jenkins-ci/main/jenkins-war/2.528.1/jenkins-war-2.528.1.war"
+
+
 def test_proxy_mode_streams_with_credentials():
     response = fetch("web-proxy", "/download/plugins/git/5.2.0/git.hpi")
     assert response.status == 200
diff --git a/tests/web/entrypoint-checks.sh b/tests/web/entrypoint-checks.sh
index a1b196f..af13b79 100644
--- a/tests/web/entrypoint-checks.sh
+++ b/tests/web/entrypoint-checks.sh
@@ -20,7 +20,10 @@ expect_exit() {  # expect_exit CODE MESSAGE [docker run args...]
 
 expect_exit 64 "PUBLIC_URL must be set" -e MODE=online
 expect_exit 64 "MODE must be online or airgapped" -e MODE=bogus -e PUBLIC_URL=http://uc
-expect_exit 64 "set DOWNLOAD_BACKEND_URL, or ARTIFACTORY_URL and ARTIFACTORY_REPOSITORY" -e MODE=airgapped -e PUBLIC_URL=http://uc
+expect_exit 64 "set DOWNLOAD_BACKEND_URL, or REPO_URL and REPO_KEY" -e MODE=airgapped -e PUBLIC_URL=http://uc
+expect_exit 64 "REPO_TYPE must be artifactory or artifact-keeper" -e MODE=online -e PUBLIC_URL=http://uc -e REPO_TYPE=nexus
+expect_exit 64 "REPO_TYPE=artifact-keeper needs REPO_ADAPTER=on" -e MODE=airgapped -e PUBLIC_URL=http://uc \
+  -e REPO_TYPE=artifact-keeper -e REPO_URL=http://ak -e REPO_KEY=jenkins -e REPO_ADAPTER=off
 expect_exit 64 "DOWNLOAD_BACKEND must be mirror or artifactory" -e MODE=online -e PUBLIC_URL=http://uc -e DOWNLOAD_BACKEND=s3
 expect_exit 64 "needs username:password" -e MODE=online -e PUBLIC_URL=http://uc -e DOWNLOAD_ACCESS=proxy
 expect_exit 64 "TLS_CERT_FILE and TLS_KEY_FILE must both be readable" -e MODE=online -e PUBLIC_URL=http://uc -e TLS_CERT_FILE=/nope
```

- [ ] **Step 2: Run them and watch them fail**

Run: `make test-routing`
Expected: FAIL. `web-artifactory` and `web-keeper` exit 64 with `set DOWNLOAD_BACKEND_URL, or ARTIFACTORY_URL and ARTIFACTORY_REPOSITORY`, because the entrypoint still reads the old names, so Compose reports a dependency that failed to start. Do not run `make test-web` at this point: the old entrypoint ignores `REPO_TYPE`, so the new `REPO_TYPE=nexus` check would wait for a release instead of exiting.

- [ ] **Step 3: Update the entrypoint and every other surface of the rename**

````diff
diff --git a/.env.airgapped.example b/.env.airgapped.example
index 45f6962..10cdd25 100644
--- a/.env.airgapped.example
+++ b/.env.airgapped.example
@@ -2,8 +2,8 @@
 # "What the Artifactory repository must contain") and downloads redirect to it. Every setting: spec §11.
 MODE=airgapped
 PUBLIC_URL=https://jenkins-updates.corp.example
-ARTIFACTORY_URL=https://artifactory.corp.example/artifactory/
-ARTIFACTORY_REPOSITORY=jenkins-plugins-local
+REPO_URL=https://artifactory.corp.example/artifactory/
+REPO_KEY=jenkins-plugins-local
 # Controllers cannot read Artifactory anonymously? Stream downloads through the web tier instead:
 # DOWNLOAD_ACCESS=proxy
 # Backend uses a private CA? Mount the bundle and point this at it (docs/operations.md, section 7):
diff --git a/Makefile b/Makefile
index 9b3df5c..f088a1b 100644
--- a/Makefile
+++ b/Makefile
@@ -45,7 +45,7 @@ pki-init: build-uc
 
 secrets-placeholders:
 	@mkdir -p secrets
-	@for f in artifactory_username artifactory_password github_username github_token download_proxy_auth; do \
+	@for f in repo_username repo_password github_username github_token download_proxy_auth; do \
 	  [ -e secrets/$$f ] || : > secrets/$$f; done
 	@echo "secrets/: created any missing credential files (empty means 'not set')"
 
@@ -72,14 +72,14 @@ it-clean:
 	docker compose -f $(IT_DIR)/airgapped.yaml --profile seed down -v --remove-orphans
 	# Compose interpolates online.yaml's required (:?) vars even for `down`, which never starts
 	# the pki service that actually needs them, so harmless placeholders are enough here.
-	ARTIFACTORY_USERNAME=unused ARTIFACTORY_PASSWORD=unused \
+	REPO_USERNAME=unused REPO_PASSWORD=unused \
 	  docker compose -f $(IT_DIR)/online.yaml down -v --remove-orphans
 
 IT_ONLINE = docker compose -f $(IT_DIR)/online.yaml
 
 it-online: build
-	@test -n "$$ARTIFACTORY_USERNAME" -a -n "$$ARTIFACTORY_PASSWORD" || \
-	  { echo "export ARTIFACTORY_USERNAME and ARTIFACTORY_PASSWORD (a Jenkins community account)"; exit 2; }
+	@test -n "$$REPO_USERNAME" -a -n "$$REPO_PASSWORD" || \
+	  { echo "export REPO_USERNAME and REPO_PASSWORD (a Jenkins community account)"; exit 2; }
 	@mkdir -p $(IT_DIR)/.results
 	$(IT_ONLINE) run --rm pki
 	$(IT_ONLINE) run --rm driver $(ARGS); rc=$$?; \
diff --git a/README.md b/README.md
index b910ce8..032cf15 100644
--- a/README.md
+++ b/README.md
@@ -16,7 +16,7 @@ Two modes:
 make build
 cp .env.online.example .env      # or .env.airgapped.example; set PUBLIC_URL
 make pki-init secrets-placeholders
-# write secrets/artifactory_username and secrets/artifactory_password
+# write secrets/repo_username and secrets/repo_password
 docker compose up -d
 ```
 
diff --git a/compose.yaml b/compose.yaml
index 52ad27e..d72cd42 100644
--- a/compose.yaml
+++ b/compose.yaml
@@ -17,8 +17,8 @@ x-uc: &uc
     - update-center.key
     - update-center.cert
     - root-ca.crt
-    - artifactory_username
-    - artifactory_password
+    - repo_username
+    - repo_password
     - github_username
     - github_token
 
@@ -57,10 +57,10 @@ secrets:
     file: ./secrets/update-center.cert
   root-ca.crt:
     file: ./secrets/root-ca.crt
-  artifactory_username:
-    file: ./secrets/artifactory_username
-  artifactory_password:
-    file: ./secrets/artifactory_password
+  repo_username:
+    file: ./secrets/repo_username
+  repo_password:
+    file: ./secrets/repo_password
   github_username:
     file: ./secrets/github_username
   github_token:
diff --git a/docs/operations.md b/docs/operations.md
index d0e3cf3..c028aba 100644
--- a/docs/operations.md
+++ b/docs/operations.md
@@ -39,8 +39,8 @@ expiring certificate stops publication before Jenkins would reject the signature
 
 ```bash
 make secrets-placeholders
-printf '%s' 'svc-jenkins-uc' > secrets/artifactory_username
-printf '%s' '...password or API token...' > secrets/artifactory_password
+printf '%s' 'svc-jenkins-uc' > secrets/repo_username
+printf '%s' '...password or API token...' > secrets/repo_password
 # online, recommended: GitHub user and a token without scopes
 printf '%s' 'my-bot' > secrets/github_username
 printf '%s' 'ghp_...' > secrets/github_token
@@ -57,8 +57,8 @@ Compose mounts secret files with their host ownership. The uc containers run as
 container as uid 33:
 
 ```bash
-sudo chown 10001:10001 secrets/update-center.* secrets/root-ca.crt secrets/artifactory_* secrets/github_*
-sudo chmod 0400 secrets/update-center.key secrets/artifactory_password secrets/github_token
+sudo chown 10001:10001 secrets/update-center.* secrets/root-ca.crt secrets/repo_* secrets/github_*
+sudo chmod 0400 secrets/update-center.key secrets/repo_password secrets/github_token
 sudo chown 33:33 secrets/download_proxy_auth && sudo chmod 0400 secrets/download_proxy_auth
 ```
 
@@ -201,7 +201,7 @@ available for a proxy in front.
 
 | Symptom | Cause and fix |
 |---|---|
-| `Artifactory credentials missing: provide /run/secrets/artifactory_username and /run/secrets/artifactory_password` (preflight), or AQL HTTP 403 / `ARTIFACTORY_USERNAME and ARTIFACTORY_PASSWORD need to be set` from the generator | Credentials missing or not allowed to run AQL (spec S1). |
+| `Repository credentials missing: provide /run/secrets/repo_username and /run/secrets/repo_password` (preflight), or AQL HTTP 403 | Credentials missing or not allowed to run AQL (spec S1). |
 | `PUBLIC_URL must be a bare origin` | Remove the path; give the update center its own hostname. |
 | `signing certificate expires in N days` | Rotate the signing certificate (section 3). |
 | `plugin count N < 0.9 × M` | The source shrank. Check `uc doctor`; if intended, `uc generate --force`. |
diff --git a/docs/spikes/README.md b/docs/spikes/README.md
index 175d7c4..84d42d4 100644
--- a/docs/spikes/README.md
+++ b/docs/spikes/README.md
@@ -7,9 +7,9 @@ half of S1b and S2 against the operator's own Artifactory. Still open: the downl
 
 | # | Question | Procedure | Result (date, who, outcome) |
 |---|---|---|---|
-| S1a | Can a Jenkins community account run the generator's AQL query on repo.jenkins-ci.org? | `export ARTIFACTORY_USERNAME=... ARTIFACTORY_PASSWORD=...; make it-online`. The first test fails with `HTTP 403` if not. | **Yes** - 2026-09-22, operator's community account. `make it-online` passed (6 passed, 1 skipped; the skip is S7). Anonymous AQL returns 403, the same account authenticated returns 200. Online mode needs no private Artifactory mirror. |
+| S1a | Can a Jenkins community account run the generator's AQL query on repo.jenkins-ci.org? | `export REPO_USERNAME=... REPO_PASSWORD=...; make it-online`. The first test fails with `HTTP 403` if not. | **Yes** - 2026-09-22, operator's community account. `make it-online` passed (6 passed, 1 skipped; the skip is S7). Anonymous AQL returns 403, the same account authenticated returns 200. Online mode needs no private Artifactory mirror. |
 | S1b | Can a **non-admin** service account run it on the internal Artifactory? | With `.env` from `.env.airgapped.example` and the service account in `secrets/`: `docker compose run --rm uc doctor --fetch`. An AQL error names the HTTP status. | **Yes for AQL; downloads not yet checked** - 2026-09-22, operator's own non-admin account (not yet a dedicated service account), local repository. The hand probe below returned 3 results with `repo`, `path` and `name` populated. Still to run: `uc doctor --fetch`, which also downloads POMs and reads files inside each `.hpi`. |
-| S2 | Does AQL work against a **virtual** repository? | Repeat S1b with `ARTIFACTORY_REPOSITORY=<virtual repo>`; compare `Plugins with a POM` with the local repository's count. | **Yes for AQL; count and downloads not yet checked** - 2026-09-22, same account: the hand probe against a virtual repository returned correct results. Still to run: the count comparison (a virtual can aggregate several repositories, remote caches included) and downloads through the virtual, both via `uc doctor --fetch`. |
+| S2 | Does AQL work against a **virtual** repository? | Repeat S1b with `REPO_KEY=<virtual repo>`; compare `Plugins with a POM` with the local repository's count. | **Yes for AQL; count and downloads not yet checked** - 2026-09-22, same account: the hand probe against a virtual repository returned correct results. Still to run: the count comparison (a virtual can aggregate several repositories, remote caches included) and downloads through the virtual, both via `uc doctor --fetch`. |
 | S6 | Full-catalog first run: duration, heap, cache and site size. | `make it-full` (hours); copy `tests/integration/.results/online-*.json` here. If it fails with `OutOfMemoryError`, raise `-Xmx` in the `it-full` target and rerun. | |
 | S7 | Does GitHub GraphQL accept username + token over basic auth? | `export GITHUB_USERNAME=... GITHUB_TOKEN=...` (a token with no scopes), then `make it-online`; `test_github_labels_when_a_token_is_given` must pass rather than skip. | **Yes** - 2026-09-22, operator's account with a scopeless classic token. `make it-online` passed 7/7 with no skip. A direct `POST https://api.github.com/graphql` with `--user name:token` returned 200 and real `repositoryTopics`. |
 
@@ -26,7 +26,7 @@ half of S1b and S2 against the operator's own Artifactory. Still open: the downl
   labels landing in the JSON.
 - **S1b, S2:** a non-admin account needs only Read on the repository. AQL doesn't fail without it;
   it returns an empty `results` list, which the `MIN_PLUGINS` gate then refuses to publish. `uc`
-  ignores the `repo` field in AQL results and downloads from the configured `ARTIFACTORY_REPOSITORY`,
+  ignores the `repo` field in AQL results and downloads from the configured `REPO_KEY`,
   so a virtual repository that reports items under an underlying repository's name is harmless.
 - **Probing AQL by hand.** Put the query in a file rather than typing it inline. cmd and Windows
   PowerShell 5.1 strip the inner double quotes before curl sees them, and Artifactory answers
diff --git a/secrets/README.md b/secrets/README.md
index f24e8b0..4ee9a02 100644
--- a/secrets/README.md
+++ b/secrets/README.md
@@ -5,7 +5,7 @@ Everything here except this file is gitignored. Compose mounts each file under `
 | File | Required | Content |
 |---|---|---|
 | `update-center.key`, `update-center.cert`, `root-ca.crt` | yes | from `make pki-init` (move `root/root-ca.key` offline afterwards) |
-| `artifactory_username`, `artifactory_password` | yes | account that can run AQL on the source repository |
+| `repo_username`, `repo_password` | yes | account that can run AQL on the source repository |
 | `github_username`, `github_token` | online, recommended | GitHub user and token; empty files mean "not set" |
 | `download_proxy_auth` | only with `DOWNLOAD_ACCESS=proxy` | `username:password` of a read-only Artifactory account |
 
diff --git a/tests/integration/airgapped.yaml b/tests/integration/airgapped.yaml
index 75abae6..150565e 100644
--- a/tests/integration/airgapped.yaml
+++ b/tests/integration/airgapped.yaml
@@ -66,7 +66,7 @@ services:
       - -c
       - >-
         uc pki init --out /pki && cp /pki/root-ca.crt /trust/ &&
-        printf uc > /pki/artifactory_username && printf uc-secret > /pki/artifactory_password &&
+        printf uc > /pki/repo_username && printf uc-secret > /pki/repo_password &&
         printf uc:uc-secret > /pki/download_proxy_auth && chmod -R a+rX /pki /trust
     volumes:
       - pki:/pki
@@ -97,8 +97,8 @@ services:
     environment:
       MODE: airgapped
       PUBLIC_URL: "http://web:8080"
-      ARTIFACTORY_URL: "http://artifactory:8081/artifactory/"
-      ARTIFACTORY_REPOSITORY: jenkins
+      REPO_URL: "http://artifactory:8081/artifactory/"
+      REPO_KEY: jenkins
     volumes:
       - uc-data:/data:ro
 
@@ -140,8 +140,9 @@ services:
     environment:
       MODE: airgapped
       PUBLIC_URL: "http://web:8080"
-      ARTIFACTORY_URL: "http://artifactory:8081/artifactory/"
-      ARTIFACTORY_REPOSITORY: jenkins
+      REPO_URL: "http://artifactory:8081/artifactory/"
+      REPO_KEY: jenkins
+      REPO_ADAPTER: "off"  # until the repo-adapter service exists (plan Task 11)
       SECRETS_DIR: /pki
       UC_DATA_DIR: /data
       LTS_OLD: ${LTS_OLD:-x}
diff --git a/tests/integration/online.yaml b/tests/integration/online.yaml
index 0da6d39..4d35636 100644
--- a/tests/integration/online.yaml
+++ b/tests/integration/online.yaml
@@ -7,8 +7,8 @@ services:
     user: "0:0"
     network_mode: none
     environment:
-      ARTIFACTORY_USERNAME: ${ARTIFACTORY_USERNAME:?set ARTIFACTORY_USERNAME (a Jenkins community account)}
-      ARTIFACTORY_PASSWORD: ${ARTIFACTORY_PASSWORD:?set ARTIFACTORY_PASSWORD}
+      REPO_USERNAME: ${REPO_USERNAME:?set REPO_USERNAME (a Jenkins community account)}
+      REPO_PASSWORD: ${REPO_PASSWORD:?set REPO_PASSWORD}
       GITHUB_USERNAME: ${GITHUB_USERNAME:-}
       GITHUB_TOKEN: ${GITHUB_TOKEN:-}
     entrypoint:
@@ -16,8 +16,8 @@ services:
       - -c
       - >-
         uc pki init --out /pki &&
-        printf %s "$$ARTIFACTORY_USERNAME" > /pki/artifactory_username &&
-        printf %s "$$ARTIFACTORY_PASSWORD" > /pki/artifactory_password &&
+        printf %s "$$REPO_USERNAME" > /pki/repo_username &&
+        printf %s "$$REPO_PASSWORD" > /pki/repo_password &&
         printf %s "$$GITHUB_USERNAME" > /pki/github_username &&
         printf %s "$$GITHUB_TOKEN" > /pki/github_token &&
         chmod -R a+rX /pki
diff --git a/web/entrypoint.sh b/web/entrypoint.sh
index 8cc1b5b..25cb767 100644
--- a/web/entrypoint.sh
+++ b/web/entrypoint.sh
@@ -8,10 +8,21 @@ die() { echo "entrypoint: $*" >&2; exit 64; }
 export PUBLIC_URL="${PUBLIC_URL%/}"
 MODE="${MODE:-}"
 case "${MODE}" in
-  online)    default_backend=mirror; default_artifactory=https://repo.jenkins-ci.org/; default_repository=releases ;;
-  airgapped) default_backend=artifactory; default_artifactory=; default_repository= ;;
+  online)    default_backend=mirror; default_repo_url=https://repo.jenkins-ci.org/; default_repo_key=releases ;;
+  airgapped) default_backend=artifactory; default_repo_url=; default_repo_key= ;;
   *) die "MODE must be online or airgapped (got '${MODE}')" ;;
 esac
+REPO_TYPE="${REPO_TYPE:-artifactory}"
+case "${REPO_TYPE}" in
+  artifactory) ;;
+  artifact-keeper)
+    default_repo_url=; default_repo_key=
+    adapter="${REPO_ADAPTER:-on}"
+    case "${adapter,,}" in
+      0|false|no|off) die "REPO_TYPE=artifact-keeper needs REPO_ADAPTER=on" ;;
+    esac ;;
+  *) die "REPO_TYPE must be artifactory or artifact-keeper (got '${REPO_TYPE}')" ;;
+esac
 
 DOWNLOAD_BACKEND="${DOWNLOAD_BACKEND:-${default_backend}}"
 DOWNLOAD_ACCESS="${DOWNLOAD_ACCESS:-redirect}"
@@ -25,11 +36,14 @@ case "${DOWNLOAD_BACKEND}" in
     if [[ -n "${DOWNLOAD_BACKEND_URL:-}" ]]; then
       backend_url="${DOWNLOAD_BACKEND_URL}"
     else
-      artifactory="${ARTIFACTORY_URL:-${default_artifactory}}"
-      repository="${ARTIFACTORY_REPOSITORY:-${default_repository}}"
-      [[ -n "${artifactory}" && -n "${repository}" ]] \
-        || die "set DOWNLOAD_BACKEND_URL, or ARTIFACTORY_URL and ARTIFACTORY_REPOSITORY"
-      backend_url="${artifactory%/}/${repository}"
+      repo_url="${REPO_URL:-${default_repo_url}}"
+      repo_key="${REPO_KEY:-${default_repo_key}}"
+      [[ -n "${repo_url}" && -n "${repo_key}" ]] || die "set DOWNLOAD_BACKEND_URL, or REPO_URL and REPO_KEY"
+      if [[ "${REPO_TYPE}" == artifact-keeper ]]; then
+        backend_url="${repo_url%/}/maven/${repo_key}"
+      else
+        backend_url="${repo_url%/}/${repo_key}"
+      fi
     fi
     defines+=(-D BACKEND_ARTIFACTORY) ;;
   *) die "DOWNLOAD_BACKEND must be mirror or artifactory (got '${DOWNLOAD_BACKEND}')" ;;
````

- [ ] **Step 4: Run the web, routing and unit suites, and check Compose**

Run: `make test compose-check`
Expected: PASS: `192 passed` (unit), 9 `ok:` lines from `entrypoint-checks.sh`, `79 passed` (routing), and `compose-check` exits 0.

- [ ] **Step 5: Commit**

```bash
git add web/entrypoint.sh tests/web/entrypoint-checks.sh tests/routing/compose.yaml tests/routing/test_downloads.py \
  compose.yaml Makefile tests/integration/online.yaml tests/integration/airgapped.yaml .env.airgapped.example \
  secrets/README.md README.md docs/operations.md docs/spikes/README.md
git commit -m "Rename to REPO_* outside Python; Artifact Keeper download URLs in the web tier"
```

### Task 3: Configurable core age for tiers (upstream patch 0002)

Upstream hard-codes `CORE_AGE_DAYS = 400` in `json/TieredUpdateSitesGenerator.java`. The patch reads it from the environment, with `0` meaning no age limit. `uc` already validates it and passes it to the jar (Task 1).

**Files:**
- Create: `uc/patches/0002-core-age-configurable.patch`
- Modify: `uc/Dockerfile` (version label), `Makefile` (`upstream-diff` watches the patched files), `docs/upgrading.md`, `README.md`, `tests/unit/test_generate.py` (the label string in two assertions)

**Interfaces:**
- The jar reads `CORE_AGE_DAYS` from its environment (default 400; `0` makes `isReleaseRecentEnough` return true).
- The image's `UC2_VERSION` environment becomes `3.18.4+patches`.

- [ ] **Step 1: Add the patch**

The Dockerfile already applies every `uc/patches/*.patch` and fails the build if one does not apply.

Create `uc/patches/0002-core-age-configurable.patch`:

```diff
diff --git a/src/main/java/io/jenkins/update_center/json/TieredUpdateSitesGenerator.java b/src/main/java/io/jenkins/update_center/json/TieredUpdateSitesGenerator.java
index 1a7e36c..e3eef9f 100644
--- a/src/main/java/io/jenkins/update_center/json/TieredUpdateSitesGenerator.java
+++ b/src/main/java/io/jenkins/update_center/json/TieredUpdateSitesGenerator.java
@@ -28,6 +28,7 @@ import hudson.util.VersionNumber;
 import io.jenkins.update_center.HPI;
 import io.jenkins.update_center.JenkinsWar;
 import io.jenkins.update_center.MavenRepository;
+import io.jenkins.update_center.util.Environment;
 
 import java.io.IOException;
 import java.time.Instant;
@@ -77,6 +78,9 @@ public class TieredUpdateSitesGenerator extends WithoutSignature {
 
     private static boolean isReleaseRecentEnough(JenkinsWar war) throws IOException {
         Objects.requireNonNull(war, "war");
+        if (CORE_AGE_DAYS == 0) {
+            return true;
+        }
         return war.getTimestampAsDate().toInstant().isAfter(Instant.now().minus(CORE_AGE_DAYS, ChronoUnit.DAYS));
     }
 
@@ -151,5 +155,5 @@ public class TieredUpdateSitesGenerator extends WithoutSignature {
 
     public static final Logger LOGGER = Logger.getLogger(TieredUpdateSitesGenerator.class.getName());
 
-    private static final int CORE_AGE_DAYS = 400;
+    private static final int CORE_AGE_DAYS = Environment.getInteger("CORE_AGE_DAYS", 400);
 }
```

- [ ] **Step 2: Update the label, `upstream-diff`, the upgrade guide and the README**

```diff
diff --git a/Makefile b/Makefile
index f088a1b..594e478 100644
--- a/Makefile
+++ b/Makefile
@@ -97,7 +97,9 @@ UPSTREAM_FILES = site/generate.sh site/generate-htaccess.sh site/test/test.sh \
   src/main/java/io/jenkins/update_center/Signer.java \
   src/main/java/io/jenkins/update_center/Popularities.java \
   src/main/java/io/jenkins/update_center/ArtifactoryRepositoryImpl.java \
-  src/main/java/io/jenkins/update_center/MavenArtifact.java
+  src/main/java/io/jenkins/update_center/MavenArtifact.java \
+  src/main/java/io/jenkins/update_center/HPI.java \
+  src/main/java/io/jenkins/update_center/json/TieredUpdateSitesGenerator.java
 
 # Usage: make upstream-diff TO=3.19   (compares the pinned UC2_VERSION with TO; see docs/upgrading.md)
 upstream-diff: build-uc
@@ -109,5 +111,5 @@ upstream-diff: build-uc
 	  echo "== files uc depends on"; \
 	  git diff update-center2-$(UC2_VERSION) update-center2-$(TO) -- $(UPSTREAM_FILES); \
 	  git checkout -q update-center2-$(TO); \
-	  if git apply --check /patches/*.patch 2>/dev/null; then echo "== patch applies to $(TO)"; \
-	  else echo "== PATCH DOES NOT APPLY to $(TO); check whether Popularities.java already reads POPULARITY_JSON_URL"; fi'
+	  if git apply --check /patches/*.patch 2>/dev/null; then echo "== patches apply to $(TO)"; \
+	  else echo "== PATCHES DO NOT APPLY to $(TO); check whether upstream already reads POPULARITY_JSON_URL (Popularities.java) and CORE_AGE_DAYS (TieredUpdateSitesGenerator.java)"; fi'
diff --git a/README.md b/README.md
index 032cf15..1718288 100644
--- a/README.md
+++ b/README.md
@@ -2,7 +2,7 @@
 
 The same update center Jenkins uses (`updates.jenkins.io`), in two containers:
 
-- **uc**: runs the upstream `update-center2` generator (3.18.4, one small patch), signs everything
+- **uc**: runs the upstream `update-center2` generator (3.18.4, two small patches), signs everything
   with your own PKI, validates each release and publishes it atomically. It keeps the last five
   releases for rollback.
 - **web**: Apache serving the result, including the `?version=` routing to `dynamic-*` tiers, every
diff --git a/docs/upgrading.md b/docs/upgrading.md
index 082013f..3b5d860 100644
--- a/docs/upgrading.md
+++ b/docs/upgrading.md
@@ -19,11 +19,13 @@ every online run regardless.
    | `site/test/test.sh` | port new expectations into `tests/routing/test_upstream_rules.py` |
    | `Main.java`, `DirectoryTreeBuilder.java`, `MetadataWriter.java`, `Signer.java` | removed or renamed options used in `args.py` (`--connection-check-url` is the one upstream's own script does not use) |
    | `MavenArtifact.java` | `DOWNLOADS_ROOT_URL` still controls download URLs |
-   | `ArtifactoryRepositoryImpl.java` | AQL query (`uc/uc/aql.py`), cache layout (`uc/uc/doctor.py`), the Authenticator port check (§10) |
-   | `Popularities.java` | whether the patch still applies, or is no longer needed |
+   | `ArtifactoryRepositoryImpl.java` | AQL query (`uc/uc/aql.py`), cache layout (`uc/uc/doctor.py`), the Authenticator port check (§10), and the three requests the repo-adapter answers (repo-adapter spec §2) |
+   | `HPI.java` | the archive entries the repo-adapter serves: `META-INF/MANIFEST.MF` and `<artifactId>-<version>.jar!/index.jelly` (repo-adapter spec §4.2) |
+   | `Popularities.java` | whether `0001-popularity-configurable-and-optional.patch` still applies, or is no longer needed |
+   | `json/TieredUpdateSitesGenerator.java` | whether `0002-core-age-configurable.patch` still applies, or is no longer needed |
 
 2. Update `uc/Dockerfile`: set `UC2_VERSION`, and set `UC2_COMMIT` to `git rev-parse update-center2-<version>^{commit}`.
-   If upstream has merged the popularity change, delete the patch and drop the `git apply` step; the
+   If upstream has merged both changes, delete the patches and drop the `git apply` step; the
    `jar-build` stage can then take the generator jar from the released
    `update-center2-<version>-bin.zip` on `repo.jenkins-ci.org`, pinned by SHA-256, instead of running
    `mvn verify`. Keep the pinned `git clone` in that stage regardless: it is still where
diff --git a/tests/unit/test_generate.py b/tests/unit/test_generate.py
index 5e21e0d..93d96ce 100644
--- a/tests/unit/test_generate.py
+++ b/tests/unit/test_generate.py
@@ -51,7 +51,7 @@ def tools_for(settings, **jar_options):
 
 def test_airgapped_generation_publishes(tmp_path, secrets_dir):
     settings = make_settings(tmp_path, secrets_dir)
-    rid = generate(settings, tools=tools_for(settings), now=T1, base_env={"UC2_VERSION": "3.18.4+popularity-patch"})
+    rid = generate(settings, tools=tools_for(settings), now=T1, base_env={"UC2_VERSION": "3.18.4+patches"})
     data = settings.data_dir
     live = data / "live"
     assert rid == "20260922T030000Z"
@@ -65,7 +65,7 @@ def test_airgapped_generation_publishes(tmp_path, secrets_dir):
     status = read_status(data)
     assert status["live_release"] == rid
     assert status["last_run"]["result"] == "success"
-    assert status["update_center2"] == "3.18.4+popularity-patch"
+    assert status["update_center2"] == "3.18.4+patches"
     assert status["resources"]["source"] == "bundled"
     assert status["tiers"] == {"weekly": ["2.540"], "stable": ["2.528.1"]}
     assert status["plugin_count"] == 2
diff --git a/uc/Dockerfile b/uc/Dockerfile
index 2b74a19..5ef0b52 100644
--- a/uc/Dockerfile
+++ b/uc/Dockerfile
@@ -58,7 +58,7 @@ RUN chmod 0755 /usr/local/bin/uc /opt/uc/site/generate-htaccess.sh \
 ENV PYTHONPATH=/opt/uc/python \
     PYTHONDONTWRITEBYTECODE=1 \
     UC_APP_DIR=/opt/uc \
-    UC2_VERSION=${UC2_VERSION}+popularity-patch
+    UC2_VERSION=${UC2_VERSION}+patches
 USER 10001:10001
 WORKDIR /data
 ENTRYPOINT ["/usr/bin/tini", "--", "uc"]
```

- [ ] **Step 3: Build the image, which runs upstream's build and tests with both patches**

Run: `make build-uc`
Expected: the `git apply` step succeeds, Maven prints `BUILD SUCCESS` (its `[ERROR]` lines are SpotBugs notes on upstream code and do not fail the build).

- [ ] **Step 4: Confirm the patched class is in the jar, and run the unit tests**

Run:
```bash
docker run --rm --entrypoint python3 jenkins-uc/uc:local -c "import zipfile, glob; jar = sorted(glob.glob('/opt/uc/generator/update-center2-*.jar'))[0]; data = zipfile.ZipFile(jar).read('io/jenkins/update_center/json/TieredUpdateSitesGenerator.class'); print(b'io/jenkins/update_center/util/Environment' in data)"
make test-unit
```
Expected: `True`, then `192 passed`.

- [ ] **Step 5: Commit**

```bash
git add uc/patches/0002-core-age-configurable.patch uc/Dockerfile Makefile docs/upgrading.md README.md tests/unit/test_generate.py
git commit -m "Patch 0002: CORE_AGE_DAYS for upstream's tier generator, 0 for no age limit"
```

### Task 4: Safe POM parsing

A shared module for reading untrusted POMs: refuse DOCTYPE and entity declarations, NUL bytes and anything that is not UTF-8 (the byte check alone misses a UTF-16 declaration), and decide whether a POM belongs to a path (spec §4.2 rule 3). `uc doctor`'s stub-POM check moves onto it. The archive fixtures used from here on are added now too.

**Files:**
- Create: `uc/uc/pom.py`, `tests/support/archivefixture.py`, `tests/unit/test_pom.py`
- Modify: `uc/uc/doctor.py` (`is_stub_pom` uses `pom.safe_root`)

**Interfaces:**
- Produces `pom.Coordinates = tuple[str, str, str]`, `safe_root(data) -> Element | None`, `declares_entities(data) -> bool`, `pom_coordinates(data) -> Coordinates | None`, `unusable_reason(data, expected) -> str | None` (None means usable).
- Produces test support `archivefixture.pom_xml(group, artifact, version, name=...)`, `make_hpi(group, artifact, version, *, layout="maven"|"gradle"|"classes", embed_pom, pom, manifest_group, manifest_artifact, description, date, unix_time)`, `make_war(version, *, embed_pom, date)`, `make_jar(description)`, `extended_timestamp(seconds)`.

- [ ] **Step 1: Write the tests and the fixtures**

Create `tests/support/archivefixture.py`:

```python
"""Plugin and core archives shaped like Maven, Gradle and pre-2015 builds (repo-adapter spec §2)."""
from __future__ import annotations

import io
import struct
import zipfile

DATE = (2024, 5, 1, 12, 30, 0)


def pom_xml(group: str, artifact: str, version: str, name: str = "Test plugin") -> bytes:
    return (f'<project xmlns="http://maven.apache.org/POM/4.0.0"><modelVersion>4.0.0</modelVersion>'
            f"<groupId>{group}</groupId><artifactId>{artifact}</artifactId><version>{version}</version>"
            f"<packaging>hpi</packaging><name>{name}</name></project>").encode()


def manifest(group: str | None, artifact: str | None, version: str, extra: str = "") -> str:
    lines = ["Manifest-Version: 1.0"]
    if group:
        lines.append(f"Group-Id: {group}")
    if artifact:
        lines.append(f"Artifact-Id: {artifact}")
    lines += [f"Short-Name: {artifact or 'x'}", f"Plugin-Version: {version}", "Jenkins-Version: 2.479.1"]
    return "\r\n".join(lines) + "\r\n" + extra + "\r\n"


def extended_timestamp(seconds: int) -> bytes:
    """A 0x5455 extra field carrying a UTC mtime."""
    return struct.pack("<HHB", 0x5455, 5, 1) + struct.pack("<i", seconds)


def _jar(entries: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as jar:
        for name, data in entries.items():
            jar.writestr(name, data)
    return buffer.getvalue()


def make_hpi(group: str, artifact: str, version: str, *, layout: str = "maven", embed_pom: bool = True,
             pom: bytes | None = None, manifest_group: str | None = "", manifest_artifact: str | None = "",
             description: bytes | None = b"<div>An excellent plugin.</div>", date: tuple = DATE,
             unix_time: int | None = None) -> bytes:
    """layout: maven (WEB-INF/lib/<a>.jar), gradle (WEB-INF/lib/<a>-<v>.jar, no META-INF/maven) or
    classes (index.jelly under WEB-INF/classes). manifest_group/artifact default to the real ones."""
    group_in_manifest = group if manifest_group == "" else manifest_group
    artifact_in_manifest = artifact if manifest_artifact == "" else manifest_artifact
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        info = zipfile.ZipInfo("META-INF/MANIFEST.MF", date_time=date)
        if unix_time is not None:
            info.extra = extended_timestamp(unix_time)
        archive.writestr(info, manifest(group_in_manifest, artifact_in_manifest, version))
        if embed_pom and layout != "gradle":
            archive.writestr(f"META-INF/maven/{group}/{artifact}/pom.xml", pom or pom_xml(group, artifact, version))
        inner = {"index.jelly": description} if description is not None else {}
        if layout == "maven":
            archive.writestr(f"WEB-INF/lib/{artifact}.jar", _jar({"x/Plugin.class": b"\xca\xfe", **inner}))
        elif layout == "gradle":
            archive.writestr(f"WEB-INF/lib/{artifact}-{version}.jar", _jar({"x/Plugin.class": b"\xca\xfe", **inner}))
        elif layout == "classes" and description is not None:
            archive.writestr("WEB-INF/classes/index.jelly", description)
    return buffer.getvalue()


def make_war(version: str, *, embed_pom: bool = True, date: tuple = DATE) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr(zipfile.ZipInfo("META-INF/MANIFEST.MF", date_time=date),
                         f"Manifest-Version: 1.0\r\nJenkins-Version: {version}\r\n\r\n")
        if embed_pom:
            archive.writestr("META-INF/maven/org.jenkins-ci.main/jenkins-war/pom.xml",
                             pom_xml("org.jenkins-ci.main", "jenkins-war", version, "Jenkins war"))
        archive.writestr("WEB-INF/lib/jenkins-core.jar", _jar({"hudson/Main.class": b"\xca\xfe"}))
    return buffer.getvalue()


def make_jar(description: bytes | None) -> bytes:
    return _jar({"x/Plugin.class": b"\xca\xfe", **({"index.jelly": description} if description else {})})
```

Create `tests/unit/test_pom.py`:

```python
import pytest
from archivefixture import pom_xml

from uc.pom import pom_coordinates, safe_root, unusable_reason

GIT = ("org.jenkins-ci.plugins", "git", "5.2.0")
NGINX_404 = (b"<html>\r\n<head><title>404 Not Found</title></head>\r\n<body>\r\n<center><h1>404 Not Found</h1>"
             b"</center>\r\n<hr><center>nginx</center>\r\n</body>\r\n</html>\r\n")


def test_coordinates_of_a_real_pom():
    assert pom_coordinates(pom_xml(*GIT)) == GIT


def test_group_and_version_may_come_from_the_parent():
    data = (b"<project><parent><groupId>org.jenkins-ci.plugins</groupId><artifactId>plugin</artifactId>"
            b"<version>5.2.0</version></parent><artifactId>git</artifactId></project>")
    assert pom_coordinates(data) == GIT


def test_a_usable_pom_has_no_reason():
    assert unusable_reason(pom_xml(*GIT), GIT) is None


@pytest.mark.parametrize(
    "data,reason",
    [
        (NGINX_404, "not XML"),  # the saved error page from the operator's test repository
        (b"<html><body>404</body></html>", "not a POM (root element <html>)"),
        (pom_xml("io.jenkins.plugins", "git", "5.2.0"), "declares io.jenkins.plugins:git:5.2.0, not org.jenkins-ci.plugins:git:5.2.0"),
        (b"<project><artifactId>git</artifactId></project>", "does not declare groupId, artifactId and version"),
        (b'<!DOCTYPE p [<!ENTITY x "y">]><project/>', "declares a DOCTYPE or entities"),
        (b"<project><version>${revision}${changelist}</version></project>", "does not declare groupId, artifactId and version"),
    ],
)
def test_unusable_poms_say_why(data, reason):
    assert unusable_reason(data, GIT) == reason


def test_safe_root_refuses_what_the_byte_check_cannot_see():
    assert safe_root(b"<project/>") is not None
    utf16 = '<?xml version="1.0" encoding="UTF-16"?><!DOCTYPE p [<!ENTITY x "y">]><project/>'.encode("utf-16")
    assert safe_root(utf16) is None  # a declaration the ASCII byte check would miss
    assert safe_root("<project/>".encode("utf-16-le")) is None  # UTF-16 without a BOM is full of NULs
    assert safe_root(b"<project>\xff</project>") is None  # not UTF-8
```

- [ ] **Step 2: Run them and watch them fail**

Run: `make test-unit ARGS="-k pom"`
Expected: FAIL with `ModuleNotFoundError: No module named 'uc.pom'`.

- [ ] **Step 3: Implement `pom.py` and move `is_stub_pom` onto it**

Create `uc/uc/pom.py`:

```python
"""Reading untrusted POMs safely, and deciding whether one belongs to a path (repo-adapter spec §4.2 rule 3)."""
from __future__ import annotations

import xml.etree.ElementTree as ET

Coordinates = tuple[str, str, str]  # groupId, artifactId, version


def declares_entities(data: bytes) -> bool:
    return b"<!DOCTYPE" in data or b"<!ENTITY" in data


def safe_root(data: bytes) -> ET.Element | None:
    """The XML root element, or None for anything that is not plain, well-formed UTF-8 XML.

    Real Maven POMs never declare a DOCTYPE or entities; refusing them avoids entity-expansion
    tricks without a third-party parser (ElementTree never resolves external entities). The byte
    check only sees declarations in an ASCII-compatible encoding, so NUL bytes and anything that is
    not valid UTF-8 (UTF-16, for one) are refused before it.
    """
    if b"\x00" in data or declares_entities(data):
        return None
    try:
        data.decode("utf-8")
        return ET.fromstring(data)
    except (UnicodeDecodeError, ET.ParseError):
        return None


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _child_text(element: ET.Element, name: str) -> str | None:
    for child in element:
        if _local(child.tag) == name:
            return (child.text or "").strip() or None
    return None


def pom_coordinates(data: bytes) -> Coordinates | None:
    """(groupId, artifactId, version) a POM declares; groupId and version may come from <parent>."""
    root = safe_root(data)
    if root is None or _local(root.tag) != "project":
        return None
    parent = next((child for child in root if _local(child.tag) == "parent"), None)
    group = _child_text(root, "groupId") or (_child_text(parent, "groupId") if parent is not None else None)
    version = _child_text(root, "version") or (_child_text(parent, "version") if parent is not None else None)
    artifact = _child_text(root, "artifactId")
    if not group or not artifact or not version:
        return None
    return group, artifact, version


def unusable_reason(data: bytes, expected: Coordinates) -> str | None:
    """None when the POM is usable for the expected coordinates; otherwise a short reason for the report."""
    if declares_entities(data):
        return "declares a DOCTYPE or entities"
    root = safe_root(data)
    if root is None:
        return "not XML"
    if _local(root.tag) != "project":
        return f"not a POM (root element <{_local(root.tag)}>)"
    declared = pom_coordinates(data)
    if declared is None:
        return "does not declare groupId, artifactId and version"
    if declared != expected:
        return f"declares {':'.join(declared)}, not {':'.join(expected)}"
    return None
```

```diff
diff --git a/uc/uc/doctor.py b/uc/uc/doctor.py
index 9978bc3..1ae1c44 100644
--- a/uc/uc/doctor.py
+++ b/uc/uc/doctor.py
@@ -6,7 +6,6 @@ import hashlib
 import json
 import urllib.error
 import urllib.request
-import xml.etree.ElementTree as ET
 from dataclasses import asdict, dataclass, field
 from pathlib import Path
 from typing import Callable, Protocol
@@ -17,6 +16,7 @@ from .aql import Gav, Listing
 from .config import Settings
 from .errors import UcError
 from .model import Credentials
+from .pom import safe_root
 from .versions import VersionTool
 
 
@@ -140,13 +140,8 @@ def parse_dependencies(value: str) -> list[tuple[str, str, bool]]:
 
 
 def is_stub_pom(data: bytes) -> bool:
-    # Real Maven POMs never declare a DOCTYPE or entities; refusing them avoids entity-expansion
-    # tricks without a third-party parser (ElementTree never resolves external entities).
-    if b"<!DOCTYPE" in data or b"<!ENTITY" in data:
-        return True
-    try:
-        root = ET.fromstring(data)
-    except ET.ParseError:
+    root = safe_root(data)
+    if root is None:
         return True
     for child in root:
         if child.tag.rsplit("}", 1)[-1] == "name" and (child.text or "").strip():
```

- [ ] **Step 4: Run the unit tests**

Run: `make test-unit`
Expected: PASS, `202 passed` (the existing `test_is_stub_pom` still passes).

- [ ] **Step 5: Commit**

```bash
git add uc/uc/pom.py uc/uc/doctor.py tests/support/archivefixture.py tests/unit/test_pom.py
git commit -m "Safe parsing of untrusted POMs, shared by doctor and the repo-adapter"
```

### Task 5: Reading untrusted archives

The adapter reads a few small entries from each archive: its manifest, its embedded POM, its description, and its date. Every read is bounded, and the central directory is size-checked from the end records before `zipfile` parses it, zip64 included.

**Files:**
- Create: `uc/uc/repo_adapter/__init__.py`, `uc/uc/repo_adapter/archive.py`, `tests/unit/test_repo_adapter_archive.py`

**Interfaces:**
- Consumes `pom.Coordinates`, `doctor.parse_manifest`.
- Produces `ArchiveFacts(manifest, coordinates, pom, description, released)`, `ArchiveError`, constants `SMALL_ENTRY_LIMIT`, `INNER_JAR_LIMIT`, `CENTRAL_DIRECTORY_LIMIT`, `MANIFEST`, type `EntryReader = Callable[[str, int], bytes | None]`, and functions `central_directory_size`, `open_archive`, `read_entry`, `zip_reader`, `archive_date`, `plausible(released, repository_time) -> str | None` (None means use the archive date), `manifest_coordinates`, `jar_description`, `find_description`, `extract_facts(read, path_coordinates, *, plugin, released)`.

- [ ] **Step 1: Write the tests**

Create `tests/unit/test_repo_adapter_archive.py`:

```python
import datetime as dt
import io
import struct
import zipfile

import pytest
from archivefixture import make_hpi, make_war, pom_xml

from uc.repo_adapter.archive import (
    ArchiveError,
    archive_date,
    central_directory_size,
    extract_facts,
    find_description,
    manifest_coordinates,
    open_archive,
    plausible,
    read_entry,
    zip_reader,
)

UTC = dt.timezone.utc
GIT = ("org.jenkins-ci.plugins", "git", "5.2.0")


def zip_of(entries: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, data in entries.items():
            archive.writestr(name, data)
    return buffer.getvalue()


def as_zip64(data: bytes) -> bytes:
    """Replace the end record with zip64 records, as archives with over 65535 entries have."""
    eocd = data.rfind(b"PK\x05\x06")
    entries, size, offset = struct.unpack("<HII", data[eocd + 10:eocd + 20])
    body = data[:eocd]
    record = b"PK\x06\x06" + struct.pack("<QHHIIQQQQ", 44, 45, 45, 0, 0, entries, entries, size, offset)
    locator = b"PK\x06\x07" + struct.pack("<IQI", 0, len(body), 1)
    end = b"PK\x05\x06" + struct.pack("<HHHHIIH", 0, 0, 0xFFFF, 0xFFFF, 0xFFFFFFFF, 0xFFFFFFFF, 0)
    return body + record + locator + end


def test_central_directory_size_plain_and_zip64():
    data = zip_of({"a.txt": b"hello", "b.txt": b"world"})
    size = central_directory_size(io.BytesIO(data))
    assert 0 < size < len(data)
    assert central_directory_size(io.BytesIO(as_zip64(data))) == size
    assert open_archive(io.BytesIO(as_zip64(data))).read("a.txt") == b"hello"


def test_open_archive_refuses_non_zips_and_huge_central_directories():
    with pytest.raises(ArchiveError, match="not a zip archive"):
        open_archive(io.BytesIO(b"<html>404</html>"))
    data = bytearray(zip_of({"a.txt": b"x"}))
    eocd = data.rfind(b"PK\x05\x06")
    data[eocd + 12:eocd + 16] = struct.pack("<I", 17 * 1024 * 1024)
    with pytest.raises(ArchiveError, match="central directory of 17825792 bytes"):
        open_archive(io.BytesIO(bytes(data)))


def test_read_entry_is_bounded():
    archive = open_archive(io.BytesIO(zip_of({"small": b"x" * 10, "big": b"y" * 5000})))
    assert read_entry(archive, "small", 10) == b"x" * 10
    assert read_entry(archive, "missing", 10) is None
    with pytest.raises(ArchiveError, match="big is larger than 100 bytes"):
        read_entry(archive, "big", 100)


def test_a_lying_size_header_is_refused():
    data = bytearray(zip_of({"entry": b"z" * 4096}))  # stored, so sizes are easy to patch
    central = data.find(b"PK\x01\x02")
    data[central + 20:central + 28] = struct.pack("<II", 16, 16)  # claim 16 bytes, compressed and not
    with pytest.raises(ArchiveError):
        read_entry(open_archive(io.BytesIO(bytes(data))), "entry", 1024)


def test_archive_date_prefers_the_extended_timestamp():
    stamp = int(dt.datetime(2023, 11, 18, 20, 14, tzinfo=UTC).timestamp())
    with_unix = open_archive(io.BytesIO(make_hpi(*GIT, unix_time=stamp)))
    assert archive_date(with_unix) == dt.datetime(2023, 11, 18, 20, 14, tzinfo=UTC)
    dos_only = open_archive(io.BytesIO(make_hpi(*GIT, date=(2013, 2, 25, 16, 44, 0))))
    assert archive_date(dos_only) == dt.datetime(2013, 2, 25, 16, 44, tzinfo=UTC)
    assert archive_date(open_archive(io.BytesIO(zip_of({"x": b"y"})))) is None


@pytest.mark.parametrize(
    "released,reason",
    [
        (dt.datetime(2024, 5, 1, tzinfo=UTC), None),
        (dt.datetime(2026, 9, 23, 6, tzinfo=UTC), None),  # up to a day after the upload: time zones
        (dt.datetime(1980, 2, 1, tzinfo=UTC), "archive date 1980-02-01 is before 2004"),
        (dt.datetime(2026, 9, 25, tzinfo=UTC), "archive date 2026-09-25 is after the upload"),
        (None, "archive has no readable date"),
    ],
)
def test_plausible(released, reason):
    assert plausible(released, dt.datetime(2026, 9, 22, 23, tzinfo=UTC)) == reason


def test_manifest_coordinates():
    text = b"Manifest-Version: 1.0\r\nGroup-Id: io.jenkins.plugins\r\nArtifact-Id: caffeine-api\r\nPlugin-Version: 3.2.4 (private-x)\r\n\r\n"
    assert manifest_coordinates(text) == ("io.jenkins.plugins", "caffeine-api", "3.2.4")
    assert manifest_coordinates(b"Manifest-Version: 1.0\r\nShort-Name: old\r\nPlugin-Version: 1.0\r\n\r\n") is None
    assert manifest_coordinates(None) is None


@pytest.mark.parametrize("layout", ["maven", "gradle", "classes"])
def test_description_is_found_wherever_the_build_put_it(layout):
    archive = open_archive(io.BytesIO(make_hpi(*GIT, layout=layout)))
    assert find_description(zip_reader(archive), "git", "5.2.0") == b"<div>An excellent plugin.</div>"


def test_no_description_anywhere():
    archive = open_archive(io.BytesIO(make_hpi(*GIT, description=None)))
    assert find_description(zip_reader(archive), "git", "5.2.0") is None


def test_extract_facts_for_a_plugin_uses_the_manifest_coordinates():
    data = make_hpi("io.jenkins.plugins", "caffeine-api", "3.2.4")
    archive = open_archive(io.BytesIO(data))
    facts = extract_facts(zip_reader(archive), ("org.jenkins-ci.plugins", "caffeine-api", "3.2.4"),
                          plugin=True, released=archive_date(archive))
    assert facts.coordinates == ("io.jenkins.plugins", "caffeine-api", "3.2.4")
    assert facts.pom == pom_xml("io.jenkins.plugins", "caffeine-api", "3.2.4")  # found under the real group
    assert facts.description == b"<div>An excellent plugin.</div>"
    assert b"Group-Id: io.jenkins.plugins" in facts.manifest
    assert facts.released == dt.datetime(2024, 5, 1, 12, 30, tzinfo=UTC)


def test_extract_facts_for_a_gradle_plugin_has_no_pom():
    archive = open_archive(io.BytesIO(make_hpi(*GIT, layout="gradle")))
    facts = extract_facts(zip_reader(archive), GIT, plugin=True, released=None)
    assert facts.pom is None
    assert facts.description == b"<div>An excellent plugin.</div>"


def test_extract_facts_for_a_war_uses_the_path():
    archive = open_archive(io.BytesIO(make_war("2.583")))
    facts = extract_facts(zip_reader(archive), ("org.jenkins-ci.main", "jenkins-war", "2.583"), plugin=False,
                          released=None)
    assert facts.coordinates is None
    assert facts.pom == pom_xml("org.jenkins-ci.main", "jenkins-war", "2.583", "Jenkins war")
    assert facts.description is None
```

- [ ] **Step 2: Run them and watch them fail**

Run: `make test-unit ARGS="-k repo_adapter_archive"`
Expected: FAIL with `ModuleNotFoundError: No module named 'uc.repo_adapter'`.

- [ ] **Step 3: Implement the package and `archive.py`**

Create `uc/uc/repo_adapter/__init__.py`:

```python
"""The repo-adapter service: upstream's Artifactory requests, answered from Artifactory or Artifact Keeper,
with POMs, descriptions and release dates filled in from the archives (docs/superpowers/specs/2026-09-22-repo-adapter-design.md)."""
```

Create `uc/uc/repo_adapter/archive.py`:

```python
"""The few small entries the adapter reads from untrusted archives (repo-adapter spec §4.2, §5.2)."""
from __future__ import annotations

import datetime as dt
import io
import struct
import zipfile
from dataclasses import dataclass
from typing import BinaryIO, Callable

from ..doctor import parse_manifest
from ..errors import UcError
from ..pom import Coordinates

SMALL_ENTRY_LIMIT = 1024 * 1024  # a POM, MANIFEST.MF or index.jelly
INNER_JAR_LIMIT = 64 * 1024 * 1024
CENTRAL_DIRECTORY_LIMIT = 16 * 1024 * 1024
EARLIEST_RELEASE = dt.datetime(2004, 1, 1, tzinfo=dt.timezone.utc)
MANIFEST = "META-INF/MANIFEST.MF"
_EOCD = b"PK\x05\x06"
_ZIP64_LOCATOR = b"PK\x06\x07"
_ZIP64_EOCD = b"PK\x06\x06"

EntryReader = Callable[[str, int], "bytes | None"]


class ArchiveError(UcError):
    pass


@dataclass(frozen=True)
class ArchiveFacts:
    """What the listing needs from one archive, read once and cached by its sha256."""

    manifest: bytes | None
    coordinates: Coordinates | None  # from the manifest; None for a WAR or an incomplete manifest
    pom: bytes | None  # META-INF/maven/<groupId>/<artifactId>/pom.xml
    description: bytes | None  # index.jelly (plugins only)
    released: dt.datetime | None  # rule 9's archive date, before the plausibility check


def central_directory_size(stream: BinaryIO) -> int:
    """Size in bytes of a zip's central directory, read from its end records without parsing it."""
    end = stream.seek(0, io.SEEK_END)
    tail_length = min(end, 22 + 65535)
    stream.seek(end - tail_length)
    tail = stream.read(tail_length)
    at = tail.rfind(_EOCD)
    if at < 0 or len(tail) - at < 22:
        raise ArchiveError("not a zip archive (no end of central directory record)")
    entries, size = struct.unpack("<HI", tail[at + 10:at + 16])
    if size != 0xFFFFFFFF and entries != 0xFFFF:
        return size
    locator = tail[at - 20:at] if at >= 20 else b""
    if not locator.startswith(_ZIP64_LOCATOR):
        raise ArchiveError("zip64 archive without a zip64 end record locator")
    (record_offset,) = struct.unpack("<Q", locator[8:16])
    stream.seek(record_offset)
    record = stream.read(56)
    if len(record) < 56 or not record.startswith(_ZIP64_EOCD):
        raise ArchiveError("zip64 end of central directory record not found")
    (size,) = struct.unpack("<Q", record[40:48])
    return size


def open_archive(stream: BinaryIO) -> zipfile.ZipFile:
    """A ZipFile over a seekable stream, refusing central directories over CENTRAL_DIRECTORY_LIMIT."""
    size = central_directory_size(stream)
    if size > CENTRAL_DIRECTORY_LIMIT:
        raise ArchiveError(f"central directory of {size} bytes exceeds {CENTRAL_DIRECTORY_LIMIT}")
    stream.seek(0)
    try:
        return zipfile.ZipFile(stream)
    except (zipfile.BadZipFile, zipfile.LargeZipFile, OSError, ValueError) as exc:
        raise ArchiveError(f"unreadable archive: {exc}") from exc


def read_entry(archive: zipfile.ZipFile, name: str, limit: int) -> bytes | None:
    """The entry's bytes, None when absent; ArchiveError when it exceeds limit, whatever its header says."""
    try:
        info = archive.getinfo(name)
    except KeyError:
        return None
    if info.file_size > limit:
        raise ArchiveError(f"{name} is larger than {limit} bytes")
    try:
        with archive.open(info) as entry:
            data = entry.read(limit + 1)
    except (zipfile.BadZipFile, OSError, ValueError, NotImplementedError) as exc:
        raise ArchiveError(f"unreadable entry {name}: {exc}") from exc
    if len(data) > limit:
        raise ArchiveError(f"{name} is larger than {limit} bytes")
    return data


def zip_reader(archive: zipfile.ZipFile) -> EntryReader:
    return lambda name, limit: read_entry(archive, name, limit)


def _extended_mtime(extra: bytes) -> dt.datetime | None:
    """The mtime of a 0x5455 extended-timestamp field, which is UTC, when the entry carries one."""
    at = 0
    while at + 4 <= len(extra):
        header, size = struct.unpack("<HH", extra[at:at + 4])
        body = extra[at + 4:at + 4 + size]
        if header == 0x5455 and len(body) >= 5 and body[0] & 1:
            (seconds,) = struct.unpack("<i", body[1:5])
            return dt.datetime.fromtimestamp(seconds, dt.timezone.utc)
        at += 4 + size
    return None


def archive_date(archive: zipfile.ZipFile) -> dt.datetime | None:
    """META-INF/MANIFEST.MF's time: its UTC extended timestamp, else its DOS time read as UTC."""
    try:
        info = archive.getinfo(MANIFEST)
    except KeyError:
        return None
    extended = _extended_mtime(info.extra)
    if extended is not None:
        return extended
    try:
        return dt.datetime(*info.date_time, tzinfo=dt.timezone.utc)
    except ValueError:
        return None


def plausible(released: dt.datetime | None, repository_time: dt.datetime) -> str | None:
    """None when the archive date may be used (rule 9); otherwise why the repository's time is used."""
    if released is None:
        return "archive has no readable date"
    if released < EARLIEST_RELEASE:
        return f"archive date {released:%Y-%m-%d} is before 2004"
    if released > repository_time + dt.timedelta(days=1):
        return f"archive date {released:%Y-%m-%d} is after the upload"
    return None


def manifest_coordinates(manifest: bytes | None) -> Coordinates | None:
    if manifest is None:
        return None
    attributes = parse_manifest(manifest.decode("utf-8", "replace"))
    group, artifact = attributes.get("Group-Id"), attributes.get("Artifact-Id")
    version = attributes.get("Plugin-Version", "").split(" ")[0]
    if not group or not artifact or not version:
        return None
    return group, artifact, version


def jar_description(jar: bytes) -> bytes | None:
    return read_entry(open_archive(io.BytesIO(jar)), "index.jelly", SMALL_ENTRY_LIMIT)


def find_description(read: EntryReader, artifact_id: str, version: str) -> bytes | None:
    """index.jelly where Maven, Gradle and pre-2015 builds put it (rule 6)."""
    for inner in (f"WEB-INF/lib/{artifact_id}.jar", f"WEB-INF/lib/{artifact_id}-{version}.jar"):
        jar = read(inner, INNER_JAR_LIMIT)
        if jar is not None:
            description = jar_description(jar)
            if description is not None:
                return description
    return read("WEB-INF/classes/index.jelly", SMALL_ENTRY_LIMIT)


def extract_facts(read: EntryReader, path_coordinates: Coordinates, *, plugin: bool,
                  released: dt.datetime | None) -> ArchiveFacts:
    """Read an archive's facts through `read`, which serves entries from a local zip or `!/` requests."""
    manifest = read(MANIFEST, SMALL_ENTRY_LIMIT)
    coordinates = manifest_coordinates(manifest) if plugin else None
    group, artifact, version = coordinates or path_coordinates
    pom = read(f"META-INF/maven/{group}/{artifact}/pom.xml", SMALL_ENTRY_LIMIT)
    description = find_description(read, artifact, version) if plugin else None
    return ArchiveFacts(manifest, coordinates, pom, description, released)
```

- [ ] **Step 4: Run the unit tests**

Run: `make test-unit`
Expected: PASS, `220 passed`.

- [ ] **Step 5: Commit**

```bash
git add uc/uc/repo_adapter/__init__.py uc/uc/repo_adapter/archive.py tests/unit/test_repo_adapter_archive.py
git commit -m "repo-adapter: bounded reads from untrusted archives, archive dates, descriptions"
```

### Task 6: The content-addressed cache

What the adapter learns about a file is kept under the file's sha256, so it never goes stale. An archive's facts are complete once `released` or `released.none` exists, which is written last.

**Files:**
- Create: `uc/uc/repo_adapter/cache.py`, `tests/unit/test_repo_adapter_cache.py`

**Interfaces:**
- Consumes `ArchiveFacts`.
- Produces `Cache(root)` with `facts`/`store_facts`, `sha1`/`store_sha1`, `pom_verdict`/`store_pom_verdict` (`''` usable, a reason, or None when unchecked), `description`/`has_description`/`store_description` (for repository `.jar` files), `touch`, and `prune(now=None, max_age_days=30) -> int`.

- [ ] **Step 1: Write the tests**

Create `tests/unit/test_repo_adapter_cache.py`:

```python
import datetime as dt
import os
import time

import pytest

from uc.repo_adapter.archive import ArchiveFacts
from uc.repo_adapter.cache import Cache

SHA = "ab" + "0" * 62
FACTS = ArchiveFacts(b"Manifest-Version: 1.0\n", ("io.jenkins.plugins", "caffeine-api", "3.2.4"), b"<project/>",
                     b"<div>d</div>", dt.datetime(2024, 5, 1, 12, 30, tzinfo=dt.timezone.utc))


def test_facts_round_trip(tmp_path):
    cache = Cache(tmp_path)
    assert cache.facts(SHA) is None
    cache.store_facts(SHA, FACTS)
    assert cache.facts(SHA) == FACTS
    assert (tmp_path / "ab" / SHA / "pom.xml").read_bytes() == b"<project/>"


def test_absent_facts_are_remembered_as_markers(tmp_path):
    cache = Cache(tmp_path)
    cache.store_facts(SHA, ArchiveFacts(None, None, None, None, None))
    assert cache.facts(SHA) == ArchiveFacts(None, None, None, None, None)
    assert (tmp_path / "ab" / SHA / "pom.none").is_file()
    assert (tmp_path / "ab" / SHA / "released.none").is_file()


def test_facts_without_the_final_marker_are_incomplete(tmp_path):
    cache = Cache(tmp_path)
    cache.store_facts(SHA, FACTS)
    (tmp_path / "ab" / SHA / "released").unlink()  # as if the process died before the last write
    assert cache.facts(SHA) is None


def test_sha1_verdict_and_description(tmp_path):
    cache = Cache(tmp_path)
    assert (cache.sha1(SHA), cache.pom_verdict(SHA)) == (None, None)
    cache.store_sha1(SHA, "a" * 40)
    cache.store_pom_verdict(SHA, None)
    assert (cache.sha1(SHA), cache.pom_verdict(SHA)) == ("a" * 40, "")
    cache.store_pom_verdict(SHA, "not XML")
    assert cache.pom_verdict(SHA) == "not XML"
    assert not cache.has_description(SHA)
    cache.store_description(SHA, None)
    assert cache.has_description(SHA) and cache.description(SHA) is None


def test_rejects_keys_that_are_not_sha256(tmp_path):
    with pytest.raises(ValueError):
        Cache(tmp_path).facts("../../etc/passwd")


def test_prune_removes_entries_untouched_for_30_days(tmp_path):
    cache = Cache(tmp_path)
    old, fresh = "cd" + "1" * 62, "ef" + "2" * 62
    cache.store_facts(old, FACTS)
    cache.store_facts(fresh, FACTS)
    long_ago = time.time() - 31 * 86400
    os.utime(tmp_path / "cd" / old, (long_ago, long_ago))
    assert cache.prune() == 1
    assert cache.facts(old) is None and cache.facts(fresh) == FACTS
```

- [ ] **Step 2: Run them and watch them fail**

Run: `make test-unit ARGS="-k repo_adapter_cache"`
Expected: FAIL with `ModuleNotFoundError: No module named 'uc.repo_adapter.cache'`.

- [ ] **Step 3: Implement the cache**

Create `uc/uc/repo_adapter/cache.py`:

```python
"""Content-addressed cache of small derived files, keyed by sha256 (repo-adapter spec §5.1)."""
from __future__ import annotations

import datetime as dt
import os
import re
import shutil
import tempfile
import time
from pathlib import Path

from .archive import ArchiveFacts

_SHA256 = re.compile(r"^[0-9a-f]{64}$")
PRUNE_AFTER_DAYS = 30


class Cache:
    """<root>/<sha256[:2]>/<sha256>/ holds whatever is known about that content.

    An archive's facts are complete once `released` or `released.none` exists: it is written last.
    """

    def __init__(self, root: Path) -> None:
        self.root = root

    def _dir(self, sha256: str) -> Path:
        if not _SHA256.match(sha256):
            raise ValueError(f"not a sha256: {sha256!r}")
        return self.root / sha256[:2] / sha256

    def _read(self, sha256: str, name: str) -> bytes | None:
        try:
            return (self._dir(sha256) / name).read_bytes()
        except FileNotFoundError:
            return None

    def _write(self, sha256: str, name: str, data: bytes) -> None:
        directory = self._dir(sha256)
        directory.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=directory, prefix=".tmp-")
        with os.fdopen(fd, "wb") as out:
            out.write(data)
        os.replace(tmp, directory / name)

    def touch(self, sha256: str) -> None:
        directory = self._dir(sha256)
        if directory.is_dir():
            os.utime(directory)

    def _optional(self, sha256: str, name: str, marker: str, data: bytes | None) -> None:
        """Write data as name, or the empty marker file when it is None: "known absent"."""
        if data is None:
            self._write(sha256, marker, b"")
        else:
            self._write(sha256, name, data)

    def store_facts(self, sha256: str, facts: ArchiveFacts) -> None:
        coordinates = ":".join(facts.coordinates).encode() if facts.coordinates else None
        self._optional(sha256, "MANIFEST.MF", "MANIFEST.none", facts.manifest)
        self._optional(sha256, "coordinates", "coordinates.none", coordinates)
        self._optional(sha256, "pom.xml", "pom.none", facts.pom)
        self._optional(sha256, "index.jelly", "index.none", facts.description)
        released = facts.released.isoformat().encode() if facts.released else None
        self._optional(sha256, "released", "released.none", released)  # last: marks the entry complete

    def facts(self, sha256: str) -> ArchiveFacts | None:
        directory = self._dir(sha256)
        if not (directory / "released").is_file() and not (directory / "released.none").is_file():
            return None
        coordinates = self._read(sha256, "coordinates")
        released = self._read(sha256, "released")
        self.touch(sha256)
        group, artifact, version = coordinates.decode().split(":", 2) if coordinates else (None, None, None)
        return ArchiveFacts(
            manifest=self._read(sha256, "MANIFEST.MF"),
            coordinates=(group, artifact, version) if coordinates else None,
            pom=self._read(sha256, "pom.xml"),
            description=self._read(sha256, "index.jelly"),
            released=dt.datetime.fromisoformat(released.decode()) if released else None,
        )

    def sha1(self, sha256: str) -> str | None:
        data = self._read(sha256, "sha1")
        return data.decode() if data else None

    def store_sha1(self, sha256: str, sha1: str) -> None:
        self._write(sha256, "sha1", sha1.encode())

    def pom_verdict(self, sha256: str) -> str | None:
        """'' for a usable POM, a reason for an unusable one, None when not yet checked."""
        data = self._read(sha256, "pom-verdict")
        return data.decode() if data is not None else None

    def store_pom_verdict(self, sha256: str, reason: str | None) -> None:
        self._write(sha256, "pom-verdict", (reason or "").encode())

    def description(self, sha256: str) -> bytes | None:
        """index.jelly extracted from a repository .jar (rule 6), when cached."""
        return self._read(sha256, "jar-index.jelly")

    def has_description(self, sha256: str) -> bool:
        directory = self._dir(sha256)
        return (directory / "jar-index.jelly").is_file() or (directory / "jar-index.none").is_file()

    def store_description(self, sha256: str, data: bytes | None) -> None:
        self._optional(sha256, "jar-index.jelly", "jar-index.none", data)

    def prune(self, now: float | None = None, max_age_days: int = PRUNE_AFTER_DAYS) -> int:
        """Remove content directories untouched for max_age_days; returns how many."""
        cutoff = (now if now is not None else time.time()) - max_age_days * 86400
        removed = 0
        if not self.root.is_dir():
            return 0
        for shard in self.root.iterdir():
            if not shard.is_dir() or len(shard.name) != 2:
                continue
            for entry in shard.iterdir():
                if entry.is_dir() and _SHA256.match(entry.name) and entry.stat().st_mtime < cutoff:
                    shutil.rmtree(entry, ignore_errors=True)
                    removed += 1
        return removed
```

- [ ] **Step 4: Run the unit tests**

Run: `make test-unit`
Expected: PASS, `226 passed`.

- [ ] **Step 5: Commit**

```bash
git add uc/uc/repo_adapter/cache.py tests/unit/test_repo_adapter_cache.py
git commit -m "repo-adapter: content-addressed cache of derived files"
```

### Task 7: The Artifactory and Artifact Keeper backends

One HTTP client with a TLS context from `REPO_CA_FILE`, which drops `Authorization` on a redirect to another host. A seekable reader fetches only the byte ranges `zipfile` asks for. The two backends expose the same five operations. The stub server learns function routes, so tests can honour `Range` and redirect.

**Files:**
- Create: `uc/uc/repo_adapter/backends.py`, `tests/unit/test_repo_adapter_backends.py`
- Modify: `tests/support/httpstub.py`

**Interfaces:**
- Consumes `archive` (Task 5) and `aql.AQL_TEMPLATE`.
- Produces `RepoEntry(directory, name, sha256, sha1, size, created, modified)` with `.path` and `.extension`; `BackendError`; `Http(ca_file)` with `request(...)`/`download(...)`; `RangeReader`; `artifactory_time(datetime) -> str` and `parse_time(str) -> datetime`; the `Backend` protocol (`type`, `list()`, `get(path)`, `sha1(entry)`, `facts(entry, path_coordinates, *, plugin)`, `jar_description(jar_path)`); `ArtifactoryBackend(http, url, api_url, key, username, password, max_archive_bytes)` and `ArtifactKeeperBackend(http, url, key, username, token, tmp_dir, max_archive_bytes)`.
- Produces test support `httpstub.serve_bytes(data, *, ranges=True)`; `StubServer` routes may now be callables `(headers, body) -> (status, headers, body)`.

- [ ] **Step 1: Extend the stub server and write the tests**

```diff
diff --git a/tests/support/httpstub.py b/tests/support/httpstub.py
index 265967b..806ef9c 100644
--- a/tests/support/httpstub.py
+++ b/tests/support/httpstub.py
@@ -1,12 +1,38 @@
-"""A threaded HTTP server that answers with canned responses and records every request."""
+"""A threaded HTTP server that answers with canned responses and records every request.
+
+A route is (status, headers, body), or a callable taking (headers, body) and returning one.
+"""
 from __future__ import annotations
 
+import re
 import threading
 from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
+from typing import Callable, Union
+
+Reply = tuple[int, dict[str, str], bytes]
+Route = Union[Reply, Callable[[dict[str, str], bytes], Reply]]
+_RANGE = re.compile(r"^bytes=(\d*)-(\d*)$")
+
+
+def serve_bytes(data: bytes, *, ranges: bool = True) -> Callable[[dict[str, str], bytes], Reply]:
+    """A route serving data, honouring single `Range: bytes=a-b` / `bytes=-n` requests when ranges is set."""
+
+    def route(headers: dict[str, str], body: bytes) -> Reply:
+        match = _RANGE.match(headers.get("Range", "")) if ranges else None
+        if not match:
+            return 200, {}, data
+        first, last = match.groups()
+        if first == "":
+            start, end = max(0, len(data) - int(last)), len(data) - 1
+        else:
+            start, end = int(first), min(int(last) if last else len(data) - 1, len(data) - 1)
+        return 206, {"Content-Range": f"bytes {start}-{end}/{len(data)}"}, data[start:end + 1]
+
+    return route
 
 
 class StubServer:
-    def __init__(self, routes: dict[tuple[str, str], tuple[int, dict[str, str], bytes]]) -> None:
+    def __init__(self, routes: dict[tuple[str, str], Route]) -> None:
         self.routes = routes
         self.requests: list[tuple[str, str, dict[str, str], bytes]] = []
         stub = self
@@ -15,10 +41,12 @@ class StubServer:
             def _handle(self) -> None:
                 length = int(self.headers.get("Content-Length") or 0)
                 body = self.rfile.read(length) if length else b""
-                stub.requests.append((self.command, self.path, dict(self.headers), body))
-                status, headers, payload = stub.routes.get((self.command, self.path), (404, {}, b""))
+                headers = dict(self.headers)
+                stub.requests.append((self.command, self.path, headers, body))
+                route = stub.routes.get((self.command, self.path), (404, {}, b""))
+                status, reply_headers, payload = route(headers, body) if callable(route) else route
                 self.send_response(status)
-                for key, value in headers.items():
+                for key, value in reply_headers.items():
                     self.send_header(key, value)
                 self.send_header("Content-Length", str(len(payload)))
                 self.end_headers()
```

Create `tests/unit/test_repo_adapter_backends.py`:

```python
import base64
import datetime as dt
import hashlib
import io
import json
import zipfile

import pytest
from archivefixture import make_hpi, make_jar
from httpstub import StubServer, serve_bytes

from uc.aql import AQL_TEMPLATE
from uc.repo_adapter.backends import (
    ArtifactKeeperBackend,
    ArtifactoryBackend,
    BackendError,
    Http,
    RepoEntry,
    artifactory_time,
)

UTC = dt.timezone.utc
GIT = ("org.jenkins-ci.plugins", "git", "5.2.0")
HPI_PATH = "org/jenkins-ci/plugins/git/5.2.0/git-5.2.0.hpi"
BASIC = "Basic " + base64.b64encode(b"svc:pw").decode()


def artifactory(stub, max_archive=1024 * 1024):
    return ArtifactoryBackend(Http(None), stub.url + "/artifactory/", stub.url + "/artifactory/api/", "jenkins",
                              "svc", "pw", max_archive)


def keeper(stub, tmp_path, max_archive=1024 * 1024):
    return ArtifactKeeperBackend(Http(None), stub.url + "/", "jenkins-prod-local", "zac", "tok", tmp_path / "tmp",
                                 max_archive)


def entry(data, path=HPI_PATH):
    directory, _, name = path.rpartition("/")
    return RepoEntry(directory, name, hashlib.sha256(data).hexdigest(), None, len(data),
                     "2026-09-22T23:09:48.946Z", "2026-09-22T23:09:48.946Z")


def test_artifactory_time():
    assert artifactory_time(dt.datetime(2026, 9, 22, 23, 9, 48, 946941, tzinfo=UTC)) == "2026-09-22T23:09:48.946Z"


def test_artifactory_listing_sends_the_upstream_query_with_basic_auth():
    results = [{"repo": "jenkins", "path": "org/jenkins-ci/plugins/git/5.2.0", "name": "git-5.2.0.hpi",
                "created": "2026-09-01T00:00:00.000Z", "modified": "2026-09-02T00:00:00.000Z",
                "sha256": "a" * 64, "actual_sha1": "b" * 40, "size": 12}]
    routes = {("POST", "/artifactory/api/search/aql"): (200, {}, json.dumps({"results": results}).encode())}
    with StubServer(routes) as stub:
        listed = artifactory(stub).list()
    assert listed == [RepoEntry("org/jenkins-ci/plugins/git/5.2.0", "git-5.2.0.hpi", "a" * 64, "b" * 40, 12,
                                "2026-09-01T00:00:00.000Z", "2026-09-02T00:00:00.000Z")]
    method, path, headers, body = stub.requests[0]
    assert body == (AQL_TEMPLATE % "jenkins").encode()
    assert headers["Authorization"] == BASIC


def test_artifactory_listing_failures_are_backend_errors():
    with StubServer({("POST", "/artifactory/api/search/aql"): (403, {}, b"denied")}) as stub:
        with pytest.raises(BackendError, match="HTTP 403"):
            artifactory(stub).list()


def test_artifactory_files_entries_and_sha1():
    routes = {
        ("GET", "/artifactory/jenkins/org/x/1/x-1.pom"): (200, {}, b"<project/>"),
        ("GET", f"/artifactory/jenkins/{HPI_PATH}!/META-INF/MANIFEST.MF"): (200, {}, b"Manifest-Version: 1.0"),
    }
    with StubServer(routes) as stub:
        backend = artifactory(stub)
        assert backend.get("org/x/1/x-1.pom") == b"<project/>"
        assert backend.get("org/x/1/missing.pom") is None
        assert backend.entry(HPI_PATH, "META-INF/MANIFEST.MF", 100) == b"Manifest-Version: 1.0"
        assert backend.entry(HPI_PATH, "index.jelly", 100) is None
        assert backend.sha1(RepoEntry("d", "n.hpi", "a" * 64, "c" * 40, 1, "", "")) == "c" * 40
        with pytest.raises(BackendError, match="no sha1"):
            backend.sha1(RepoEntry("d", "n.hpi", "a" * 64, None, 1, "", ""))


def test_artifactory_facts_use_entries_and_a_ranged_central_directory():
    data = make_hpi(*GIT, date=(2023, 11, 18, 20, 14, 0))
    prefix = f"/artifactory/jenkins/{HPI_PATH}"
    routes = {("GET", prefix): serve_bytes(data)}
    for name in ("META-INF/MANIFEST.MF", "META-INF/maven/org.jenkins-ci.plugins/git/pom.xml"):
        routes[("GET", f"{prefix}!/{name}")] = (200, {}, zipfile.ZipFile(io.BytesIO(data)).read(name))
    routes[("GET", f"{prefix}!/WEB-INF/lib/git.jar")] = (200, {}, make_jar(b"<div>From the jar.</div>"))
    with StubServer(routes) as stub:
        facts = artifactory(stub).facts(entry(data), GIT, plugin=True)
    assert facts.coordinates == GIT
    assert facts.description == b"<div>From the jar.</div>"
    assert facts.released == dt.datetime(2023, 11, 18, 20, 14, tzinfo=UTC)
    ranged = [r for r in stub.requests if r[1] == prefix]
    assert all("Range" in headers for _, _, headers, _ in ranged)
    assert len(ranged) <= 4  # a probe and a block or two, never the whole archive


def test_artifactory_without_range_support_gives_no_date():
    data = make_hpi(*GIT)
    prefix = f"/artifactory/jenkins/{HPI_PATH}"
    with StubServer({("GET", prefix): serve_bytes(data, ranges=False)}) as stub:
        assert artifactory(stub).archive_released(entry(data)) is None


def test_redirects_to_another_host_drop_the_credentials():
    with StubServer({("GET", "/blob"): (200, {}, b"<project/>")}) as storage:
        other_host = storage.url.replace("127.0.0.1", "localhost") + "/blob"
        with StubServer({("GET", "/artifactory/jenkins/x/1/x-1.pom"): (302, {"Location": other_host}, b"")}) as stub:
            assert artifactory(stub).get("x/1/x-1.pom") == b"<project/>"
    assert stub.requests[0][2]["Authorization"] == BASIC
    assert "Authorization" not in storage.requests[0][2]


def keeper_item(path, sha, size=10, created="2026-09-22T23:09:48.946941Z"):
    return {"id": "x", "repository_key": "jenkins-prod-local", "path": path, "name": "org", "version": None,
            "size_bytes": size, "checksum_sha256": sha, "created_at": created}


def test_keeper_listing_follows_cursors_with_a_bearer_token(tmp_path):
    base = "/api/v1/repositories/jenkins-prod-local/artifacts?per_page=100"
    first = {"items": [keeper_item(HPI_PATH, "a" * 64), keeper_item("org/x/1/x-1.jar", "b" * 64),
                       keeper_item("org/x/1/x-1.txt", "c" * 64)],
             "pagination": {"page": 1, "per_page": 100, "total": 4, "total_pages": 2},
             "next_cursor": "abc", "has_more": True}
    second = {"items": [keeper_item("org/x/1/x-1.pom", "d" * 64)],
              "pagination": {"page": 2, "per_page": 100, "total": 4, "total_pages": 2}, "has_more": False}
    routes = {("GET", base + "&page=1"): (200, {}, json.dumps(first).encode()),
              ("GET", base + "&cursor=abc"): (200, {}, json.dumps(second).encode())}
    with StubServer(routes) as stub:
        listed = keeper(stub, tmp_path).list()
    assert [e.path for e in listed] == [HPI_PATH, "org/x/1/x-1.jar", "org/x/1/x-1.pom"]
    assert listed[0].created == listed[0].modified == "2026-09-22T23:09:48.946Z"
    assert listed[0].sha1 is None
    assert all(headers["Authorization"] == "Bearer tok" for _, _, headers, _ in stub.requests)


def test_keeper_listing_falls_back_to_page_numbers(tmp_path):
    base = "/api/v1/repositories/jenkins-prod-local/artifacts?per_page=100"
    pages = {1: [keeper_item(HPI_PATH, "a" * 64)], 2: [keeper_item("org/x/1/x-1.pom", "d" * 64)]}
    routes = {("GET", f"{base}&page={n}"): (200, {}, json.dumps({
        "items": items, "pagination": {"page": n, "per_page": 100, "total": 2, "total_pages": 2}}).encode())
        for n, items in pages.items()}
    with StubServer(routes) as stub:
        assert len(keeper(stub, tmp_path).list()) == 2


def test_keeper_files_use_basic_auth_and_sha1_side_files(tmp_path):
    routes = {("GET", "/maven/jenkins-prod-local/org/x/1/x-1.pom"): (200, {}, b"<project/>"),
              ("GET", f"/maven/jenkins-prod-local/{HPI_PATH}.sha1"): (200, {}, b"C" * 40 + b"  git-5.2.0.hpi\n"),
              ("GET", "/maven/jenkins-prod-local/org/x/1/x-1.hpi.sha1"): (200, {}, b"<html>404</html>")}
    with StubServer(routes) as stub:
        backend = keeper(stub, tmp_path)
        assert backend.get("org/x/1/x-1.pom") == b"<project/>"
        assert backend.sha1(entry(b"x")) == "c" * 40
        with pytest.raises(BackendError, match="no valid sha1"):
            backend.sha1(entry(b"x", "org/x/1/x-1.hpi"))
    assert stub.requests[0][2]["Authorization"] == "Basic " + base64.b64encode(b"zac:tok").decode()


def test_keeper_facts_download_the_archive_once(tmp_path):
    data = make_hpi(*GIT, layout="classes", date=(2019, 12, 19, 10, 8, 0))
    with StubServer({("GET", f"/maven/jenkins-prod-local/{HPI_PATH}"): (200, {}, data)}) as stub:
        facts = keeper(stub, tmp_path).facts(entry(data), GIT, plugin=True)
    assert facts.description == b"<div>An excellent plugin.</div>"
    assert facts.released == dt.datetime(2019, 12, 19, 10, 8, tzinfo=UTC)
    assert len(stub.requests) == 1
    assert list((tmp_path / "tmp").iterdir()) == []  # the archive itself is not kept


def test_keeper_refuses_archives_over_the_cap(tmp_path):
    data = make_hpi(*GIT)
    with StubServer({("GET", f"/maven/jenkins-prod-local/{HPI_PATH}"): (200, {}, data)}) as stub:
        with pytest.raises(BackendError, match="larger than 100 bytes"):
            keeper(stub, tmp_path, max_archive=100).facts(entry(data), GIT, plugin=True)
```

- [ ] **Step 2: Run them and watch them fail**

Run: `make test-unit ARGS="-k repo_adapter_backends"`
Expected: FAIL with `ModuleNotFoundError: No module named 'uc.repo_adapter.backends'`.

- [ ] **Step 3: Implement the backends**

Create `uc/uc/repo_adapter/backends.py`:

```python
"""Artifactory and Artifact Keeper, as the adapter needs them (repo-adapter spec §2, §4.1)."""
from __future__ import annotations

import base64
import datetime as dt
import io
import json
import re
import ssl
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO, Protocol
from urllib.parse import urlsplit

from .. import http as uc_http
from ..aql import AQL_TEMPLATE
from ..errors import UcError
from ..pom import Coordinates
from .archive import (
    SMALL_ENTRY_LIMIT,
    ArchiveError,
    ArchiveFacts,
    archive_date,
    extract_facts,
    jar_description,
    open_archive,
    zip_reader,
)

TIMEOUT = 60
LONG_TIMEOUT = 600
LISTING_LIMIT = 256 * 1024 * 1024
RANGE_BLOCK = 256 * 1024
LISTED_EXTENSIONS = (".hpi", ".jpi", ".war", ".pom")
_SHA1 = re.compile(r"^[0-9a-f]{40}$")


class BackendError(UcError):
    pass


@dataclass(frozen=True)
class RepoEntry:
    directory: str  # org/jenkins-ci/plugins/git/5.2.0
    name: str  # git-5.2.0.hpi
    sha256: str
    sha1: str | None  # None when the backend's listing has none (Artifact Keeper)
    size: int
    created: str  # Artifactory's time format, as the repository reports it
    modified: str

    @property
    def path(self) -> str:
        return f"{self.directory}/{self.name}"

    @property
    def extension(self) -> str:
        return self.name.rsplit(".", 1)[-1]


def artifactory_time(moment: dt.datetime) -> str:
    """The time format Artifactory's AQL uses, which upstream parses: UTC with milliseconds."""
    utc = moment.astimezone(dt.timezone.utc)
    return utc.strftime("%Y-%m-%dT%H:%M:%S.") + f"{utc.microsecond // 1000:03d}Z"


def parse_time(value: str) -> dt.datetime:
    return dt.datetime.fromisoformat(value.replace("Z", "+00:00"))


@dataclass
class Response:
    status: int
    headers: dict[str, str]
    body: bytes


class _NoCredentialsAcrossHosts(urllib.request.HTTPRedirectHandler):
    """Artifactory redirects downloads to object storage; repository credentials must not follow."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: D102 - urllib's signature
        new = super().redirect_request(req, fp, code, msg, headers, newurl)
        if new is not None and urlsplit(newurl).hostname != urlsplit(req.full_url).hostname:
            new.remove_header("Authorization")
        return new


class Http:
    def __init__(self, ca_file: Path | None) -> None:
        context = ssl.create_default_context(cafile=str(ca_file) if ca_file else None)
        self.opener = urllib.request.build_opener(
            urllib.request.HTTPSHandler(context=context), _NoCredentialsAcrossHosts()
        )

    def request(self, url: str, *, headers: dict[str, str], limit: int, data: bytes | None = None,
                method: str | None = None, timeout: float = TIMEOUT, truncate: bool = False) -> Response:
        """One request; the body is read up to limit bytes (BackendError beyond, unless truncate)."""
        request = urllib.request.Request(url, data=data, method=method,
                                         headers={"User-Agent": uc_http.USER_AGENT, **headers})
        try:
            with self.opener.open(request, timeout=timeout) as response:
                body = response.read(limit + 1)
                status, got = response.status, dict(response.headers.items())
        except urllib.error.HTTPError as exc:
            body, status, got = exc.read(4096), exc.code, dict(exc.headers.items())
        except (OSError, ValueError) as exc:
            raise BackendError(f"{method or 'GET'} {url} failed: {exc}") from exc
        if len(body) > limit and not truncate:
            raise BackendError(f"{url} is larger than {limit} bytes")
        return Response(status, {k.lower(): v for k, v in got.items()}, body[:limit] if truncate else body)

    def download(self, url: str, *, headers: dict[str, str], out: BinaryIO, limit: int) -> None:
        request = urllib.request.Request(url, headers={"User-Agent": uc_http.USER_AGENT, **headers})
        try:
            with self.opener.open(request, timeout=LONG_TIMEOUT) as response:
                total = 0
                while chunk := response.read(1024 * 1024):
                    total += len(chunk)
                    if total > limit:
                        raise BackendError(f"{url} is larger than {limit} bytes")
                    out.write(chunk)
        except urllib.error.HTTPError as exc:
            raise BackendError(f"GET {url} failed with HTTP {exc.code}") from exc
        except (OSError, ValueError) as exc:
            raise BackendError(f"GET {url} failed: {exc}") from exc


class RangeReader(io.RawIOBase):
    """A seekable, read-only view of a remote file, fetched in cached blocks with Range requests."""

    def __init__(self, http: Http, url: str, headers: dict[str, str], size: int) -> None:
        self.http, self.url, self.headers, self.size = http, url, headers, size
        self.pos = 0
        self.blocks: dict[int, bytes] = {}

    @staticmethod
    def supported(http: Http, url: str, headers: dict[str, str]) -> bool:
        response = http.request(url, headers={**headers, "Range": "bytes=0-0"}, limit=1, truncate=True)
        return response.status == 206

    def readable(self) -> bool:
        return True

    def seekable(self) -> bool:
        return True

    def tell(self) -> int:
        return self.pos

    def seek(self, offset: int, whence: int = io.SEEK_SET) -> int:
        base = {io.SEEK_SET: 0, io.SEEK_CUR: self.pos, io.SEEK_END: self.size}[whence]
        self.pos = max(0, base + offset)
        return self.pos

    def _block(self, index: int) -> bytes:
        if index not in self.blocks:
            start = index * RANGE_BLOCK
            end = min(start + RANGE_BLOCK, self.size) - 1
            response = self.http.request(self.url, headers={**self.headers, "Range": f"bytes={start}-{end}"},
                                         limit=RANGE_BLOCK)
            if response.status != 206:
                raise BackendError(f"{self.url} answered a Range request with HTTP {response.status}")
            self.blocks[index] = response.body
        return self.blocks[index]

    def read(self, n: int = -1) -> bytes:
        end = self.size if n is None or n < 0 else min(self.size, self.pos + n)
        out = bytearray()
        while self.pos < end:
            block = self._block(self.pos // RANGE_BLOCK)
            offset = self.pos % RANGE_BLOCK
            chunk = block[offset:offset + (end - self.pos)]
            if not chunk:
                break
            out += chunk
            self.pos += len(chunk)
        return bytes(out)

    def readinto(self, buffer) -> int:
        data = self.read(len(buffer))
        buffer[:len(data)] = data
        return len(data)


class Backend(Protocol):
    type: str

    def list(self) -> list[RepoEntry]: ...
    def get(self, path: str) -> bytes | None: ...
    def sha1(self, entry: RepoEntry) -> str: ...
    def facts(self, entry: RepoEntry, path_coordinates: Coordinates, *, plugin: bool) -> ArchiveFacts: ...
    def jar_description(self, jar_path: str) -> bytes | None: ...


def _basic(username: str, password: str) -> dict[str, str]:
    return {"Authorization": "Basic " + base64.b64encode(f"{username}:{password}".encode()).decode()}


def _quote(path: str) -> str:
    return urllib.parse.quote(path, safe="/!+-._~")


class ArtifactoryBackend:
    type = "artifactory"

    def __init__(self, http: Http, url: str, api_url: str, key: str, username: str, password: str,
                 max_archive_bytes: int) -> None:
        self.http, self.api_url, self.key = http, api_url, key
        self.base = f"{url}{key}/"
        self.auth = _basic(username, password)
        self.max_archive_bytes = max_archive_bytes

    def list(self) -> list[RepoEntry]:
        response = self.http.request(
            self.api_url + "search/aql", method="POST", data=(AQL_TEMPLATE % self.key).encode(),
            headers={**self.auth, "Content-Type": "text/plain", "Accept": "application/json"},
            limit=LISTING_LIMIT, timeout=LONG_TIMEOUT,
        )
        if response.status != 200:
            raise BackendError(f"AQL query to {self.api_url} failed with HTTP {response.status}")
        try:
            results = json.loads(response.body)["results"]
            return [RepoEntry(r["path"], r["name"], r["sha256"], r.get("actual_sha1"), int(r.get("size") or 0),
                              r.get("created") or r["modified"], r["modified"]) for r in results]
        except (ValueError, KeyError, TypeError) as exc:
            raise BackendError(f"unexpected AQL response from {self.api_url}: {exc}") from exc

    def get(self, path: str) -> bytes | None:
        response = self.http.request(self.base + _quote(path), headers=self.auth, limit=self.max_archive_bytes,
                                     timeout=LONG_TIMEOUT)
        if response.status == 404:
            return None
        if response.status != 200:
            raise BackendError(f"GET {self.base}{path} failed with HTTP {response.status}")
        return response.body

    def entry(self, path: str, name: str, limit: int) -> bytes | None:
        response = self.http.request(f"{self.base}{_quote(path)}!/{_quote(name)}", headers=self.auth, limit=limit)
        if response.status == 404:
            return None
        if response.status != 200:
            raise BackendError(f"GET {self.base}{path}!/{name} failed with HTTP {response.status}")
        return response.body

    def sha1(self, entry: RepoEntry) -> str:
        if not entry.sha1 or not _SHA1.match(entry.sha1):
            raise BackendError(f"no sha1 for {entry.path} in the AQL listing")
        return entry.sha1

    def archive_released(self, entry: RepoEntry) -> dt.datetime | None:
        """The archive date from its central directory via Range requests; None without Range support."""
        url = self.base + _quote(entry.path)
        if not RangeReader.supported(self.http, url, self.auth):
            return None
        return archive_date(open_archive(RangeReader(self.http, url, self.auth, entry.size)))

    def facts(self, entry: RepoEntry, path_coordinates: Coordinates, *, plugin: bool) -> ArchiveFacts:
        return extract_facts(lambda name, limit: self.entry(entry.path, name, limit), path_coordinates,
                             plugin=plugin, released=self.archive_released(entry))

    def jar_description(self, jar_path: str) -> bytes | None:
        return self.entry(jar_path, "index.jelly", SMALL_ENTRY_LIMIT)


class ArtifactKeeperBackend:
    type = "artifact-keeper"

    def __init__(self, http: Http, url: str, key: str, username: str, token: str, tmp_dir: Path,
                 max_archive_bytes: int) -> None:
        self.http, self.key, self.tmp_dir = http, key, tmp_dir
        self.api = f"{url}api/v1/repositories/{key}/artifacts"
        self.base = f"{url}maven/{key}/"
        self.bearer = {"Authorization": f"Bearer {token}"}
        self.basic = _basic(username, token)
        self.max_archive_bytes = max_archive_bytes

    def _page(self, cursor: str | None, page: int) -> dict:
        query = {"per_page": "100", **({"cursor": cursor} if cursor else {"page": str(page)})}
        response = self.http.request(f"{self.api}?{urllib.parse.urlencode(query)}",
                                     headers={**self.bearer, "Accept": "application/json"}, limit=LISTING_LIMIT)
        if response.status != 200:
            raise BackendError(f"listing {self.api} failed with HTTP {response.status}")
        try:
            return json.loads(response.body)
        except ValueError as exc:
            raise BackendError(f"unexpected listing response from {self.api}: {exc}") from exc

    def list(self) -> list[RepoEntry]:
        entries: list[RepoEntry] = []
        cursor, page = None, 1
        while True:
            body = self._page(cursor, page)
            try:
                for item in body["items"]:
                    directory, _, name = item["path"].rpartition("/")
                    if name.endswith((*LISTED_EXTENSIONS, ".jar")) and directory:
                        created = artifactory_time(parse_time(item["created_at"]))
                        entries.append(RepoEntry(directory, name, item["checksum_sha256"], None,
                                                 int(item["size_bytes"]), created, created))
            except (KeyError, TypeError, ValueError) as exc:
                raise BackendError(f"unexpected listing response from {self.api}: {exc}") from exc
            more = body.get("has_more")
            if more is None:
                pagination = body.get("pagination") or {}
                more = page < int(pagination.get("total_pages") or 0)
            if not more or not body["items"]:
                return entries
            cursor, page = body.get("next_cursor"), page + 1

    def get(self, path: str) -> bytes | None:
        response = self.http.request(self.base + _quote(path), headers=self.basic, limit=self.max_archive_bytes,
                                     timeout=LONG_TIMEOUT)
        if response.status == 404:
            return None
        if response.status != 200:
            raise BackendError(f"GET {self.base}{path} failed with HTTP {response.status}")
        return response.body

    def sha1(self, entry: RepoEntry) -> str:
        data = self.get(entry.path + ".sha1")
        value = (data or b"").decode("ascii", "replace").strip().split(" ")[0].lower()
        if not _SHA1.match(value):
            raise BackendError(f"no valid sha1 file for {entry.path}")
        return value

    def facts(self, entry: RepoEntry, path_coordinates: Coordinates, *, plugin: bool) -> ArchiveFacts:
        self.tmp_dir.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryFile(dir=self.tmp_dir) as out:
            self.http.download(self.base + _quote(entry.path), headers=self.basic, out=out,
                               limit=self.max_archive_bytes)
            archive = open_archive(out)
            return extract_facts(zip_reader(archive), path_coordinates, plugin=plugin,
                                 released=archive_date(archive))

    def jar_description(self, jar_path: str) -> bytes | None:
        data = self.get(jar_path)
        if data is None:
            return None
        try:
            return jar_description(data)
        except ArchiveError:
            return None
```

- [ ] **Step 4: Run the unit tests**

Run: `make test-unit`
Expected: PASS, `238 passed` (existing tests using `StubServer` still pass).

- [ ] **Step 5: Commit**

```bash
git add uc/uc/repo_adapter/backends.py tests/support/httpstub.py tests/unit/test_repo_adapter_backends.py
git commit -m "repo-adapter: Artifactory and Artifact Keeper backends"
```

### Task 8: The gap-filled listing

The heart of the adapter: spec §4.2 rules 1 to 9, turning a repository's files into the AQL rows upstream needs, with the report operators read.

**Files:**
- Create: `uc/uc/repo_adapter/listing.py`, `tests/support/fakebackend.py`, `tests/unit/test_repo_adapter_listing.py`

**Interfaces:**
- Consumes `Backend`, `Cache`, `archive.plausible`, `pom.unusable_reason`, `aql.to_gav`.
- Produces `Report` (the fields of spec §7 and `counts()`), `REPORT_LISTS`, `Index(rows, poms, manifests, descriptions, jars, report)` where `poms` maps a path to derived bytes or `None` (forward the repository's own), `build_index(backend, cache, *, key, now) -> Index`, `write_report(report, path)` (atomic, mode 0600) and `read_report(path) -> dict | None`.
- Produces test support `fakebackend.FakeBackend(files, *, type, uploaded)`, an in-memory `Backend` recording `calls`, with a `failing` set of paths.

- [ ] **Step 1: Write the fake backend and the tests**

Create `tests/support/fakebackend.py`:

```python
"""An in-memory repository with the Backend interface, for listing and server tests."""
from __future__ import annotations

import hashlib
import io

from uc.repo_adapter.archive import (
    ArchiveError,
    archive_date,
    extract_facts,
    jar_description,
    open_archive,
    zip_reader,
)
from uc.repo_adapter.backends import BackendError, RepoEntry

UPLOADED = "2026-09-22T23:09:48.946Z"


class FakeBackend:
    def __init__(self, files: dict[str, bytes], *, type: str = "artifact-keeper", uploaded: str = UPLOADED) -> None:
        self.type = type
        self.files = dict(files)
        self.uploaded = uploaded
        self.failing: set[str] = set()
        self.calls: list[tuple[str, str]] = []

    def list(self) -> list[RepoEntry]:
        self.calls.append(("list", ""))
        entries = []
        for path, data in sorted(self.files.items()):
            directory, _, name = path.rpartition("/")
            sha1 = hashlib.sha1(data).hexdigest() if self.type == "artifactory" else None
            entries.append(RepoEntry(directory, name, hashlib.sha256(data).hexdigest(), sha1, len(data),
                                     self.uploaded, self.uploaded))
        return entries

    def get(self, path: str) -> bytes | None:
        self.calls.append(("get", path))
        return self.files.get(path)

    def sha1(self, entry: RepoEntry) -> str:
        self.calls.append(("sha1", entry.path))
        return entry.sha1 or hashlib.sha1(self.files[entry.path]).hexdigest()

    def facts(self, entry: RepoEntry, path_coordinates, *, plugin: bool):
        self.calls.append(("facts", entry.path))
        if entry.path in self.failing:
            raise BackendError(f"GET {entry.path} failed: connection reset")
        archive = open_archive(io.BytesIO(self.files[entry.path]))
        return extract_facts(zip_reader(archive), path_coordinates, plugin=plugin, released=archive_date(archive))

    def jar_description(self, jar_path: str) -> bytes | None:
        self.calls.append(("jar", jar_path))
        data = self.files.get(jar_path)
        if data is None:
            return None
        try:
            return jar_description(data)
        except ArchiveError:
            return None
```

Create `tests/unit/test_repo_adapter_listing.py`:

```python
import datetime as dt
import hashlib
import json
import stat

from archivefixture import make_hpi, make_war, pom_xml
from fakebackend import FakeBackend

from uc.repo_adapter.cache import Cache
from uc.repo_adapter.listing import build_index, read_report, write_report

NOW = dt.datetime(2026, 9, 23, 0, 0, tzinfo=dt.timezone.utc)
AD = "org/jenkins-ci/plugins/active-directory/2.967.vf4e254546a_b_3/active-directory-2.967.vf4e254546a_b_3"
CAFFEINE = "io/jenkins/plugins/caffeine-api/3.2.4-208.v7e2da_a_7db_82b_/caffeine-api-3.2.4-208.v7e2da_a_7db_82b_"
AD_GAV = ("org.jenkins-ci.plugins", "active-directory", "2.967.vf4e254546a_b_3")
CAFFEINE_GAV = ("io.jenkins.plugins", "caffeine-api", "3.2.4-208.v7e2da_a_7db_82b_")
NGINX_404 = b"<html>\r\n<head><title>404 Not Found</title></head>\r\n<body><hr><center>nginx</center></body></html>\r\n"


def index_of(files, tmp_path, **backend_options):
    backend = FakeBackend(files, **backend_options)
    return backend, build_index(backend, Cache(tmp_path / "cache"), key="jenkins-prod-local", now=NOW)


def rows_by_name(index):
    return {row["name"]: row for row in index.rows}


def test_the_operators_test_repository(tmp_path):
    """Both plugins publish: one POM derived, one HTML "POM" replaced by the embedded one."""
    files = {f"{AD}.hpi": make_hpi(*AD_GAV), f"{AD}.pom": NGINX_404, f"{CAFFEINE}.hpi": make_hpi(*CAFFEINE_GAV)}
    _, index = index_of(files, tmp_path)
    rows = rows_by_name(index)
    assert set(rows) == {n.rsplit("/", 1)[1] + ext for n in (AD, CAFFEINE) for ext in (".hpi", ".pom")}
    assert index.poms[f"{AD}.pom"] == pom_xml(*AD_GAV)
    assert index.poms[f"{CAFFEINE}.pom"] == pom_xml(*CAFFEINE_GAV)
    report = index.report
    assert report.derived_poms == [":".join(CAFFEINE_GAV), ":".join(AD_GAV)]  # in path order
    assert report.replaced_poms == [{"path": f"{AD}.pom", "reason": "not XML"}]
    assert report.counts()["needs_pom"] == 0
    derived = rows[AD.rsplit("/", 1)[1] + ".pom"]
    assert derived["sha256"] == hashlib.sha256(pom_xml(*AD_GAV)).hexdigest()
    assert derived["actual_sha1"] == hashlib.sha1(pom_xml(*AD_GAV)).hexdigest()
    assert derived["size"] == len(pom_xml(*AD_GAV))


def test_a_usable_repository_pom_wins(tmp_path):
    own = pom_xml(*AD_GAV, name="The repository's own POM")
    _, index = index_of({f"{AD}.hpi": make_hpi(*AD_GAV), f"{AD}.pom": own}, tmp_path)
    assert index.poms[f"{AD}.pom"] is None  # forwarded to the repository, not derived
    assert index.report.derived_poms == [] and index.report.replaced_poms == []


def test_a_pom_for_another_artifact_is_replaced(tmp_path):
    files = {f"{AD}.hpi": make_hpi(*AD_GAV), f"{AD}.pom": pom_xml("org.jenkins-ci.plugins", "git", "5.2.0")}
    _, index = index_of(files, tmp_path)
    assert index.report.replaced_poms[0]["reason"].startswith("declares org.jenkins-ci.plugins:git:5.2.0")
    assert index.poms[f"{AD}.pom"] == pom_xml(*AD_GAV)


def test_a_gradle_build_needs_a_real_pom(tmp_path):
    base = "org/jenkins-ci/plugins/synopsys-polaris/1.4.0/synopsys-polaris-1.4.0"
    _, index = index_of({f"{base}.hpi": make_hpi("org.jenkins-ci.plugins", "synopsys-polaris", "1.4.0",
                                                  layout="gradle")}, tmp_path)
    assert [r["name"] for r in index.rows] == ["synopsys-polaris-1.4.0.hpi"]  # upstream drops it: no .pom
    assert index.report.needs_pom == [{"gav": "org.jenkins-ci.plugins:synopsys-polaris:1.4.0",
                                       "url": f"https://repo.jenkins-ci.org/releases/{base}.pom"}]
    assert index.descriptions[("org.jenkins-ci.plugins", "synopsys-polaris", "1.4.0")]  # rule 6 still works


def test_a_misplaced_plugin_is_dropped_with_its_correct_path(tmp_path):
    wrong = "org/jenkins-ci/plugins/caffeine-api/3.2.4-208.v7e2da_a_7db_82b_/caffeine-api-3.2.4-208.v7e2da_a_7db_82b_"
    files = {f"{wrong}.hpi": make_hpi(*CAFFEINE_GAV), f"{wrong}.pom": pom_xml(*CAFFEINE_GAV)}
    _, index = index_of(files, tmp_path)
    assert index.rows == []  # neither the archive nor the POM beside it
    assert index.report.misplaced == [{"path": f"{wrong}.hpi", "correct_path": f"{CAFFEINE}.hpi"}]


def test_a_manifest_without_coordinates_is_never_misplaced(tmp_path):
    base = "org/example/old/1.0/old-1.0"
    data = make_hpi("org.example", "old", "1.0", manifest_group=None, manifest_artifact=None)
    _, index = index_of({f"{base}.hpi": data}, tmp_path)
    assert index.report.misplaced == []
    assert index.report.derived_poms == ["org.example:old:1.0"]


def test_archive_dates_replace_upload_times(tmp_path):
    _, index = index_of({f"{AD}.hpi": make_hpi(*AD_GAV, date=(2025, 11, 2, 9, 15, 0))}, tmp_path)
    rows = rows_by_name(index)
    for name in ("active-directory-2.967.vf4e254546a_b_3.hpi", "active-directory-2.967.vf4e254546a_b_3.pom"):
        assert rows[name]["created"] == rows[name]["modified"] == "2025-11-02T09:15:00.000Z"
    assert index.report.dates_from_repository == []


def test_a_placeholder_date_falls_back_to_the_upload_time(tmp_path):
    _, index = index_of({f"{AD}.hpi": make_hpi(*AD_GAV, date=(1980, 2, 1, 0, 0, 0))}, tmp_path)
    assert rows_by_name(index)["active-directory-2.967.vf4e254546a_b_3.hpi"]["modified"] == "2026-09-22T23:09:48.946Z"
    assert index.report.dates_from_repository == [
        {"path": f"{AD}.hpi", "reason": "archive date 1980-02-01 is before 2004"}]


def test_cores_get_derived_poms_and_dates(tmp_path):
    war = "org/jenkins-ci/main/jenkins-war/2.583/jenkins-war-2.583"
    _, index = index_of({f"{war}.war": make_war("2.583", date=(2026, 9, 22, 4, 32, 0))}, tmp_path)
    assert index.poms[f"{war}.pom"] == pom_xml("org.jenkins-ci.main", "jenkins-war", "2.583", "Jenkins war")
    assert rows_by_name(index)["jenkins-war-2.583.war"]["modified"] == "2026-09-22T04:32:00.000Z"
    assert f"{war}.war" in index.manifests


def test_an_unreadable_archive_is_skipped_and_the_rest_proceed(tmp_path):
    files = {f"{AD}.hpi": make_hpi(*AD_GAV), f"{CAFFEINE}.hpi": make_hpi(*CAFFEINE_GAV)}
    backend = FakeBackend(files)
    backend.failing.add(f"{AD}.hpi")
    index = build_index(backend, Cache(tmp_path / "cache"), key="k", now=NOW)
    assert index.report.skipped == [{"path": f"{AD}.hpi", "reason": f"GET {AD}.hpi failed: connection reset"}]
    assert {r["name"] for r in index.rows} == {"caffeine-api-3.2.4-208.v7e2da_a_7db_82b_.hpi",
                                              "caffeine-api-3.2.4-208.v7e2da_a_7db_82b_.pom"}


def test_not_an_archive_is_skipped(tmp_path):
    _, index = index_of({f"{AD}.hpi": NGINX_404}, tmp_path)
    assert index.report.skipped[0]["reason"] == "not a zip archive (no end of central directory record)"


def test_files_outside_maven_layout_pass_through_for_upstream_to_reject(tmp_path):
    _, index = index_of({"plugins/git/git.hpi": b"not even a zip"}, tmp_path)
    assert [r["name"] for r in index.rows] == ["git.hpi"]


def test_parent_poms_pass_through_and_bogus_ones_are_reported(tmp_path):
    parent = "org/jenkins-ci/plugins/plugin/4.88/plugin-4.88.pom"
    bogus = "org/example/x/1.0/x-1.0.pom"
    files = {parent: pom_xml("org.jenkins-ci.plugins", "plugin", "4.88"), bogus: NGINX_404}
    _, index = index_of(files, tmp_path)
    assert [r["name"] for r in index.rows] == ["plugin-4.88.pom"]
    assert index.poms[parent] is None
    assert index.report.replaced_poms == [{"path": bogus, "reason": "not XML"}]


def test_the_cache_spares_a_second_download(tmp_path):
    files = {f"{AD}.hpi": make_hpi(*AD_GAV), f"{AD}.pom": NGINX_404}
    backend = FakeBackend(files)
    cache = Cache(tmp_path / "cache")
    first = build_index(backend, cache, key="k", now=NOW)
    backend.calls.clear()
    second = build_index(backend, cache, key="k", now=NOW)
    assert second.rows == first.rows
    assert [call for call in backend.calls if call[0] in ("facts", "get", "sha1")] == []


def test_artifact_keeper_jars_are_indexed_but_not_listed(tmp_path):
    jar = f"{AD}.jar"
    _, index = index_of({f"{AD}.hpi": make_hpi(*AD_GAV), jar: b"jar bytes"}, tmp_path)
    assert jar in index.jars
    assert all(not r["name"].endswith(".jar") for r in index.rows)


def test_report_is_written_privately(tmp_path):
    _, index = index_of({f"{CAFFEINE}.hpi": make_hpi(*CAFFEINE_GAV)}, tmp_path)
    path = tmp_path / "state" / "report.json"
    write_report(index.report, path)
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    report = read_report(path)
    assert report["repo"] == {"type": "artifact-keeper", "key": "jenkins-prod-local"}
    assert report["archives"] == 1 and report["derived_poms"] == [":".join(CAFFEINE_GAV)]
    assert json.loads(path.read_text())["generated_at"] == "2026-09-23T00:00:00Z"
    assert read_report(tmp_path / "missing.json") is None
```

- [ ] **Step 2: Run them and watch them fail**

Run: `make test-unit ARGS="-k repo_adapter_listing"`
Expected: FAIL with `ModuleNotFoundError: No module named 'uc.repo_adapter.listing'`.

- [ ] **Step 3: Implement the listing**

Create `uc/uc/repo_adapter/listing.py`:

```python
"""The gap-filled listing the adapter serves, and its report (repo-adapter spec §4.2, §7)."""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import tempfile
from dataclasses import asdict, dataclass, field
from pathlib import Path

from ..aql import Gav, RepoFile, to_gav
from ..pom import Coordinates, unusable_reason
from ..timeutil import iso
from .archive import ArchiveError, ArchiveFacts, plausible
from .backends import Backend, BackendError, RepoEntry, artifactory_time, parse_time
from .cache import Cache

PUBLIC_RELEASES = "https://repo.jenkins-ci.org/releases/"
ARCHIVE_EXTENSIONS = ("hpi", "jpi", "war")
REPORT_LISTS = ("derived_poms", "replaced_poms", "needs_pom", "misplaced", "skipped", "dates_from_repository")


@dataclass
class Report:
    generated_at: str
    repo: dict
    archives: int = 0
    derived_poms: list[str] = field(default_factory=list)
    replaced_poms: list[dict] = field(default_factory=list)
    needs_pom: list[dict] = field(default_factory=list)
    misplaced: list[dict] = field(default_factory=list)
    skipped: list[dict] = field(default_factory=list)
    dates_from_repository: list[dict] = field(default_factory=list)

    def counts(self) -> dict[str, int]:
        return {name: len(getattr(self, name)) for name in REPORT_LISTS}


@dataclass
class Index:
    """One listing: the AQL rows, and what GETs resolve against until the next listing."""

    rows: list[dict] = field(default_factory=list)
    poms: dict[str, bytes | None] = field(default_factory=dict)  # path -> derived bytes, None: the repository's
    manifests: dict[str, bytes] = field(default_factory=dict)  # archive path -> MANIFEST.MF
    descriptions: dict[Coordinates, bytes] = field(default_factory=dict)  # plugin -> index.jelly from its .hpi
    jars: dict[str, RepoEntry] = field(default_factory=dict)  # the repository's .jar files (Artifact Keeper)
    report: Report | None = None


def _row(key: str, directory: str, name: str, *, sha256: str, sha1: str, size: int, created: str,
         modified: str) -> dict:
    return {"repo": key, "path": directory, "name": name, "created": created, "modified": modified,
            "sha256": sha256, "actual_sha1": sha1, "size": size}


def _facts(backend: Backend, cache: Cache, entry: RepoEntry, coordinates: Coordinates, plugin: bool) -> ArchiveFacts:
    facts = cache.facts(entry.sha256)
    if facts is None:
        facts = backend.facts(entry, coordinates, plugin=plugin)
        cache.store_facts(entry.sha256, facts)
    return facts


def _sha1(backend: Backend, cache: Cache, entry: RepoEntry) -> str:
    if entry.sha1:
        return entry.sha1
    cached = cache.sha1(entry.sha256)
    if cached is None:
        cached = backend.sha1(entry)
        cache.store_sha1(entry.sha256, cached)
    return cached


def _pom_verdict(backend: Backend, cache: Cache, entry: RepoEntry, coordinates: Coordinates) -> str | None:
    """None for a usable repository POM, otherwise the reason it is not (rule 3)."""
    cached = cache.pom_verdict(entry.sha256)
    if cached is not None:
        return cached or None
    data = backend.get(entry.path)
    reason = "missing from the repository" if data is None else unusable_reason(data, coordinates)
    cache.store_pom_verdict(entry.sha256, reason)
    return reason


def build_index(backend: Backend, cache: Cache, *, key: str, now: dt.datetime) -> Index:
    entries = backend.list()
    report = Report(generated_at=iso(now), repo={"type": backend.type, "key": key})
    index = Index(report=report)
    poms = {e.path: e for e in entries if e.extension == "pom"}
    index.jars = {e.path: e for e in entries if e.extension == "jar"}
    consumed: set[str] = set()

    for entry in sorted((e for e in entries if e.extension in ARCHIVE_EXTENSIONS), key=lambda e: e.path):
        report.archives += 1
        gav = to_gav(RepoFile(entry.directory, entry.name))
        if gav is None:  # not in Maven layout: upstream rejects it, and uc doctor reports it
            index.rows.append(_row(key, entry.directory, entry.name, sha256=entry.sha256, sha1=entry.sha1 or "",
                                   size=entry.size, created=entry.created, modified=entry.modified))
            continue
        pom_path = gav.with_extension("pom").maven_path
        consumed.add(pom_path)
        coordinates = (gav.group_id, gav.artifact_id, gav.version)
        plugin = entry.extension != "war"
        try:
            facts = _facts(backend, cache, entry, coordinates, plugin)
            sha1 = _sha1(backend, cache, entry)
        except (BackendError, ArchiveError) as exc:
            report.skipped.append({"path": entry.path, "reason": str(exc)})
            continue
        if plugin and facts.coordinates and facts.coordinates != coordinates:
            correct = Gav(*facts.coordinates, entry.extension).maven_path
            report.misplaced.append({"path": entry.path, "correct_path": correct})
            continue

        reason = plausible(facts.released, parse_time(entry.modified))
        if reason is None and facts.released is not None:
            created = modified = artifactory_time(facts.released)
        else:
            created, modified = entry.created, entry.modified
            report.dates_from_repository.append({"path": entry.path, "reason": reason})
        index.rows.append(_row(key, entry.directory, entry.name, sha256=entry.sha256, sha1=sha1, size=entry.size,
                               created=created, modified=modified))
        if facts.manifest is not None:
            index.manifests[entry.path] = facts.manifest
        if plugin and facts.description is not None:
            index.descriptions[coordinates] = facts.description

        real = poms.get(pom_path)
        if real is not None:
            try:
                verdict = _pom_verdict(backend, cache, real, coordinates)
                real_sha1 = _sha1(backend, cache, real) if verdict is None else ""
            except BackendError as exc:
                verdict = f"unreadable: {exc}"
            if verdict is None:
                index.poms[pom_path] = None
                index.rows.append(_row(key, real.directory, real.name, sha256=real.sha256, sha1=real_sha1,
                                       size=real.size, created=created, modified=modified))
                continue
            report.replaced_poms.append({"path": pom_path, "reason": verdict})
        if facts.pom is not None and unusable_reason(facts.pom, coordinates) is None:
            index.poms[pom_path] = facts.pom
            directory, _, name = pom_path.rpartition("/")
            index.rows.append(_row(key, directory, name, sha256=hashlib.sha256(facts.pom).hexdigest(),
                                   sha1=hashlib.sha1(facts.pom).hexdigest(), size=len(facts.pom),
                                   created=created, modified=modified))
            report.derived_poms.append(":".join(coordinates))
        else:
            report.needs_pom.append({"gav": ":".join(coordinates), "url": PUBLIC_RELEASES + pom_path})

    for path, entry in sorted(poms.items()):  # POMs without an archive: parent POMs and the like
        if path in consumed:
            continue
        gav = to_gav(RepoFile(entry.directory, entry.name))
        if gav is None:
            continue
        try:
            verdict = _pom_verdict(backend, cache, entry, (gav.group_id, gav.artifact_id, gav.version))
            sha1 = _sha1(backend, cache, entry) if verdict is None else ""
        except BackendError as exc:
            verdict, sha1 = f"unreadable: {exc}", ""
        if verdict is None:
            index.poms[path] = None
            index.rows.append(_row(key, entry.directory, entry.name, sha256=entry.sha256, sha1=sha1,
                                   size=entry.size, created=entry.created, modified=entry.modified))
        else:
            report.replaced_poms.append({"path": path, "reason": verdict})
    cache.prune()
    return index


def write_report(report: Report, path: Path) -> None:
    """Atomically, mode 0600: the paths and URLs in it are for operators, not the public (spec §7)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".report-")
    with os.fdopen(fd, "w", encoding="utf-8") as out:
        json.dump(asdict(report), out, indent=2, sort_keys=True)
        out.write("\n")
    os.replace(tmp, path)


def read_report(path: Path) -> dict | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError):
        return None
    return value if isinstance(value, dict) else None
```

- [ ] **Step 4: Run the unit tests**

Run: `make test-unit`
Expected: PASS, `254 passed`.

- [ ] **Step 5: Commit**

```bash
git add uc/uc/repo_adapter/listing.py tests/support/fakebackend.py tests/unit/test_repo_adapter_listing.py
git commit -m "repo-adapter: gap-filled listing and report (spec rules 1-9)"
```

### Task 9: The HTTP service and `uc repo-adapter`

The service upstream and uc talk to. Only the AQL listing needs the token, because upstream cannot answer a challenge on file reads (spec §2). GETs serve POMs, manifests and descriptions only. It is idle when `REPO_ADAPTER` is off, so the online stack can run it without repository credentials.

**Files:**
- Create: `uc/uc/repo_adapter/server.py`, `tests/unit/test_repo_adapter_server.py`
- Modify: `uc/uc/cli.py`, `tests/unit/test_cli.py`

**Interfaces:**
- Consumes `build_index`, `write_report`, the backends, `Cache`, `preflight.load_repository_credentials`, `ADAPTER_USERNAME`.
- Produces `ensure_token(path) -> str`, `Adapter(backend, cache, *, key, report_file, clock)` with `refresh()`, `current()`, `pom(path)`, `manifest(archive_path)`, `description(jar_path)`; `make_handler(adapter | None, *, key, token)`; `create_adapter(settings)`; `make_server(settings, port=8081, host="0.0.0.0")`; `serve(settings, port)`; CLI `uc repo-adapter [--port 8081]`.

- [ ] **Step 1: Write the tests**

Create `tests/unit/test_repo_adapter_server.py`:

```python
import base64
import datetime as dt
import stat
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

import pytest
from archivefixture import make_hpi, make_jar, pom_xml
from fakebackend import FakeBackend
from ucsupport import make_settings

from uc.aql import AQL_TEMPLATE, Gav, classify, query_repository
from uc.doctor import ArtifactSource
from uc.repo_adapter.cache import Cache
from uc.repo_adapter.listing import read_report
from uc.repo_adapter.server import Adapter, ensure_token, make_handler, make_server

NOW = dt.datetime(2026, 9, 23, tzinfo=dt.timezone.utc)
KEY = "jenkins-prod-local"
TOKEN = "t0ken"
GAV = ("io.jenkins.plugins", "caffeine-api", "3.2.4")
BASE = "io/jenkins/plugins/caffeine-api/3.2.4/caffeine-api-3.2.4"


@pytest.fixture
def running(tmp_path):
    started = []

    def start(files, *, type="artifact-keeper", adapter_on=True):
        backend = FakeBackend(files, type=type)
        adapter = Adapter(backend, Cache(tmp_path / "cache"), key=KEY, report_file=tmp_path / "report.json",
                          clock=lambda: NOW) if adapter_on else None
        server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(adapter, key=KEY, token=TOKEN))
        threading.Thread(target=server.serve_forever, daemon=True).start()
        started.append(server)
        return backend, f"http://127.0.0.1:{server.server_address[1]}/artifactory/"

    yield start
    for server in started:
        server.shutdown()
        server.server_close()


def fetch(url, *, token=TOKEN, data=None):
    headers = {"Authorization": "Basic " + base64.b64encode(f"uc:{token}".encode()).decode()} if token else {}
    request = urllib.request.Request(url, data=data, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return response.status, dict(response.headers), response.read()
    except urllib.error.HTTPError as exc:
        return exc.code, dict(exc.headers), exc.read()


def aql(url, key=KEY):
    return fetch(url + "api/search/aql", data=(AQL_TEMPLATE % key).encode())


def test_health_needs_no_credentials(running):
    _, url = running({})
    assert fetch(url.replace("/artifactory/", "/healthz"), token=None)[0] == 200


def test_the_listing_needs_the_token(running):
    _, url = running({})
    status, headers, _ = fetch(url + "api/search/aql", token=None, data=(AQL_TEMPLATE % KEY).encode())
    assert status == 401
    assert headers["WWW-Authenticate"] == 'Basic realm="repo-adapter"'
    assert fetch(url + "api/search/aql", token="wrong", data=b"x")[0] == 401


def test_file_reads_need_no_credentials(running):
    """Upstream sends none, and cannot answer a challenge (its Authenticator NPEs on Java's HttpClient)."""
    _, url = running({f"{BASE}.hpi": make_hpi(*GAV)})
    aql(url)
    assert fetch(f"{url}{KEY}/{BASE}.pom", token=None)[2] == pom_xml(*GAV)
    assert b"Group-Id" in fetch(f"{url}{KEY}/{BASE}.hpi!/META-INF/MANIFEST.MF", token=None)[2]


def test_only_upstreams_query_for_the_configured_repository_is_answered(running):
    _, url = running({})
    assert fetch(url + "api/search/aql", data=b'items.find({"repo":"other"})')[0] == 400
    assert aql(url, key="other")[0] == 400


def test_upstream_and_uc_read_the_gap_filled_listing(running, tmp_path):
    _, url = running({f"{BASE}.hpi": make_hpi(*GAV)})
    listing = classify(query_repository(url + "api/", KEY, "uc", TOKEN))  # uc's own AQL client
    assert [g.artifact_id for g in listing.plugins] == ["caffeine-api"]  # paired with its derived POM
    assert read_report(tmp_path / "report.json")["derived_poms"] == [":".join(GAV)]


def test_derived_poms_manifests_and_descriptions_are_served(running):
    _, url = running({f"{BASE}.hpi": make_hpi(*GAV)})
    aql(url)
    source = ArtifactSource(url, KEY, "uc", TOKEN, None, allow_network=True)  # what uc doctor reads with
    gav = Gav(*GAV, "hpi")
    assert source.pom(gav) == pom_xml(*GAV)
    assert "Group-Id: io.jenkins.plugins" in source.manifest(gav)
    status, _, body = fetch(f"{url}{KEY}/{BASE}.jar!/index.jelly")
    assert (status, body) == (200, b"<div>An excellent plugin.</div>")


def test_a_repository_jar_wins_for_the_description(running):
    _, url = running({f"{BASE}.hpi": make_hpi(*GAV), f"{BASE}.jar": make_jar(b"<div>From the jar.</div>")})
    aql(url)
    assert fetch(f"{url}{KEY}/{BASE}.jar!/index.jelly")[2] == b"<div>From the jar.</div>"


def test_artifactory_descriptions_try_the_repository_jar_first(running):
    backend, url = running({f"{BASE}.hpi": make_hpi(*GAV)}, type="artifactory")
    aql(url)
    assert fetch(f"{url}{KEY}/{BASE}.jar!/index.jelly")[2] == b"<div>An excellent plugin.</div>"
    assert ("jar", f"{BASE}.jar") in backend.calls


def test_repository_poms_are_forwarded_and_archives_are_not_served(running):
    own = pom_xml(*GAV, name="Own")
    backend, url = running({f"{BASE}.hpi": make_hpi(*GAV), f"{BASE}.pom": own})
    aql(url)
    assert fetch(f"{url}{KEY}/{BASE}.pom")[2] == own
    assert fetch(f"{url}{KEY}/{BASE}.hpi")[0] == 404  # upstream never downloads whole archives
    assert fetch(f"{url}{KEY}/{BASE}.hpi!/WEB-INF/lib/caffeine-api.jar")[0] == 404  # not an entry upstream reads
    assert fetch(f"{url}{KEY}/org/jenkins-ci/plugins/plugin/4.88/plugin-4.88.pom")[0] == 404


def test_a_get_before_any_listing_builds_one(running):
    backend, url = running({f"{BASE}.hpi": make_hpi(*GAV)})
    assert fetch(f"{url}{KEY}/{BASE}.pom")[2] == pom_xml(*GAV)  # as after an adapter restart
    assert backend.calls[0] == ("list", "")


def test_an_idle_adapter_only_answers_health(running):
    _, url = running({}, adapter_on=False)
    assert fetch(url.replace("/artifactory/", "/healthz"), token=None)[0] == 200
    assert aql(url)[0] == 503


def test_ensure_token_creates_a_private_token_once(tmp_path):
    path = tmp_path / "state" / "token"
    token = ensure_token(path)
    assert len(token) == 64 and stat.S_IMODE(path.stat().st_mode) == 0o600
    assert ensure_token(path) == token


def test_make_server_needs_repository_credentials_only_when_on(tmp_path, secrets_dir):
    idle = make_server(make_settings(tmp_path, secrets_dir, MODE="online", REPO_URL="", REPO_KEY=""), port=0,
                       host="127.0.0.1")
    idle.server_close()
    settings = make_settings(tmp_path, secrets_dir, REPO_ADAPTER="on")
    server = make_server(settings, port=0, host="127.0.0.1")
    server.server_close()
    assert settings.repo_adapter_token_file.is_file()
```

```diff
diff --git a/tests/unit/test_cli.py b/tests/unit/test_cli.py
index 20340db..29593d4 100644
--- a/tests/unit/test_cli.py
+++ b/tests/unit/test_cli.py
@@ -62,6 +62,14 @@ def test_rollback_reports_a_held_lock(monkeypatch, tmp_path, secrets_dir):
     assert os.readlink(data / "live") == "releases/20260922T020000Z"
 
 
+def test_repo_adapter_refuses_to_start_without_repository_credentials(monkeypatch, tmp_path, secrets_dir, capsys):
+    use_env(monkeypatch, tmp_path, secrets_dir)
+    monkeypatch.setenv("REPO_ADAPTER", "on")
+    (secrets_dir / "repo_password").write_text("")
+    assert main(["repo-adapter", "--port", "0"]) == 1
+    assert "uc: error: Repository credentials missing" in capsys.readouterr().err
+
+
 def test_pki_init_and_show(tmp_path, capsys):
     out = tmp_path / "pki"
     assert main(["pki", "init", "--out", str(out), "--root-days", "30", "--signing-days", "10"]) == 0
```

- [ ] **Step 2: Run them and watch them fail**

Run: `make test-unit ARGS="-k 'repo_adapter_server or repo_adapter_refuses'"`
Expected: FAIL: `ModuleNotFoundError: No module named 'uc.repo_adapter.server'`, and the CLI test exits 2 with `invalid choice: 'repo-adapter'`.

- [ ] **Step 3: Implement the server and the CLI command**

Create `uc/uc/repo_adapter/server.py`:

```python
"""uc repo-adapter: upstream's three Artifactory requests, answered from either repository type
(repo-adapter spec §3, §4.1, §5.3)."""
from __future__ import annotations

import base64
import datetime as dt
import hmac
import json
import os
import secrets
import sys
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Callable

from ..aql import AQL_TEMPLATE, RepoFile, to_gav
from ..config import Settings, read_secret
from ..preflight import ADAPTER_USERNAME, load_repository_credentials
from ..timeutil import utcnow
from .archive import MANIFEST, ArchiveError
from .backends import ArtifactKeeperBackend, ArtifactoryBackend, Backend, BackendError, Http
from .cache import Cache
from .listing import Index, build_index, write_report

PREFIX = "/artifactory/"
QUERY_LIMIT = 64 * 1024
PORT = 8081


def ensure_token(path: Path) -> str:
    """The generator's credential for the adapter: 32 random bytes, created once, mode 0600."""
    existing = read_secret(path)
    if existing:
        return existing
    path.parent.mkdir(parents=True, exist_ok=True)
    token = secrets.token_hex(32)
    tmp = path.with_name(f".{path.name}.{os.getpid()}")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as out:
        out.write(token + "\n")
    os.replace(tmp, path)
    return token


class Adapter:
    def __init__(self, backend: Backend, cache: Cache, *, key: str, report_file: Path,
                 clock: Callable[[], dt.datetime] = utcnow) -> None:
        self.backend, self.cache, self.key, self.report_file, self.clock = backend, cache, key, report_file, clock
        self.index: Index | None = None
        self.lock = threading.Lock()

    def refresh(self) -> Index:
        with self.lock:
            index = build_index(self.backend, self.cache, key=self.key, now=self.clock())
            assert index.report is not None
            write_report(index.report, self.report_file)
            self.index = index
            return index

    def current(self) -> Index:
        """The latest listing; after a restart, the first GET builds one (spec §4.1)."""
        return self.index or self.refresh()

    def pom(self, path: str) -> bytes | None:
        index = self.current()
        if path not in index.poms:
            return None
        derived = index.poms[path]
        return derived if derived is not None else self.backend.get(path)

    def manifest(self, archive_path: str) -> bytes | None:
        return self.current().manifests.get(archive_path)

    def description(self, jar_path: str) -> bytes | None:
        """<artifactId>-<version>.jar!/index.jelly: from the repository's .jar, else from the .hpi (rule 6)."""
        index = self.current()
        directory, _, name = jar_path.rpartition("/")
        gav = to_gav(RepoFile(directory, name))
        if gav is None or gav.extension != "jar":
            return None
        jar = index.jars.get(jar_path)
        if self.backend.type == "artifactory":
            found = self.backend.jar_description(jar_path)
            if found is not None:
                return found
        elif jar is not None:
            if not self.cache.has_description(jar.sha256):
                self.cache.store_description(jar.sha256, self.backend.jar_description(jar_path))
            found = self.cache.description(jar.sha256)
            if found is not None:
                return found
        return index.descriptions.get((gav.group_id, gav.artifact_id, gav.version))


def make_handler(adapter: Adapter | None, *, key: str, token: str | None):
    expected = ("Basic " + base64.b64encode(f"{ADAPTER_USERNAME}:{token}".encode()).decode()) if token else ""
    repo_prefix = f"{PREFIX}{key}/"

    class Handler(BaseHTTPRequestHandler):
        server_version = "uc-repo-adapter"

        def log_message(self, format: str, *args) -> None:  # noqa: A002 - http.server's signature
            sys.stderr.write(f"repo-adapter: {self.address_string()} {format % args}\n")

        def _send(self, status: int, body: bytes = b"", content_type: str = "application/octet-stream") -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(body)

        def _refuse(self, *, authenticate: bool) -> bool:
            """True when the request was answered already: idle adapter, or no valid credentials.

            Only the listing is authenticated. Upstream cannot answer an authentication challenge for
            file reads: its Authenticator dereferences the requesting site, which Java's HttpClient
            always passes as null. GETs serve metadata only, on the internal repo network (spec §5.3).
            """
            if adapter is None:
                self._send(503, b"REPO_ADAPTER is off\n", "text/plain")
                return True
            if authenticate and not hmac.compare_digest(self.headers.get("Authorization", ""), expected):
                self.send_response(401)
                self.send_header("WWW-Authenticate", 'Basic realm="repo-adapter"')
                self.send_header("Content-Length", "0")
                self.end_headers()
                return True
            return False

        def do_GET(self) -> None:
            path = self.path.split("?", 1)[0]
            if path == "/healthz":
                return self._send(200, b"ok\n", "text/plain")
            if self._refuse(authenticate=False):
                return None
            assert adapter is not None
            if not path.startswith(repo_prefix):
                return self._send(404)
            archive, separator, entry = urllib.parse.unquote(path[len(repo_prefix):]).partition("!/")
            try:
                if not separator:
                    data = adapter.pom(archive) if archive.endswith(".pom") else None
                elif entry == MANIFEST and archive.endswith((".hpi", ".jpi", ".war")):
                    data = adapter.manifest(archive)
                elif entry == "index.jelly" and archive.endswith(".jar"):
                    data = adapter.description(archive)
                else:
                    self.log_message("refused archive entry %s!/%s", archive, entry)
                    data = None
            except (BackendError, ArchiveError) as exc:
                self.log_message("GET %s failed: %s", path, exc)
                return self._send(502, str(exc).encode(), "text/plain")
            return self._send(200, data) if data is not None else self._send(404)

        do_HEAD = do_GET

        def do_POST(self) -> None:
            if self.path.split("?", 1)[0] != PREFIX + "api/search/aql":
                return self._send(404)
            if self._refuse(authenticate=True):
                return None
            assert adapter is not None
            length = int(self.headers.get("Content-Length") or 0)
            if length > QUERY_LIMIT:
                return self._send(413)
            query = self.rfile.read(length).decode("utf-8", "replace")
            if query != AQL_TEMPLATE % key:
                return self._send(400, b"unsupported query\n", "text/plain")
            try:
                rows = adapter.refresh().rows
            except (BackendError, ArchiveError) as exc:
                self.log_message("listing failed: %s", exc)
                return self._send(502, str(exc).encode(), "text/plain")
            body = {"results": rows, "range": {"start_pos": 0, "end_pos": len(rows), "total": len(rows)}}
            return self._send(200, json.dumps(body).encode(), "application/json")

    return Handler


def create_adapter(settings: Settings) -> Adapter:
    username, password = load_repository_credentials(settings)
    http = Http(settings.repo_ca_file)
    max_bytes = settings.repo_max_archive_mb * 1024 * 1024
    backend: Backend
    if settings.repo_type == "artifact-keeper":
        backend = ArtifactKeeperBackend(http, settings.repo_url, settings.repo_key, username, password,
                                        settings.repo_adapter_dir / "tmp", max_bytes)
    else:
        backend = ArtifactoryBackend(http, settings.repo_url, settings.repo_api_url, settings.repo_key,
                                     username, password, max_bytes)
    return Adapter(backend, Cache(settings.repo_adapter_dir / "cache"), key=settings.repo_key,
                   report_file=settings.repo_adapter_report_file)


def make_server(settings: Settings, port: int = PORT, host: str = "0.0.0.0") -> ThreadingHTTPServer:
    if not settings.repo_adapter:
        return ThreadingHTTPServer((host, port), make_handler(None, key=settings.repo_key, token=None))
    adapter = create_adapter(settings)
    token = ensure_token(settings.repo_adapter_token_file)
    return ThreadingHTTPServer((host, port), make_handler(adapter, key=settings.repo_key, token=token))


def serve(settings: Settings, port: int = PORT) -> None:
    server = make_server(settings, port)
    state = f"{settings.repo_type} repository {settings.repo_key} at {settings.repo_url}" if settings.repo_adapter \
        else "idle (REPO_ADAPTER is off)"
    print(f"repo-adapter: listening on :{port}, {state}", flush=True)
    server.serve_forever()
```

```diff
diff --git a/uc/uc/cli.py b/uc/uc/cli.py
index 6260713..5ad78e6 100644
--- a/uc/uc/cli.py
+++ b/uc/uc/cli.py
@@ -1,4 +1,4 @@
-"""Command-line entry point: uc generate | doctor | rollback | status | schedule | pki (spec §5)."""
+"""Command-line entry point: uc generate | doctor | rollback | status | schedule | repo-adapter | pki (spec §5)."""
 from __future__ import annotations
 
 import argparse
@@ -32,6 +32,9 @@ def build_parser() -> argparse.ArgumentParser:
     sub.add_parser("status", help="print status.json")
     sub.add_parser("schedule", help="run generate on GENERATE_SCHEDULE (the scheduler service)")
 
+    adapter = sub.add_parser("repo-adapter", help="serve the repository to the generator (the repo-adapter service)")
+    adapter.add_argument("--port", type=int, default=8081)
+
     pki = sub.add_parser("pki", help="manage the signing PKI").add_subparsers(dest="pki_command", required=True)
     init = pki.add_parser("init", help="create a root CA and a signing certificate")
     init.add_argument("--out", type=Path, required=True)
@@ -95,6 +98,11 @@ def _dispatch(args: argparse.Namespace) -> int:
 
         run_schedule(settings)
         return 0
+    if args.command == "repo-adapter":
+        from .repo_adapter.server import serve
+
+        serve(settings, port=args.port)
+        return 0
     raise AssertionError(f"unhandled command {args.command}")
 
 
```

- [ ] **Step 4: Run the unit tests**

Run: `make test-unit`
Expected: PASS, `268 passed`.

- [ ] **Step 5: Commit**

```bash
git add uc/uc/repo_adapter/server.py uc/uc/cli.py tests/unit/test_repo_adapter_server.py tests/unit/test_cli.py
git commit -m "repo-adapter: the HTTP service and uc repo-adapter"
```

### Task 10: The adapter's report in `uc doctor` and `status.json`

`uc doctor` shows what the adapter filled in and what it dropped, with paths and URLs. `status.json`, which is public, gets counts only. Misplaced and skipped archives count as dropped, so the existing warning and exit code cover them.

**Files:**
- Modify: `uc/uc/doctor.py`, `uc/uc/generate.py`, `tests/unit/test_doctor.py`, `tests/unit/test_generate.py`

**Interfaces:**
- Consumes `listing.read_report`, `settings.repo_adapter_report_file`.
- Produces `doctor.ADAPTER_SECTIONS`, `DoctorReport.repo_adapter: dict | None`, `attach_adapter_report(report, settings) -> DoctorReport`; `summary()["repo_adapter"]` holds counts, or `None` when the adapter is off.

- [ ] **Step 1: Write the tests**

```diff
diff --git a/tests/unit/test_doctor.py b/tests/unit/test_doctor.py
index 4a8b10b..770f31c 100644
--- a/tests/unit/test_doctor.py
+++ b/tests/unit/test_doctor.py
@@ -1,5 +1,6 @@
 import base64
 import hashlib
+import json
 import os
 from pathlib import Path
 
@@ -10,6 +11,7 @@ from uc.aql import Gav, RepoFile, classify
 from uc.doctor import (
     ArtifactSource,
     analyze,
+    attach_adapter_report,
     format_report,
     is_stub_pom,
     parse_dependencies,
@@ -140,6 +142,41 @@ def test_run_doctor_includes_live_tiers(tmp_path, secrets_dir):
     assert report.exit_code() == 0
 
 
+ADAPTER_REPORT = {
+    "derived_poms": ["io.jenkins.plugins:caffeine-api:3.2.4"],
+    "replaced_poms": [{"path": "org/x/ad/1/ad-1.pom", "reason": "not XML"}],
+    "needs_pom": [{"gav": "org.jenkins-ci.plugins:polaris:1.4.0",
+                   "url": "https://repo.jenkins-ci.org/releases/org/jenkins-ci/plugins/polaris/1.4.0/polaris-1.4.0.pom"}],
+    "misplaced": [{"path": "org/jenkins-ci/plugins/caffeine-api/1/caffeine-api-1.hpi",
+                   "correct_path": "io/jenkins/plugins/caffeine-api/1/caffeine-api-1.hpi"}],
+    "skipped": [{"path": "org/x/big/1/big-1.hpi", "reason": "larger than 512 MiB"}],
+    "dates_from_repository": [{"path": "org/x/cas/1/cas-1.hpi", "reason": "archive date 1980-02-01 is before 2004"}],
+}
+
+
+def test_the_adapter_report_adds_sections_and_counts_its_drops(tmp_path, secrets_dir):
+    settings = make_settings(tmp_path, secrets_dir, REPO_ADAPTER="on")
+    settings.repo_adapter_dir.mkdir(parents=True)
+    settings.repo_adapter_report_file.write_text(json.dumps({"archives": 5, **ADAPTER_REPORT}))
+    listing = classify(plugin("polaris", "1.4.0", "hpi") + plugin("git", "5.2.0", "hpi", "pom"))
+    report = attach_adapter_report(analyze(listing, versions(), FakeSource({}, {})), settings)
+    assert report.dropped == 3  # polaris (no POM at all), the misplaced and the skipped archive
+    assert report.summary()["repo_adapter"] == {name: 1 for name in ADAPTER_REPORT}
+    text = format_report(report, settings)
+    assert "(through repo-adapter)" in text
+    assert "procure the real .pom (1):" in text and ADAPTER_REPORT["needs_pom"][0]["url"] in text
+    assert "-> " + "io/jenkins/plugins/caffeine-api/1/caffeine-api-1.hpi" in text.replace("  ->  ", " -> ")
+    assert "POMs filled in from archives (1):" in text
+    assert "org/x/ad/1/ad-1.pom: not XML" in text
+    assert "archive date 1980-02-01 is before 2004" in text
+
+
+def test_without_the_adapter_there_is_no_adapter_report(tmp_path, secrets_dir):
+    report = attach_adapter_report(analyze(classify([]), versions(), FakeSource({}, {})),
+                                   make_settings(tmp_path, secrets_dir))
+    assert report.repo_adapter is None and report.summary()["repo_adapter"] is None
+
+
 def test_format_report(tmp_path, secrets_dir):
     settings = make_settings(tmp_path, secrets_dir)
     listing = classify(plugin("orphan", "1.0", "hpi") + plugin("git", "5.2.0", "hpi", "pom"))
diff --git a/tests/unit/test_generate.py b/tests/unit/test_generate.py
index 93d96ce..98ccd44 100644
--- a/tests/unit/test_generate.py
+++ b/tests/unit/test_generate.py
@@ -1,4 +1,5 @@
 import datetime as dt
+import json
 import os
 
 import pytest
@@ -181,6 +182,30 @@ def test_generate_reclaims_orphaned_staging_and_work(tmp_path, secrets_dir):
     assert live_release(data) == rid
 
 
+def test_generation_through_the_adapter(tmp_path, secrets_dir):
+    settings = make_settings(tmp_path, secrets_dir, REPO_ADAPTER="on")
+    settings.repo_adapter_dir.mkdir(parents=True)
+    settings.repo_adapter_token_file.write_text("adapter-token\n")
+    settings.repo_adapter_report_file.write_text(json.dumps({
+        "derived_poms": ["org.jenkins-ci.plugins:git:5.2.0"],
+        "misplaced": [{"path": "org/jenkins-ci/plugins/caffeine-api/1/caffeine-api-1.hpi",
+                       "correct_path": "io/jenkins/plugins/caffeine-api/1/caffeine-api-1.hpi"}],
+    }))
+    queries = []
+    tools = tools_for(settings)
+    tools.aql_query = lambda *args: queries.append(args) or repo_files()
+    generate(settings, tools=tools, now=T1, base_env={})
+    env = tools.run_jar.envs[-1]
+    assert env["ARTIFACTORY_URL"] == "http://repo-adapter:8081/artifactory/"
+    assert (env["ARTIFACTORY_USERNAME"], env["ARTIFACTORY_PASSWORD"]) == ("uc", "adapter-token")
+    assert queries[0] == ("http://repo-adapter:8081/artifactory/api/", "jenkins", "uc", "adapter-token")
+    status = read_status(settings.data_dir)
+    assert status["doctor"]["repo_adapter"]["derived_poms"] == 1
+    assert status["doctor"]["repo_adapter"]["misplaced"] == 1
+    assert any("dropped by the generator" in w for w in status["warnings"])
+    assert "caffeine-api" not in (settings.data_dir / "status.json").read_text()  # counts only: it is public
+
+
 def test_status_error_omits_raw_exception_text(tmp_path, secrets_dir):
     settings = make_settings(tmp_path, secrets_dir)
     secret = "AQL query to https://artifactory.corp.internal/artifactory/api/ failed with HTTP 401: topsecret-body"
```

- [ ] **Step 2: Run them and watch them fail**

Run: `make test-unit ARGS="-k 'doctor or generate'"`
Expected: FAIL: `test_doctor.py` cannot import `attach_adapter_report`, and `test_generation_through_the_adapter` fails with `KeyError: 'repo_adapter'`.

- [ ] **Step 3: Implement**

```diff
diff --git a/uc/uc/doctor.py b/uc/uc/doctor.py
index 1ae1c44..83ddb55 100644
--- a/uc/uc/doctor.py
+++ b/uc/uc/doctor.py
@@ -35,26 +35,49 @@ class DoctorReport:
     missing_dependencies: list[str] = field(default_factory=list)
     unchecked: int = 0  # newest releases whose POM or manifest was unavailable
     tiers: dict | None = None
+    # From the repo-adapter's report.json (repo-adapter spec §7); None when the adapter is off.
+    repo_adapter: dict | None = None
+
+    def _adapter(self, name: str) -> list:
+        return (self.repo_adapter or {}).get(name) or []
 
     @property
     def dropped(self) -> int:
-        return len(self.hpi_without_pom) + len(self.war_without_pom) + len(self.non_maven)
+        # needs_pom archives already count as hpi_without_pom: no POM reaches the listing for them.
+        return (len(self.hpi_without_pom) + len(self.war_without_pom) + len(self.non_maven)
+                + len(self._adapter("misplaced")) + len(self._adapter("skipped")))
 
     def exit_code(self) -> int:
         return 1 if self.dropped or self.missing_dependencies else 0
 
     def summary(self) -> dict:
+        """For the public status.json: counts only, never paths or URLs."""
         return {
             "plugins": self.plugins, "cores": self.cores,
             "hpi_without_pom": len(self.hpi_without_pom), "war_without_pom": len(self.war_without_pom),
             "non_maven": len(self.non_maven), "stub_poms": len(self.stub_poms),
             "missing_dependencies": len(self.missing_dependencies), "unchecked": self.unchecked,
+            "repo_adapter": ({name: len(self._adapter(name)) for name in ADAPTER_SECTIONS}
+                             if self.repo_adapter is not None else None),
         }
 
     def to_dict(self) -> dict:
         return asdict(self)
 
 
+ADAPTER_SECTIONS = ("derived_poms", "replaced_poms", "needs_pom", "misplaced", "skipped", "dates_from_repository")
+
+
+def attach_adapter_report(report: DoctorReport, settings: Settings) -> DoctorReport:
+    """Add the repo-adapter's report of what it filled in and what it dropped, when the adapter is on."""
+    if settings.repo_adapter:
+        from .repo_adapter.listing import read_report
+
+        data = read_report(settings.repo_adapter_report_file) or {}
+        report.repo_adapter = {name: data.get(name) or [] for name in ADAPTER_SECTIONS}
+    return report
+
+
 class Source(Protocol):
     def pom(self, gav: Gav) -> bytes | None: ...
     def manifest(self, gav: Gav) -> str | None: ...
@@ -196,6 +219,7 @@ def run_doctor(
     files = query(settings.source_api_url, settings.repo_key, creds.repo_username, creds.repo_password)
     report = analyze(aql.classify(files), versions,
                      ArtifactSource.from_settings(settings, creds, allow_network=fetch))
+    attach_adapter_report(report, settings)
     tiers_file = settings.data_dir / "live" / "tiers.json"
     if tiers_file.is_file():
         try:
@@ -225,13 +249,26 @@ def format_report(report: DoctorReport, settings: Settings) -> str:
             lines.extend(f"{indent}  {item}" for item in items)
 
     lines.append("")
+    adapter = report.repo_adapter
     if report.dropped:
         lines.append(f"Dropped by the generator: {report.dropped}")
-        section(".hpi/.jpi without a .pom", report.hpi_without_pom, "  ")
+        if adapter is not None:
+            section("no POM in the repository or the archive: procure the real .pom",
+                    [f"{item['gav']}  {item['url']}" for item in adapter["needs_pom"]], "  ")
+            section("misplaced: move to the path its manifest names",
+                    [f"{item['path']}  ->  {item['correct_path']}" for item in adapter["misplaced"]], "  ")
+            section("skipped", [f"{item['path']}: {item['reason']}" for item in adapter["skipped"]], "  ")
+        else:
+            section(".hpi/.jpi without a .pom", report.hpi_without_pom, "  ")
         section("jenkins-war without a .pom", report.war_without_pom, "  ")
         section("not in Maven layout", report.non_maven, "  ")
     else:
         lines.append("Nothing in the source is dropped by the generator.")
+    if adapter is not None:
+        section("POMs filled in from archives", adapter["derived_poms"])
+        section(".pom files ignored (not usable)", [f"{item['path']}: {item['reason']}" for item in adapter["replaced_poms"]])
+        section("dates from the repository (no usable archive date)",
+                [f"{item['path']}: {item['reason']}" for item in adapter["dates_from_repository"]])
     section("Stub POMs (degraded names, descriptions, SCM links)", report.stub_poms)
     section("Dependency problems", report.missing_dependencies)
     if report.unchecked:
diff --git a/uc/uc/generate.py b/uc/uc/generate.py
index 855b851..5d5ff01 100644
--- a/uc/uc/generate.py
+++ b/uc/uc/generate.py
@@ -131,6 +131,7 @@ def _build_and_publish(
     if listing is not None:
         versions = tools.versions or VersionTool.from_settings(settings)
         report = doctor.analyze(listing, versions, doctor.ArtifactSource.from_settings(settings, creds, allow_network=False))
+        doctor.attach_adapter_report(report, settings)
         doctor_summary = report.summary()
         if report.dropped:
             warnings.append(
```

- [ ] **Step 4: Run the unit tests**

Run: `make test-unit`
Expected: PASS, `271 passed`.

- [ ] **Step 5: Commit**

```bash
git add uc/uc/doctor.py uc/uc/generate.py tests/unit/test_doctor.py tests/unit/test_generate.py
git commit -m "uc doctor and status.json report what the repo-adapter filled in and dropped"
```

### Task 11: Deploying the adapter

The `repo-adapter` service joins the default network, to reach the repository, and a new `internal: true` network, `repo`, shared only with `uc` and the scheduler; `web` is not attached. `uc` and the scheduler wait for its healthcheck. In air-gapped mode, where the adapter is on by default, `!override` keeps the repository credentials out of `uc` and the scheduler. (Compose rejects a tag on a YAML alias, so each list is written out.)

**Files:**
- Modify: `compose.yaml`, `compose.airgapped.yaml` (both replaced in full)

**Interfaces:**
- Consumes `uc repo-adapter` (Task 9) and its `/healthz`.

- [ ] **Step 1: Replace `compose.yaml`**

Replace the whole file `compose.yaml`:

```yaml
# Jenkins update center (spec §4; repo-adapter spec §3).
#   online:     docker compose up -d
#   air-gapped: docker compose -f compose.yaml -f compose.airgapped.yaml up -d
#   one-shot:   docker compose run --rm uc doctor | generate | rollback | status
name: jenkins-update-center

x-uc: &uc
  image: ${UC_IMAGE:-jenkins-uc/uc:local}
  env_file: ${UC_ENV_FILE:-.env}
  read_only: true
  tmpfs:
    - /tmp
  volumes:
    - uc-data:/data
    - ./config/overlay:/config/overlay:ro
  networks: [default, repo]
  depends_on:
    repo-adapter: {condition: service_healthy}
  secrets:
    - update-center.key
    - update-center.cert
    - root-ca.crt
    - repo_username
    - repo_password
    - github_username
    - github_token

services:
  web:
    image: ${WEB_IMAGE:-jenkins-uc/web:local}
    env_file: ${UC_ENV_FILE:-.env}
    read_only: true
    tmpfs:
      - /tmp
    ports:
      - "${HTTP_PORT:-8080}:8080"
      - "${HTTPS_PORT:-8443}:8443"
    volumes:
      - uc-data:/data:ro
    secrets:
      - download_proxy_auth
    restart: unless-stopped

  # Answers the generator's Artifactory requests from Artifactory or Artifact Keeper and fills in POMs,
  # descriptions and release dates from the archives. Idle when REPO_ADAPTER is off (the online default).
  repo-adapter:
    image: ${UC_IMAGE:-jenkins-uc/uc:local}
    env_file: ${UC_ENV_FILE:-.env}
    command: ["repo-adapter"]
    read_only: true
    tmpfs:
      - /tmp
    volumes:
      - uc-data:/data
    networks: [default, repo]
    secrets:
      - repo_username
      - repo_password
    healthcheck:
      test: ["CMD", "python3", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8081/healthz', timeout=3)"]
      interval: 10s
      timeout: 5s
      retries: 6
      start_period: 5s
    restart: unless-stopped

  scheduler:
    <<: *uc
    command: ["schedule"]
    restart: unless-stopped

  uc:
    <<: *uc
    profiles: ["tools"]

networks:
  default: {}
  # uc, scheduler and the adapter only: the internet-facing web container cannot reach the adapter.
  repo:
    internal: true

volumes:
  uc-data:

secrets:
  update-center.key:
    file: ./secrets/update-center.key
  update-center.cert:
    file: ./secrets/update-center.cert
  root-ca.crt:
    file: ./secrets/root-ca.crt
  repo_username:
    file: ./secrets/repo_username
  repo_password:
    file: ./secrets/repo_password
  github_username:
    file: ./secrets/github_username
  github_token:
    file: ./secrets/github_token
  download_proxy_auth:
    file: ./secrets/download_proxy_auth
```

- [ ] **Step 2: Replace `compose.airgapped.yaml`**

Replace the whole file `compose.airgapped.yaml`:

```yaml
# Air-gapped override (spec §7.3): these outside hosts resolve to 127.0.0.1, so their clients fail
# at once instead of waiting for TCP timeouts. Every other name still uses the normal resolver.
x-blocked-hosts: &blocked-hosts
  - "api.github.com:127.0.0.1"
  - "github.com:127.0.0.1"
  - "codeload.github.com:127.0.0.1"
  - "raw.githubusercontent.com:127.0.0.1"
  - "reports.jenkins.io:127.0.0.1"
  - "updates.jenkins.io:127.0.0.1"

# The repo-adapter is on by default in air-gapped mode, so only it gets the repository credentials
# (repo-adapter spec §5.3): the !override lists leave them out of uc and scheduler. With
# REPO_ADAPTER=off, add repo_username and repo_password back to both lists.
services:
  scheduler:
    extra_hosts: *blocked-hosts
    secrets: !override [update-center.key, update-center.cert, root-ca.crt, github_username, github_token]
  uc:
    extra_hosts: *blocked-hosts
    secrets: !override [update-center.key, update-center.cert, root-ca.crt, github_username, github_token]
  repo-adapter:
    extra_hosts: *blocked-hosts
```

- [ ] **Step 3: Validate, and check which service gets what**

Run:
```bash
make compose-check
for mode in online airgapped; do files="-f compose.yaml"; [ $mode = airgapped ] && files="$files -f compose.airgapped.yaml"; echo "== $mode"; UC_ENV_FILE=.env.$mode.example docker compose --env-file .env.$mode.example $files config --format json | python3 -c "
import json, sys
config = json.load(sys.stdin)
for name in ('web', 'repo-adapter', 'scheduler'):
    svc = config['services'][name]
    print(f\"  {name:13} networks={sorted(svc.get('networks', {}))} secrets={[s['source'] for s in svc.get('secrets', [])]}\")
"; done
```
Expected: `compose-check` exits 0. In both modes, `web` has networks `['default']` and secrets `['download_proxy_auth']`, and `repo-adapter` has `['default', 'repo']` and only `repo_username`, `repo_password`. The scheduler has `['default', 'repo']`, with the repository credentials online and without them air-gapped.

- [ ] **Step 4: Commit**

```bash
git add compose.yaml compose.airgapped.yaml
git commit -m "Compose: the repo-adapter service on an internal network, credentials for it alone air-gapped"
```

### Task 12: The air-gapped suite against both repository types

A fake Artifact Keeper that behaves like the real 1.10 (bearer-only API, cursor paging capped at 100, basic or bearer on `/maven`, checksum side-files, `Range` ignored), and `Range` support in the fake Artifactory, as real Artifactory has. The seed now stages what operators actually procure:

- most plugins without a POM
- one real POM, which the adapter keeps
- the nginx 404 page saved as a POM
- a misplaced plugin
- the POM-less plugin
- two LTS cores released more than 400 days ago, with plugins that require them

`make it-airgapped` runs the suite once per type against the same seeded repository, with a fresh update center each time.

**Files:**
- Create: `tests/integration/fake_artifact_keeper.py`, `tests/unit/test_fake_artifact_keeper.py`, `tests/integration/types/artifactory.env`, `tests/integration/types/artifact-keeper.env`
- Modify: `tests/integration/fake_artifactory.py`, `tests/unit/test_fake_artifactory.py`, `tests/integration/seed.py`, `tests/integration/test_airgapped.py`, `Makefile`
- Replace: `tests/integration/airgapped.yaml`

**Interfaces:**
- `fake_artifact_keeper.py --root DIR --port N --user U --token T [--anonymous-read]`, with `make_handler(root, user, token, anonymous_read)` for tests.
- `seed.py repo` gains `--top-plugin`; `seeded.json` gains `paths`, `real_pom`, `html_pom` and `old_cores`, and `cores` includes the old cores.
- Each `types/<type>.env` supplies `IT_REPO_TYPE`, `IT_REPO_URL`, `IT_ADAPTER_BACKEND_URL`, `IT_PROXY_BACKEND_URL` and `IT_DOWNLOAD_PREFIX`; `make it-airgapped IT_TYPES=artifactory` runs one type.

- [ ] **Step 1: Write the fake Artifact Keeper's unit tests and the fake Artifactory's Range test**

Create `tests/unit/test_fake_artifact_keeper.py`:

```python
"""The test-only fake Artifact Keeper, as the adapter's Artifact Keeper backend sees it."""
import datetime as dt
import sys
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest
from archivefixture import make_hpi, pom_xml

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "integration"))

from fake_artifact_keeper import make_handler  # noqa: E402

from uc.repo_adapter.backends import ArtifactKeeperBackend, Http  # noqa: E402
from uc.repo_adapter.cache import Cache  # noqa: E402
from uc.repo_adapter.listing import build_index  # noqa: E402

GAV = ("io.jenkins.plugins", "caffeine-api", "3.2.4")
BASE = "io/jenkins/plugins/caffeine-api/3.2.4/caffeine-api-3.2.4"


@pytest.fixture
def keeper(tmp_path):
    repo = tmp_path / "jenkins-prod-local"
    for number in range(150):  # more than one page of 100
        path = repo / "org" / "example" / f"p{number:03d}" / "1.0" / f"p{number:03d}-1.0.pom"
        path.parent.mkdir(parents=True)
        path.write_bytes(pom_xml("org.example", f"p{number:03d}", "1.0"))
    (repo / BASE).parent.mkdir(parents=True)
    (repo / f"{BASE}.hpi").write_bytes(make_hpi(*GAV))
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(tmp_path, "zac", "tok", anonymous_read=False))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}/"
    httpd.shutdown()
    httpd.server_close()


def backend(url, tmp_path):
    return ArtifactKeeperBackend(Http(None), url, "jenkins-prod-local", "zac", "tok", tmp_path / "tmp", 1 << 20)


def test_the_backend_pages_through_the_fake(keeper, tmp_path):
    entries = backend(keeper, tmp_path).list()
    assert len(entries) == 151
    assert entries[0].path == f"{BASE}.hpi"


def test_the_fake_serves_side_files_and_refuses_anonymous_reads(keeper, tmp_path):
    entries = {e.path: e for e in backend(keeper, tmp_path).list()}
    assert len(backend(keeper, tmp_path).sha1(entries[f"{BASE}.hpi"])) == 40
    with pytest.raises(urllib.error.HTTPError) as err:
        urllib.request.urlopen(f"{keeper}maven/jenkins-prod-local/{BASE}.hpi")
    assert err.value.code == 401
    with pytest.raises(urllib.error.HTTPError) as err:
        urllib.request.urlopen(f"{keeper}api/v1/repositories/jenkins-prod-local/artifacts")
    assert err.value.code == 401


def test_the_listing_fills_in_the_pom_from_the_fake(keeper, tmp_path):
    index = build_index(backend(keeper, tmp_path), Cache(tmp_path / "cache"), key="jenkins-prod-local",
                        now=dt.datetime(2026, 9, 23, tzinfo=dt.timezone.utc))
    assert index.poms[f"{BASE}.pom"] == pom_xml(*GAV)
    assert index.report.derived_poms == [":".join(GAV)]
    assert len(index.rows) == 152  # the archive, its derived POM and 150 standalone POMs
```

```diff
diff --git a/tests/unit/test_fake_artifactory.py b/tests/unit/test_fake_artifactory.py
index 656f8a0..6ab93d5 100644
--- a/tests/unit/test_fake_artifactory.py
+++ b/tests/unit/test_fake_artifactory.py
@@ -70,6 +70,16 @@ def test_archive_entries_and_files(server):
     assert source.pom(gav) == b"<project><name>Git</name></project>"
 
 
+def test_file_reads_honour_range(server):
+    _, start = server
+    url = start(anonymous_read=True) + "jenkins/org/jenkins-ci/plugins/git/5.2.0/git-5.2.0.pom"
+    request = urllib.request.Request(url, headers={"Range": "bytes=1-7"})
+    with urllib.request.urlopen(request) as response:
+        assert response.status == 206
+        assert response.headers["Content-Range"] == "bytes 1-7/35"
+        assert response.read() == b"project"
+
+
 def test_private_reads_challenge_for_basic_auth(server):
     _, start = server
     url = start(anonymous_read=False) + "jenkins/org/jenkins-ci/plugins/git/5.2.0/git-5.2.0.pom"
```

- [ ] **Step 2: Run them and watch them fail**

Run: `make test-unit ARGS="-k fake_artifact"`
Expected: FAIL: `ModuleNotFoundError: No module named 'fake_artifact_keeper'`, and `test_file_reads_honour_range` gets status 200 instead of 206.

- [ ] **Step 3: Implement the fakes**

Create `tests/integration/fake_artifact_keeper.py`:

```python
"""Test-only stand-in for the Artifact Keeper 1.10 calls the repo-adapter makes (repo-adapter spec §2):
GET /api/v1/repositories/<key>/artifacts (Bearer only, at most 100 per page, keyset cursor) and
GET /maven/<key>/<path> (basic or Bearer; .sha1/.sha256/.md5 side files). Like the real one it ignores Range."""
from __future__ import annotations

import argparse
import base64
import datetime as dt
import hashlib
import json
import os
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HASHES = {".sha1": hashlib.sha1, ".sha256": hashlib.sha256, ".md5": hashlib.md5}
_SHA256: dict[tuple[str, int, int], str] = {}  # (path, size, mtime_ns) -> sha256: WARs are large


def sha256_of(path: Path) -> str:
    stat = path.stat()
    key = (str(path), stat.st_size, stat.st_mtime_ns)
    if key not in _SHA256:
        _SHA256[key] = hashlib.sha256(path.read_bytes()).hexdigest()
    return _SHA256[key]


def list_items(root: Path, key: str) -> list[dict]:
    base = root / key
    items = []
    for directory, _, names in os.walk(base):
        for name in names:
            path = Path(directory) / name
            created = dt.datetime.fromtimestamp(path.stat().st_mtime, dt.timezone.utc)
            items.append({
                "id": hashlib.md5(str(path).encode()).hexdigest(), "repository_key": key,
                "path": path.relative_to(base).as_posix(), "name": "org", "version": None,
                "size_bytes": path.stat().st_size, "checksum_sha256": sha256_of(path),
                "content_type": "application/octet-stream", "download_count": 0,
                "created_at": created.strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
            })
    return sorted(items, key=lambda item: item["path"])


def page(items: list[dict], query: dict[str, list[str]]) -> dict:
    per_page = max(1, min(100, int(query.get("per_page", ["20"])[0])))
    cursor = query.get("cursor", [None])[0]
    if cursor:
        after = base64.urlsafe_b64decode(cursor.encode()).decode()
        remaining = [item for item in items if item["path"] > after]
        number = 0
    else:
        number = max(1, int(query.get("page", ["1"])[0]))
        remaining = items[(number - 1) * per_page:]
    chunk, more = remaining[:per_page], len(remaining) > per_page
    body = {"items": chunk, "pagination": {"page": number or 1, "per_page": per_page, "total": len(items),
                                           "total_pages": -(-len(items) // per_page)}, "has_more": more}
    if more:
        body["next_cursor"] = base64.urlsafe_b64encode(chunk[-1]["path"].encode()).decode()
    return body


def make_handler(root: Path, user: str, token: str, anonymous_read: bool):
    bearer = f"Bearer {token}"
    basic = "Basic " + base64.b64encode(f"{user}:{token}".encode()).decode()

    class Handler(BaseHTTPRequestHandler):
        def _send(self, status: int, body: bytes = b"", content_type: str = "application/octet-stream") -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:
            path, _, raw_query = self.path.partition("?")
            auth = self.headers.get("Authorization", "")
            if path == "/health":
                return self._send(200, b'{"status":"healthy","version":"1.10.0"}', "application/json")
            parts = path.strip("/").split("/")
            if len(parts) == 5 and parts[:3] == ["api", "v1", "repositories"] and parts[4] == "artifacts":
                if auth != bearer:
                    return self._send(401, b"Authentication required", "text/plain")
                body = page(list_items(root, parts[3]), urllib.parse.parse_qs(raw_query))
                return self._send(200, json.dumps(body).encode(), "application/json")
            if len(parts) >= 3 and parts[0] == "maven":
                if auth not in (bearer, basic) and not anonymous_read:
                    return self._send(401, b"Authentication required", "text/plain")
                relative = urllib.parse.unquote("/".join(parts[2:]))
                target = (root / parts[1] / relative).resolve()
                if not target.is_relative_to(root.resolve()):
                    return self._send(404)
                if target.is_file():
                    return self._send(200, target.read_bytes())
                suffix = target.suffix
                original = target.with_suffix("")
                if suffix in HASHES and original.is_file():
                    return self._send(200, HASHES[suffix](original.read_bytes()).hexdigest().encode(), "text/plain")
                return self._send(404)
            return self._send(404, b"Repository not found", "text/plain")

    return Handler


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True, help="directory containing one folder per repository")
    parser.add_argument("--port", type=int, default=8081)
    parser.add_argument("--user", required=True)
    parser.add_argument("--token", required=True)
    parser.add_argument("--anonymous-read", action="store_true", help="allow /maven reads without credentials")
    args = parser.parse_args()
    handler = make_handler(args.root, args.user, args.token, args.anonymous_read)
    ThreadingHTTPServer(("0.0.0.0", args.port), handler).serve_forever()


if __name__ == "__main__":
    main()
```

```diff
diff --git a/tests/integration/fake_artifactory.py b/tests/integration/fake_artifactory.py
index aec4577..3c6f8ac 100644
--- a/tests/integration/fake_artifactory.py
+++ b/tests/integration/fake_artifactory.py
@@ -1,6 +1,7 @@
 """Test-only stand-in for the three Artifactory calls update-center2 makes (spec §13):
 POST /artifactory/api/search/aql, GET /artifactory/<repo>/<path>, GET /artifactory/<repo>/<path>!/<entry>.
-update-center2 answers 401 challenges with credentials, so private reads use WWW-Authenticate: Basic."""
+update-center2 answers 401 challenges with credentials, so private reads use WWW-Authenticate: Basic.
+File reads honour a single Range, as Artifactory does (the repo-adapter reads archive dates that way)."""
 from __future__ import annotations
 
 import argparse
@@ -18,6 +19,7 @@ from pathlib import Path
 PREFIX = "/artifactory/"
 EXTENSIONS = (".hpi", ".jpi", ".war", ".pom")
 _REPO = re.compile(r'"repo":\{"\$eq":"([A-Za-z0-9._-]+)"\}')
+_RANGE = re.compile(r"^bytes=(\d*)-(\d*)$")
 
 
 def aql_results(root: Path, repository: str) -> list[dict]:
@@ -56,10 +58,13 @@ def make_handler(root: Path, credentials: str, anonymous_read: bool):
     expected = "Basic " + base64.b64encode(credentials.encode()).decode()
 
     class Handler(BaseHTTPRequestHandler):
-        def _send(self, status: int, body: bytes = b"", content_type: str = "application/octet-stream") -> None:
+        def _send(self, status: int, body: bytes = b"", content_type: str = "application/octet-stream",
+                  headers: dict[str, str] | None = None) -> None:
             self.send_response(status)
             self.send_header("Content-Type", content_type)
             self.send_header("Content-Length", str(len(body)))
+            for key, value in (headers or {}).items():
+                self.send_header(key, value)
             self.end_headers()
             if self.command != "HEAD":
                 self.wfile.write(body)
@@ -93,6 +98,15 @@ def make_handler(root: Path, credentials: str, anonymous_read: bool):
             data = read_artifact(root, relative)
             if data is None:
                 return self._send(404)
+            match = _RANGE.match(self.headers.get("Range", "")) if "!/" not in relative else None
+            if match and data:
+                first, last = match.groups()
+                if first == "":
+                    start, end = max(0, len(data) - int(last)), len(data) - 1
+                else:
+                    start, end = int(first), min(int(last) if last else len(data) - 1, len(data) - 1)
+                return self._send(206, data[start:end + 1],
+                                  headers={"Content-Range": f"bytes {start}-{end}/{len(data)}", "Accept-Ranges": "bytes"})
             self._send(200, data)
 
         do_HEAD = do_GET
```

- [ ] **Step 4: Run the unit tests**

Run: `make test-unit`
Expected: PASS, `275 passed`.

- [ ] **Step 5: Stage every rule in the seed, and assert them in the suite**

```diff
diff --git a/tests/integration/seed.py b/tests/integration/seed.py
index 8392b83..68bc239 100644
--- a/tests/integration/seed.py
+++ b/tests/integration/seed.py
@@ -1,8 +1,10 @@
 """Seed data for the air-gapped integration test; runs on a network with internet access.
 
   seed.py versions   print LTS_OLD, LTS_NEW, WEEKLY (for .versions.env)
-  seed.py repo ...   build a Maven-layout repository from jenkins-plugin-cli downloads, with the
-                     real POMs, three core WARs and two deliberately broken test plugins
+  seed.py repo ...   build a Maven-layout repository from jenkins-plugin-cli downloads the way operators
+                     procure plugins: mostly .hpi files without POMs, one real POM, one saved error page
+                     as a POM, a misplaced plugin, POM-less test plugins, and core WARs (two of them
+                     released more than 400 days ago, for CORE_AGE_DAYS)
 """
 from __future__ import annotations
 
@@ -19,6 +21,11 @@ UPDATES = "https://updates.jenkins.io"
 RELEASES = "https://repo.jenkins-ci.org/releases"
 STUB_POM = (b"<project><modelVersion>4.0.0</modelVersion><groupId>io.jenkins.uctest</groupId>"
             b"<artifactId>uc-test-stubpom</artifactId><version>1.0</version><packaging>hpi</packaging></project>")
+# What an operator's download tool saved as a .pom (seen in a real repository, 2026-09-22).
+HTML_404 = (b"<html>\r\n<head><title>404 Not Found</title></head>\r\n<body>\r\n<center><h1>404 Not Found</h1>"
+            b"</center>\r\n<hr><center>nginx</center>\r\n</body>\r\n</html>\r\n")
+# LTS releases from early 2024: always more than 400 days old by the time this test runs.
+OLD_CORES = ("2.426.3", "2.440.3")
 
 
 def get(url: str) -> bytes:
@@ -40,44 +47,69 @@ def put(root: Path, group: str, artifact: str, version: str, extension: str, dat
     path.write_bytes(data)
 
 
-def synthetic_hpi(artifact: str, jenkins_version: str) -> bytes:
+def synthetic_hpi(artifact: str, jenkins_version: str, *, embed_pom: bool = False) -> bytes:
+    """A plugin laid out like a pre-2015 build (index.jelly under WEB-INF/classes), optionally with an
+    embedded POM the repo-adapter can serve."""
     buffer = io.BytesIO()
     with zipfile.ZipFile(buffer, "w") as archive:
         archive.writestr("META-INF/MANIFEST.MF", (
             "Manifest-Version: 1.0\r\nGroup-Id: io.jenkins.uctest\r\n"
-            f"Short-Name: {artifact}\r\nExtension-Name: {artifact}\r\nLong-Name: UC test {artifact}\r\n"
-            f"Plugin-Version: 1.0\r\nJenkins-Version: {jenkins_version}\r\n\r\n"
+            f"Artifact-Id: {artifact}\r\nShort-Name: {artifact}\r\nExtension-Name: {artifact}\r\n"
+            f"Long-Name: UC test {artifact}\r\nPlugin-Version: 1.0\r\nJenkins-Version: {jenkins_version}\r\n\r\n"
         ))
-        archive.writestr("index.jelly", f"<div>uc integration-test plugin {artifact}</div>")
+        if embed_pom:
+            archive.writestr(f"META-INF/maven/io.jenkins.uctest/{artifact}/pom.xml", (
+                "<project><modelVersion>4.0.0</modelVersion><groupId>io.jenkins.uctest</groupId>"
+                f"<artifactId>{artifact}</artifactId><version>1.0</version><packaging>hpi</packaging>"
+                f"<name>UC test {artifact}</name></project>"
+            ))
+        archive.writestr("WEB-INF/classes/index.jelly", f"<div>uc integration-test plugin {artifact}</div>")
     return buffer.getvalue()
 
 
 def repo(args: argparse.Namespace) -> None:
     root = args.out / args.repository
-    seeded = []
-    for plugin in sorted(args.downloads.glob("*.[hj]pi")):
+    seeded, paths = [], {}
+    downloads = sorted(args.downloads.glob("*.[hj]pi"))
+    if len(downloads) < 3:
+        raise SystemExit(f"need at least 3 .hpi/.jpi files in {args.downloads}; did the seed-plugins step fail?")
+    others = [p.stem for p in downloads if p.stem != args.top_plugin]
+    real_pom, html_pom = others[0], others[1]  # every other plugin gets no POM: the adapter derives it
+    for plugin in downloads:
         data = plugin.read_bytes()
         with zipfile.ZipFile(io.BytesIO(data)) as archive:
             manifest = parse_manifest(archive.read("META-INF/MANIFEST.MF").decode("utf-8", "replace"))
         group = manifest["Group-Id"]
-        artifact = manifest.get("Short-Name") or manifest["Extension-Name"]
+        artifact = manifest.get("Artifact-Id") or manifest.get("Short-Name") or manifest["Extension-Name"]
         version = manifest["Plugin-Version"].split(" ")[0]
         put(root, group, artifact, version, "hpi", data)
-        put(root, group, artifact, version, "pom",
-            get(f"{RELEASES}/{group.replace('.', '/')}/{artifact}/{version}/{artifact}-{version}.pom"))
+        if plugin.stem == real_pom:
+            put(root, group, artifact, version, "pom",
+                get(f"{RELEASES}/{group.replace('.', '/')}/{artifact}/{version}/{artifact}-{version}.pom"))
+        elif plugin.stem == html_pom:
+            put(root, group, artifact, version, "pom", HTML_404)
         seeded.append(f"{artifact}:{version}")
-    if not seeded:
-        raise SystemExit(f"no .hpi/.jpi files in {args.downloads}; did the seed-plugins step fail?")
+        paths[artifact] = f"{group.replace('.', '/')}/{artifact}/{version}/{artifact}-{version}.hpi"
     cores = args.cores.split(",")
     for core in cores:
         base = f"{RELEASES}/org/jenkins-ci/main/jenkins-war/{core}/jenkins-war-{core}"
         put(root, "org.jenkins-ci.main", "jenkins-war", core, "war", get(base + ".war"))
         put(root, "org.jenkins-ci.main", "jenkins-war", core, "pom", get(base + ".pom"))
+    for core, plugin_id in zip(OLD_CORES, ("uc-test-old-a", "uc-test-old-b")):
+        base = f"{RELEASES}/org/jenkins-ci/main/jenkins-war/{core}/jenkins-war-{core}"
+        put(root, "org.jenkins-ci.main", "jenkins-war", core, "war", get(base + ".war"))  # its POM is derived
+        put(root, "io.jenkins.uctest", plugin_id, "1.0", "hpi", synthetic_hpi(plugin_id, core, embed_pom=True))
     put(root, "io.jenkins.uctest", "uc-test-nopom", "1.0", "hpi", synthetic_hpi("uc-test-nopom", args.jenkins_version))
     put(root, "io.jenkins.uctest", "uc-test-stubpom", "1.0", "hpi", synthetic_hpi("uc-test-stubpom", args.jenkins_version))
     put(root, "io.jenkins.uctest", "uc-test-stubpom", "1.0", "pom", STUB_POM)
-    (args.out / "seeded.json").write_text(json.dumps({"plugins": seeded, "cores": cores}, indent=2))
-    print(f"seeded {len(seeded)} plugins and cores {', '.join(cores)} into {root}")
+    # Its manifest says io.jenkins.uctest, but it is stored under another group, as happened in practice.
+    put(root, "org.jenkins-ci.plugins", "uc-test-misplaced", "1.0", "hpi",
+        synthetic_hpi("uc-test-misplaced", args.jenkins_version, embed_pom=True))
+    (args.out / "seeded.json").write_text(json.dumps({
+        "plugins": seeded, "paths": paths, "real_pom": real_pom, "html_pom": html_pom,
+        "cores": cores + list(OLD_CORES), "old_cores": list(OLD_CORES),
+    }, indent=2))
+    print(f"seeded {len(seeded)} plugins and cores {', '.join(cores + list(OLD_CORES))} into {root}")
 
 
 def main() -> None:
@@ -90,6 +122,7 @@ def main() -> None:
     build.add_argument("--repository", required=True)
     build.add_argument("--cores", required=True)
     build.add_argument("--jenkins-version", required=True)
+    build.add_argument("--top-plugin", required=True)
     args = parser.parse_args()
     versions() if args.command == "versions" else repo(args)
 
```

```diff
diff --git a/tests/integration/test_airgapped.py b/tests/integration/test_airgapped.py
index 88bc38b..aaca7e2 100644
--- a/tests/integration/test_airgapped.py
+++ b/tests/integration/test_airgapped.py
@@ -1,10 +1,13 @@
-"""Air-gapped end to end (spec §13): doctor, generate, serve, and real Jenkins controllers installing plugins."""
+"""Air-gapped end to end (spec §13, repo-adapter spec §9): doctor, generate through the repo-adapter, serve,
+and real Jenkins controllers installing plugins. `make it-airgapped` runs it once per repository type."""
 from __future__ import annotations
 
+import datetime as dt
 import json
 import os
 import subprocess
 import sys
+import zipfile
 from pathlib import Path
 
 import pytest
@@ -14,6 +17,7 @@ sys.path.insert(0, str(Path(__file__).resolve().parent))
 from jenkins import Jenkins  # noqa: E402
 from netutil import fetch, get, wait_for  # noqa: E402
 
+from uc.repo_adapter.archive import archive_date, plausible  # noqa: E402
 from uc.versions import VersionTool  # noqa: E402
 
 WEB = "http://web:8080"
@@ -24,7 +28,9 @@ CONTROLLERS = {
     "jenkins-weekly": os.environ["WEEKLY"],
 }
 TOP_PLUGIN = os.environ["SEED_TOP_PLUGIN"]
+DOWNLOAD_PREFIX = os.environ["DOWNLOAD_PREFIX"]
 SEEDED = json.loads(Path("/seed/repo/seeded.json").read_text())
+ADAPTER_DIR = {"REPO_ADAPTER_DIR": "/data/repo-adapter"}
 VERSIONS = VersionTool(Path("/opt/uc/generator"), Path("/opt/uc/tools"))
 
 CONFIGURE = """
@@ -64,7 +70,18 @@ def test_doctor_reports_the_seeded_defects():
     assert report["hpi_without_pom"] == ["io/jenkins/uctest/uc-test-nopom/1.0/uc-test-nopom-1.0.hpi"]
     assert "uc-test-stubpom:1.0" in report["stub_poms"]
     assert report["missing_dependencies"] == []
-    assert sorted(report["cores"]) == sorted(set(SEEDED["cores"]))
+    assert sorted(report["cores"]) == sorted(set(SEEDED["cores"]))  # WAR POMs derived where missing
+    adapter = report["repo_adapter"]
+    assert adapter["misplaced"] == [{
+        "path": "org/jenkins-ci/plugins/uc-test-misplaced/1.0/uc-test-misplaced-1.0.hpi",
+        "correct_path": "io/jenkins/uctest/uc-test-misplaced/1.0/uc-test-misplaced-1.0.hpi",
+    }]
+    assert [item["reason"] for item in adapter["replaced_poms"]] == ["not XML"]  # the saved error page
+    derived = {gav.split(":")[1] for gav in adapter["derived_poms"]}
+    assert {TOP_PLUGIN, SEEDED["html_pom"], "uc-test-old-a", "uc-test-old-b", "jenkins-war"} <= derived
+    assert SEEDED["real_pom"] not in derived  # the repository's own POM wins
+    assert [item["gav"] for item in adapter["needs_pom"]] == ["io.jenkins.uctest:uc-test-nopom:1.0"]
+    assert adapter["skipped"] == []
 
 
 @pytest.fixture(scope="module")
@@ -80,13 +97,46 @@ def test_generation_succeeds_offline_with_only_warnings(release):
     assert release["mode"] == "airgapped"
     assert any("GitHub" in warning for warning in release["warnings"])
     assert release["doctor"]["hpi_without_pom"] == 1
+    assert release["doctor"]["repo_adapter"]["misplaced"] == 1
     plugins = json.loads(get(WEB + "/current/update-center.actual.json"))["plugins"]
     assert "uc-test-stubpom" in plugins  # a stub POM degrades metadata but is still published
-    assert "uc-test-nopom" not in plugins  # no POM: dropped by the generator, as uc doctor predicted
-    assert TOP_PLUGIN in plugins
+    assert "uc-test-nopom" not in plugins  # no POM anywhere: dropped by the generator, as uc doctor predicted
+    assert "uc-test-misplaced" not in plugins  # its manifest names another group
+    assert {TOP_PLUGIN, SEEDED["html_pom"], "uc-test-old-a", "uc-test-old-b"} <= set(plugins)
     assert release["plugin_count"] == release["doctor"]["plugins"] == len(plugins)
 
 
+def test_descriptions_are_read_from_inside_the_archives(release):
+    plugins = json.loads(get(WEB + "/current/update-center.actual.json"))["plugins"]
+    assert plugins[TOP_PLUGIN]["excerpt"]  # no .jar in the repository: index.jelly from the .hpi
+    assert "integration-test plugin" in plugins["uc-test-old-a"]["excerpt"]  # WEB-INF/classes/index.jelly
+
+
+def test_release_dates_are_read_from_the_archives(release):
+    path = Path("/seed/repo/jenkins") / SEEDED["paths"][TOP_PLUGIN]
+    with zipfile.ZipFile(path) as archive:
+        built = archive_date(archive)
+    version = path.parent.name
+    stamp = json.loads(get(WEB + "/current/plugin-versions.json"))["plugins"][TOP_PLUGIN][version]["releaseTimestamp"]
+    released = dt.datetime.strptime(stamp[:19], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=dt.timezone.utc)
+    if plausible(built, dt.datetime.now(dt.timezone.utc)) is None:
+        assert abs(released - built) < dt.timedelta(minutes=1)  # the build date, not the upload
+    else:
+        assert abs(released - dt.datetime.now(dt.timezone.utc)) < dt.timedelta(days=1)  # reported fallback
+
+
+def test_every_stored_core_that_plugins_require_gets_a_tier(release):
+    assert set(SEEDED["old_cores"]) <= set(release["tiers"]["stable"])  # CORE_AGE_DAYS=0, the default
+
+
+def test_core_age_400_keeps_at_most_one_old_core(release):
+    result = uc("generate", env={"CORE_AGE_DAYS": "400", "UC_DATA_DIR": "/data-age", **ADAPTER_DIR})
+    assert result.returncode == 0, result.stdout[-4000:] + result.stderr[-4000:]
+    stable = json.loads(Path("/data-age/status.json").read_text())["tiers"]["stable"]
+    oldest = SEEDED["old_cores"][0]
+    assert oldest not in stable  # upstream keeps one core older than 400 days as the floor, then stops
+
+
 def test_signed_metadata_points_at_the_update_center(release):
     doc = json.loads(get(WEB + "/current/update-center.actual.json"))
     urls = [doc["core"]["url"]] + [p["url"] for p in doc["plugins"].values()]
@@ -99,7 +149,7 @@ def test_downloads_redirect_into_artifactory(release):
     plugin = doc["plugins"][TOP_PLUGIN]
     response = fetch(plugin["url"])
     assert response.status == 302
-    assert response.location.startswith("http://artifactory:8081/artifactory/jenkins/")
+    assert response.location.startswith(DOWNLOAD_PREFIX)
     assert len(get(response.location)) > 0
 
 
@@ -129,7 +179,7 @@ def test_controller_trusts_the_signature_and_installs(release, controller):
 
 
 def test_proxy_mode_installs_without_anonymous_artifactory_access(release):
-    result = uc("generate", env={"PUBLIC_URL": WEB_PROXY, "UC_DATA_DIR": "/data-proxy"})
+    result = uc("generate", env={"PUBLIC_URL": WEB_PROXY, "UC_DATA_DIR": "/data-proxy", **ADAPTER_DIR})
     assert result.returncode == 0, result.stdout[-4000:] + result.stderr[-4000:]
     wait_for(WEB_PROXY + "/uctest.json")
     jenkins = Jenkins("http://jenkins-weekly:8080")
```

- [ ] **Step 6: Replace the air-gapped stack, add the per-type settings, loop over types in the Makefile**

Replace the whole file `tests/integration/airgapped.yaml`:

```yaml
# Air-gapped integration test (spec §13, repo-adapter spec §9). Run with `make it-airgapped`, which runs it
# once per repository type; tests/integration/types/<type>.env supplies the IT_* values for each.
name: uc-it-airgapped

networks:
  airgap:
    internal: true
  internet: {}

x-uc-test: &uc-test
  image: ${UC_TEST_IMAGE:-jenkins-uc/uc-test:local}
  user: "0:0"

x-jenkins: &jenkins
  networks: [airgap]
  environment:
    JAVA_OPTS: "-Djenkins.install.runSetupWizard=false"
  volumes:
    - trust:/var/jenkins_home/update-center-rootCAs:ro
    - ./jenkins-init.groovy:/var/jenkins_home/init.groovy.d/uc-test.groovy:ro
  healthcheck:
    test: ["CMD", "curl", "-fsS", "-o", "/dev/null", "http://localhost:8080/login"]
    interval: 5s
    timeout: 5s
    retries: 60
    start_period: 20s

x-blocked-hosts: &blocked-hosts
  - "api.github.com:127.0.0.1"
  - "github.com:127.0.0.1"
  - "codeload.github.com:127.0.0.1"
  - "raw.githubusercontent.com:127.0.0.1"
  - "reports.jenkins.io:127.0.0.1"
  - "updates.jenkins.io:127.0.0.1"

services:
  # ---- seeding: profile "seed", internet access ----
  seed-versions:
    <<: *uc-test
    profiles: ["seed"]
    networks: [internet]
    entrypoint: ["python3", "/tests/integration/seed.py", "versions"]
    volumes:
      - ../../tests:/tests:ro
      - ../../uc/uc:/opt/uc/python/uc:ro

  seed-plugins:
    image: jenkins/jenkins:${LTS_OLD:-lts}
    profiles: ["seed"]
    user: "0:0"
    networks: [internet]
    entrypoint: ["jenkins-plugin-cli", "--plugins", "${SEED_PLUGINS:-timestamper}", "--plugin-download-directory", "/seed/downloads"]
    volumes:
      - seed:/seed

  seed-repo:
    <<: *uc-test
    profiles: ["seed"]
    networks: [internet]
    entrypoint: ["python3", "/tests/integration/seed.py", "repo", "--downloads", "/seed/downloads",
                 "--out", "/seed/repo", "--repository", "jenkins",
                 "--cores", "${LTS_OLD:-x},${LTS_NEW:-x},${WEEKLY:-x}", "--jenkins-version", "${LTS_OLD:-x}",
                 "--top-plugin", "${SEED_TOP_PLUGIN:-timestamper}"]
    volumes:
      - seed:/seed
      - ../../tests:/tests:ro
      - ../../uc/uc:/opt/uc/python/uc:ro

  pki:
    image: ${UC_IMAGE:-jenkins-uc/uc:local}
    profiles: ["seed"]
    user: "0:0"
    network_mode: none
    entrypoint:
      - sh
      - -c
      - >-
        uc pki init --out /pki && cp /pki/root-ca.crt /trust/ &&
        printf uc > /pki/repo_username && printf uc-secret > /pki/repo_password &&
        printf uc:uc-secret > /pki/download_proxy_auth && chmod -R a+rX /pki /trust
    volumes:
      - pki:/pki
      - trust:/trust

  # ---- the air-gapped side: no route to the internet ----
  artifactory:
    <<: *uc-test
    networks: [airgap]
    entrypoint: ["python3", "/tests/integration/fake_artifactory.py", "--root", "/seed/repo", "--port", "8081",
                 "--credentials", "uc:uc-secret", "--anonymous-read"]
    volumes:
      - seed:/seed:ro
      - ../../tests:/tests:ro

  artifactory-private:
    <<: *uc-test
    networks: [airgap]
    entrypoint: ["python3", "/tests/integration/fake_artifactory.py", "--root", "/seed/repo", "--port", "8081",
                 "--credentials", "uc:uc-secret"]
    volumes:
      - seed:/seed:ro
      - ../../tests:/tests:ro

  keeper:
    <<: *uc-test
    networks: [airgap]
    entrypoint: ["python3", "/tests/integration/fake_artifact_keeper.py", "--root", "/seed/repo", "--port", "8081",
                 "--user", "uc", "--token", "uc-secret", "--anonymous-read"]
    volumes:
      - seed:/seed:ro
      - ../../tests:/tests:ro

  keeper-private:
    <<: *uc-test
    networks: [airgap]
    entrypoint: ["python3", "/tests/integration/fake_artifact_keeper.py", "--root", "/seed/repo", "--port", "8081",
                 "--user", "uc", "--token", "uc-secret"]
    volumes:
      - seed:/seed:ro
      - ../../tests:/tests:ro

  repo-adapter:
    <<: *uc-test
    networks: [airgap]
    extra_hosts: *blocked-hosts
    entrypoint: ["python3", "-m", "uc", "repo-adapter"]
    environment:
      MODE: airgapped
      PUBLIC_URL: "http://web:8080"
      REPO_TYPE: ${IT_REPO_TYPE:-artifactory}
      REPO_URL: ${IT_ADAPTER_BACKEND_URL:-http://artifactory-private:8081/artifactory/}
      REPO_KEY: jenkins
      SECRETS_DIR: /pki
      UC_DATA_DIR: /data
    healthcheck:
      test: ["CMD", "python3", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8081/healthz', timeout=3)"]
      interval: 2s
      timeout: 5s
      retries: 30
    depends_on:
      artifactory-private: {condition: service_started}
      keeper-private: {condition: service_started}
    volumes:
      - uc-data:/data
      - pki:/pki:ro
      - ../../uc/uc:/opt/uc/python/uc:ro

  web:
    image: ${WEB_IMAGE:-jenkins-uc/web:local}
    networks: [airgap]
    environment:
      MODE: airgapped
      PUBLIC_URL: "http://web:8080"
      REPO_TYPE: ${IT_REPO_TYPE:-artifactory}
      REPO_URL: ${IT_REPO_URL:-http://artifactory:8081/artifactory/}
      REPO_KEY: jenkins
    volumes:
      - uc-data:/data:ro

  web-proxy:
    image: ${WEB_IMAGE:-jenkins-uc/web:local}
    networks: [airgap]
    environment:
      MODE: airgapped
      PUBLIC_URL: "http://web-proxy:8080"
      DOWNLOAD_ACCESS: proxy
      DOWNLOAD_BACKEND_URL: ${IT_PROXY_BACKEND_URL:-http://artifactory-private:8081/artifactory/jenkins}
      DOWNLOAD_PROXY_AUTH_FILE: /pki/download_proxy_auth
    volumes:
      - uc-data-proxy:/data:ro
      - pki:/pki:ro

  jenkins-lts-old:
    <<: *jenkins
    image: jenkins/jenkins:${LTS_OLD:-lts}

  jenkins-lts-new:
    <<: *jenkins
    image: jenkins/jenkins:${LTS_NEW:-lts}

  jenkins-weekly:
    <<: *jenkins
    image: jenkins/jenkins:${WEEKLY:-latest}

  driver:
    <<: *uc-test
    networks: [airgap]
    extra_hosts: *blocked-hosts
    environment:
      MODE: airgapped
      PUBLIC_URL: "http://web:8080"
      REPO_TYPE: ${IT_REPO_TYPE:-artifactory}
      REPO_URL: ${IT_ADAPTER_BACKEND_URL:-http://artifactory-private:8081/artifactory/}
      REPO_KEY: jenkins
      REPO_ADAPTER_DIR: /data/repo-adapter  # also for runs with another UC_DATA_DIR
      SECRETS_DIR: /pki
      UC_DATA_DIR: /data
      DOWNLOAD_PREFIX: ${IT_DOWNLOAD_PREFIX:-http://artifactory:8081/artifactory/jenkins/}
      LTS_OLD: ${LTS_OLD:-x}
      LTS_NEW: ${LTS_NEW:-x}
      WEEKLY: ${WEEKLY:-x}
      SEED_TOP_PLUGIN: ${SEED_TOP_PLUGIN:-timestamper}
    entrypoint: ["python3", "-m", "pytest", "-p", "no:cacheprovider", "-q", "-x", "/tests/integration/test_airgapped.py"]
    depends_on:
      artifactory: {condition: service_started}
      keeper: {condition: service_started}
      repo-adapter: {condition: service_healthy}
      web: {condition: service_started}
      web-proxy: {condition: service_started}
      jenkins-lts-old: {condition: service_healthy}
      jenkins-lts-new: {condition: service_healthy}
      jenkins-weekly: {condition: service_healthy}
    volumes:
      - uc-data:/data
      - uc-data-proxy:/data-proxy
      - uc-data-age:/data-age
      - pki:/pki:ro
      - seed:/seed:ro
      - ../../tests:/tests:ro
      - ../../uc/uc:/opt/uc/python/uc:ro

  plugin-cli:
    image: jenkins/jenkins:${LTS_NEW:-lts}
    networks: [airgap]
    user: "0:0"
    environment:
      JENKINS_UC: "http://web:8080"
      JENKINS_UC_EXPERIMENTAL: "http://web:8080/experimental"
      JENKINS_PLUGIN_INFO: "http://web:8080/current/plugin-versions.json"
    entrypoint: ["jenkins-plugin-cli", "--verbose", "--plugins", "${SEED_TOP_PLUGIN:-timestamper}",
                 "--plugin-download-directory", "/tmp/plugins"]

volumes:
  seed:
  pki:
  trust:
  uc-data:
  uc-data-proxy:
  uc-data-age:
```

Create `tests/integration/types/artifactory.env`:

```bash
# make it-airgapped: the air-gapped run against (fake) Artifactory.
IT_REPO_TYPE=artifactory
IT_REPO_URL=http://artifactory:8081/artifactory/
IT_ADAPTER_BACKEND_URL=http://artifactory-private:8081/artifactory/
IT_PROXY_BACKEND_URL=http://artifactory-private:8081/artifactory/jenkins
IT_DOWNLOAD_PREFIX=http://artifactory:8081/artifactory/jenkins/
```

Create `tests/integration/types/artifact-keeper.env`:

```bash
# make it-airgapped: the air-gapped run against (fake) Artifact Keeper.
IT_REPO_TYPE=artifact-keeper
IT_REPO_URL=http://keeper:8081/
IT_ADAPTER_BACKEND_URL=http://keeper-private:8081/
IT_PROXY_BACKEND_URL=http://keeper-private:8081/maven/jenkins
IT_DOWNLOAD_PREFIX=http://keeper:8081/maven/jenkins/
```

```diff
diff --git a/Makefile b/Makefile
index 594e478..8e20f88 100644
--- a/Makefile
+++ b/Makefile
@@ -57,6 +57,9 @@ compose-check:
 
 IT_DIR = tests/integration
 IT_AIR = docker compose -f $(IT_DIR)/airgapped.yaml --env-file $(IT_DIR)/.versions.env
+# Each repository type gets a fresh update center (its data volumes) against the same seeded repository.
+IT_TYPES ?= artifactory artifact-keeper
+IT_STATE = uc-it-airgapped_uc-data uc-it-airgapped_uc-data-proxy uc-it-airgapped_uc-data-age
 
 it-airgapped: build
 	docker compose -f $(IT_DIR)/airgapped.yaml --profile seed run --rm -T seed-versions > $(IT_DIR)/.versions.env
@@ -64,9 +67,13 @@ it-airgapped: build
 	$(IT_AIR) --profile seed run --rm seed-plugins
 	$(IT_AIR) --profile seed run --rm seed-repo
 	$(IT_AIR) --profile seed run --rm pki
-	$(IT_AIR) run --rm driver $(ARGS); rc=$$?; \
-	  if [ $$rc -eq 0 ]; then $(IT_AIR) run --rm plugin-cli; rc=$$?; fi; \
-	  $(IT_AIR) --profile seed down -v --remove-orphans >/dev/null 2>&1; exit $$rc
+	rc=0; for type in $(IT_TYPES); do \
+	  echo "== air-gapped run against $$type"; \
+	  env="--env-file $(IT_DIR)/types/$$type.env"; \
+	  $(IT_AIR) $$env rm --stop --force >/dev/null 2>&1; docker volume rm -f $(IT_STATE) >/dev/null; \
+	  $(IT_AIR) $$env run --rm driver $(ARGS) && $(IT_AIR) $$env run --rm plugin-cli || { rc=1; break; }; \
+	done; \
+	$(IT_AIR) --profile seed down -v --remove-orphans >/dev/null 2>&1; exit $$rc
 
 it-clean:
 	docker compose -f $(IT_DIR)/airgapped.yaml --profile seed down -v --remove-orphans
```

- [ ] **Step 7: Run the air-gapped suite for both types**

Run: `make it-airgapped` (it downloads about 450 MB of WARs and plugins first; allow 15 to 25 minutes)
Expected: `== air-gapped run against artifactory`, `15 passed`, the `jenkins-plugin-cli` run succeeds, then the same for `artifact-keeper`, and `make` exits 0.

- [ ] **Step 8: Commit**

```bash
git add tests/integration/fake_artifact_keeper.py tests/unit/test_fake_artifact_keeper.py \
  tests/integration/fake_artifactory.py tests/unit/test_fake_artifactory.py tests/integration/seed.py \
  tests/integration/test_airgapped.py tests/integration/airgapped.yaml tests/integration/types/artifactory.env \
  tests/integration/types/artifact-keeper.env Makefile
git commit -m "Air-gapped suite through the repo-adapter, against Artifactory and Artifact Keeper"
```

### Task 13: Operator docs and spec amendments

Document how to point the stack at either repository, how to find a plugin's group on Linux and Windows, the private-CA override for the adapter, and troubleshooting rows for each new error. Then bring both specs up to what implementation established:

- upstream cannot authenticate file reads, so the adapter's GETs need no token
- whole-file GETs are POM-only
- no listing rebuild on a miss
- the cache marker names
- the two adapter-location settings
- the core-age test wording

**Files:**
- Modify: `docs/operations.md`, `.env.airgapped.example`, `.env.online.example`, `secrets/README.md`, `README.md`, `docs/spikes/README.md`, `docs/superpowers/specs/2026-09-22-repo-adapter-design.md`, `docs/superpowers/specs/2026-09-22-jenkins-update-center-design.md`

- [ ] **Step 1: Update the operator docs and examples**

````diff
diff --git a/.env.airgapped.example b/.env.airgapped.example
index 10cdd25..6b9f54d 100644
--- a/.env.airgapped.example
+++ b/.env.airgapped.example
@@ -1,9 +1,17 @@
-# Copy to .env. Air-gapped: the generator reads your internal Artifactory (see docs/operations.md,
-# "What the Artifactory repository must contain") and downloads redirect to it. Every setting: spec §11.
+# Copy to .env. Air-gapped: the generator reads your internal repository through the repo-adapter
+# (see docs/operations.md, "What the repository must contain") and downloads redirect to it.
+# Every setting: spec §11 and the repo-adapter spec §6.
 MODE=airgapped
 PUBLIC_URL=https://jenkins-updates.corp.example
 REPO_URL=https://artifactory.corp.example/artifactory/
 REPO_KEY=jenkins-plugins-local
+# Artifact Keeper instead of Artifactory: its base URL, and the repository key (not the display name):
+# REPO_TYPE=artifact-keeper
+# REPO_URL=https://artifacts.corp.example/
+# Repository behind a private CA? Mount the bundle into repo-adapter and point this at it:
+# REPO_CA_FILE=/etc/ssl/uc-repo-ca.crt
+# Every stored core that a plugin requires gets a tier; 400 behaves like updates.jenkins.io:
+# CORE_AGE_DAYS=0
 # Controllers cannot read Artifactory anonymously? Stream downloads through the web tier instead:
 # DOWNLOAD_ACCESS=proxy
 # Backend uses a private CA? Mount the bundle and point this at it (docs/operations.md, section 7):
diff --git a/.env.online.example b/.env.online.example
index 6fe070e..41eeca9 100644
--- a/.env.online.example
+++ b/.env.online.example
@@ -4,5 +4,7 @@ MODE=online
 PUBLIC_URL=https://jenkins-updates.example.com
 # GENERATE_SCHEDULE=0 */6 * * *
 # RESOURCES_REF=master
+# Tiers for cores released within this many days (0: every core a plugin requires):
+# CORE_AGE_DAYS=400
 # JAVA_TOOL_OPTIONS=-Xmx4g -Dhttps.proxyHost=proxy.example.com -Dhttps.proxyPort=3128
 # HTTP_PORT=8080
diff --git a/README.md b/README.md
index 1718288..290be95 100644
--- a/README.md
+++ b/README.md
@@ -6,11 +6,13 @@ The same update center Jenkins uses (`updates.jenkins.io`), in two containers:
   with your own PKI, validates each release and publishes it atomically. It keeps the last five
   releases for rollback.
 - **web**: Apache serving the result, including the `?version=` routing to `dynamic-*` tiers, every
-  upstream JSON file, and download redirects to get.jenkins.io or your Artifactory.
+  upstream JSON file, and download redirects to get.jenkins.io or your repository.
+- **repo-adapter**: reads Artifactory or Artifact Keeper for the generator, and fills in the POMs,
+  descriptions and release dates procurement tends to leave out, from inside each `.hpi`.
 
 Two modes:
 - `online` reads `repo.jenkins-ci.org`.
-- `airgapped` reads your internal Artifactory and skips whatever it cannot reach.
+- `airgapped` reads your internal Artifactory or Artifact Keeper and skips whatever it cannot reach.
 
 ```bash
 make build
diff --git a/docs/operations.md b/docs/operations.md
index c028aba..80bcb69 100644
--- a/docs/operations.md
+++ b/docs/operations.md
@@ -1,6 +1,7 @@
 # Operating the Jenkins update center
 
-Design and rationale: `docs/superpowers/specs/2026-09-22-jenkins-update-center-design.md`.
+Design and rationale: `docs/superpowers/specs/2026-09-22-jenkins-update-center-design.md`, and for
+reading Artifact Keeper or filling gaps in a repository, `docs/superpowers/specs/2026-09-22-repo-adapter-design.md`.
 
 ## 1. Build
 
@@ -16,6 +17,18 @@ origin Jenkins will use, with no path (for example `https://jenkins-updates.exam
 baked into the signed metadata, so changing it requires a new generation. To keep several
 configurations side by side, set `UC_ENV_FILE=<file>` when running `docker compose`.
 
+Air-gapped, set `REPO_TYPE`, `REPO_URL` and `REPO_KEY` to your repository:
+
+| Repository | `REPO_TYPE` | `REPO_URL` | `REPO_KEY` |
+|---|---|---|---|
+| Artifactory | `artifactory` (default) | `https://artifactory.corp.example/artifactory/` | the repository key |
+| Artifact Keeper | `artifact-keeper` | the server's base URL, `https://artifacts.corp.example/` | the repository key (not its display name) |
+
+The **repo-adapter** service reads the repository for the generator. It is on by default in
+air-gapped mode and always on for Artifact Keeper (`REPO_ADAPTER`). It fills in what procurement
+often leaves out: each plugin's POM, description and release date come from inside its `.hpi`
+when the repository has no usable copy. See section 9.
+
 ## 3. Signing keys
 
 ```bash
@@ -40,7 +53,7 @@ expiring certificate stops publication before Jenkins would reject the signature
 ```bash
 make secrets-placeholders
 printf '%s' 'svc-jenkins-uc' > secrets/repo_username
-printf '%s' '...password or API token...' > secrets/repo_password
+printf '%s' '...password or API token...' > secrets/repo_password   # Artifact Keeper: an API token
 # online, recommended: GitHub user and a token without scopes
 printf '%s' 'my-bot' > secrets/github_username
 printf '%s' 'ghp_...' > secrets/github_token
@@ -49,7 +62,9 @@ printf '%s' 'svc-read:...' > secrets/download_proxy_auth
 ```
 
 Online mode uses a free Jenkins community account (https://accounts.jenkins.io) for
-`repo.jenkins-ci.org`. Anonymous AQL queries are refused with HTTP 403.
+`repo.jenkins-ci.org`. Anonymous AQL queries are refused with HTTP 403. With the repo-adapter on
+(the air-gapped default), only the `repo-adapter` container receives these two files; `uc` and the
+scheduler authenticate to the adapter with a token it creates in `/data/repo-adapter/token`.
 
 ### File ownership on Linux
 
@@ -154,16 +169,53 @@ scheduled runs, or the terminal for `docker compose run --rm uc generate`.
 
 ## 9. Air-gapped specifics
 
-### What the Artifactory repository must contain
+### What the repository must contain
+
+- One Artifactory repository (local or virtual) or one Artifact Keeper Maven repository.
+- Each plugin's `.hpi` (or `.jpi`) in Maven layout, under the group its own manifest names:
+  `<group path>/<artifactId>/<version>/<artifactId>-<version>.hpi`. Plugins do not share one group
+  (`caffeine-api` is `io.jenkins.plugins`, `git` is `org.jenkins-ci.plugins`). A file downloaded from
+  updates.jenkins.io is named `<id>.hpi`; rename it to `<artifactId>-<version>.hpi`. To read the
+  coordinates, open the `.hpi` (it is a zip) and look at `META-INF/MANIFEST.MF`:
+
+  ```bash
+  unzip -p caffeine-api.hpi META-INF/MANIFEST.MF | grep -E '^(Group-Id|Artifact-Id|Plugin-Version):'
+  ```
+
+  ```powershell
+  Add-Type -AssemblyName System.IO.Compression.FileSystem
+  $zip = [IO.Compression.ZipFile]::OpenRead("$PWD\caffeine-api.hpi")
+  (New-Object IO.StreamReader($zip.GetEntry('META-INF/MANIFEST.MF').Open())).ReadToEnd() -split "`r?`n" |
+    Select-String '^(Group-Id|Artifact-Id|Plugin-Version):'
+  $zip.Dispose()
+  ```
+
+- **POMs are optional** with the repo-adapter: it serves the one embedded in each `.hpi`. Only plugins
+  built without Maven (Gradle) have none; `uc doctor` lists them with the repo.jenkins-ci.org URL of
+  the real `.pom` to procure. A saved error page is not a POM: the adapter ignores it. `.jar` files
+  are optional too.
+- Core: `org/jenkins-ci/main/jenkins-war/<version>/jenkins-war-<version>.war` for every core version
+  you want to offer (its POM is optional too). Without them the site offers no core and has no tiers.
+  Every stored core that a plugin requires gets a tier (`CORE_AGE_DAYS=0`, the air-gapped default);
+  `CORE_AGE_DAYS=400` behaves like updates.jenkins.io, which drops cores released over 400 days ago.
+- An account that can read the repository: for Artifactory, one that can run AQL (a non-admin
+  account works); for Artifact Keeper, a user name and API token.
+
+A repository behind a private CA needs its bundle in the `repo-adapter` container:
 
-- One **local** repository in Maven layout: `<group path>/<artifactId>/<version>/<artifactId>-<version>.hpi`
-  plus the **real `.pom`** from `repo.jenkins-ci.org` at the same path. Plugins without a POM are
-  dropped.
-- Core: `org/jenkins-ci/main/jenkins-war/<version>/jenkins-war-<version>.war` and `.pom` for every core
-  version you want to offer. Without them the site offers no core and has no tiers.
-- An account that can run AQL on the repository.
+```yaml
+# compose.repo-ca.yaml
+services:
+  repo-adapter:
+    volumes:
+      - ./secrets/repo-ca.crt:/etc/ssl/uc-repo-ca.crt:ro
+    environment:
+      REPO_CA_FILE: /etc/ssl/uc-repo-ca.crt
+```
 
-`uc doctor --fetch` checks all of this and lists anything the generator will drop.
+`docker compose run --rm uc doctor --fetch` checks all of this. With the adapter on it also lists the
+POMs it filled in, `.pom` files it ignored and why, **misplaced** plugins with the path to move each
+to, and archives whose release date came from the repository instead of the archive.
 
 ### Keeping upstream data fresh
 
@@ -210,3 +262,8 @@ available for a proxy in front.
 | Plugin downloads fail with 404 | The version is not in the download map; check `uc doctor` and the Artifactory contents. Also check `status.json`'s `download_backend` against the current `DOWNLOAD_BACKEND`: if it changed since the live release was generated, the release's download map matches the old backend and every lookup misses - run `docker compose run --rm uc generate`. |
 | `/download/...` returns 502 with `DOWNLOAD_ACCESS=proxy` | The backend's TLS certificate does not verify against `DOWNLOAD_PROXY_CA_FILE` (section 7). |
 | web container keeps logging "waiting for the first published release" | No release is live yet; check `docker compose logs scheduler`. |
+| `repo-adapter token missing: /data/repo-adapter/token` | The repo-adapter has not started; check `docker compose logs repo-adapter`. |
+| `REPO_TYPE=artifact-keeper needs REPO_ADAPTER=on` | Only the adapter speaks to Artifact Keeper; remove `REPO_ADAPTER=off`. |
+| repo-adapter logs `listing failed: ... HTTP 401` | Wrong repository credentials. For Artifact Keeper, `repo_password` must be an API token. |
+| A plugin is missing and `uc doctor` lists it as misplaced | It is stored under another group than its manifest's `Group-Id`; move it to the path shown. |
+| `uc doctor`: "no POM in the repository or the archive" | A Gradle-built plugin: procure its real `.pom` from the URL shown and store it beside the `.hpi`. |
diff --git a/docs/spikes/README.md b/docs/spikes/README.md
index 84d42d4..669460c 100644
--- a/docs/spikes/README.md
+++ b/docs/spikes/README.md
@@ -12,6 +12,7 @@ half of S1b and S2 against the operator's own Artifactory. Still open: the downl
 | S2 | Does AQL work against a **virtual** repository? | Repeat S1b with `REPO_KEY=<virtual repo>`; compare `Plugins with a POM` with the local repository's count. | **Yes for AQL; count and downloads not yet checked** - 2026-09-22, same account: the hand probe against a virtual repository returned correct results. Still to run: the count comparison (a virtual can aggregate several repositories, remote caches included) and downloads through the virtual, both via `uc doctor --fetch`. |
 | S6 | Full-catalog first run: duration, heap, cache and site size. | `make it-full` (hours); copy `tests/integration/.results/online-*.json` here. If it fails with `OutOfMemoryError`, raise `-Xmx` in the `it-full` target and rerun. | |
 | S7 | Does GitHub GraphQL accept username + token over basic auth? | `export GITHUB_USERNAME=... GITHUB_TOKEN=...` (a token with no scopes), then `make it-online`; `test_github_labels_when_a_token_is_given` must pass rather than skip. | **Yes** - 2026-09-22, operator's account with a scopeless classic token. `make it-online` passed 7/7 with no skip. A direct `POST https://api.github.com/graphql` with `--user name:token` returned 200 and real `repositoryTopics`. |
+| S8 | Does the repo-adapter publish from a real Artifact Keeper? | `.env` from `.env.airgapped.example` with `REPO_TYPE=artifact-keeper`, `REPO_URL`, `REPO_KEY`, and `secrets/repo_username` / `secrets/repo_password` (an API token); `docker compose -f compose.yaml -f compose.airgapped.yaml up -d repo-adapter`, then `docker compose -f compose.yaml -f compose.airgapped.yaml run --rm uc doctor` and `... run --rm uc generate`. | |
 
 ## Notes from answered spikes
 
@@ -49,6 +50,11 @@ half of S1b and S2 against the operator's own Artifactory. Still open: the downl
   ~23.5 MB site, 5 stable and 5 weekly tiers. Not a substitute for S6, which measures the full
   catalogue.
 
+- **Embedded files (for the repo-adapter), 2026-09-22:** in a sample of 29 plugins from 2012 to 2026
+  plus core 2.583, the POM inside each archive was byte-identical to the published one in all but one
+  Gradle-built plugin, and every `index.jelly` matched. Each archive's `META-INF/MANIFEST.MF` time was
+  within 8 hours of the real release in 30 of 31. Details: repo-adapter spec §2.
+
 ## If an answer is "no"
 
 - **S1b no:** document the Artifactory permission the service account needs, then repeat S1b.
diff --git a/secrets/README.md b/secrets/README.md
index 4ee9a02..ee7b0e5 100644
--- a/secrets/README.md
+++ b/secrets/README.md
@@ -5,7 +5,7 @@ Everything here except this file is gitignored. Compose mounts each file under `
 | File | Required | Content |
 |---|---|---|
 | `update-center.key`, `update-center.cert`, `root-ca.crt` | yes | from `make pki-init` (move `root/root-ca.key` offline afterwards) |
-| `repo_username`, `repo_password` | yes | account that can run AQL on the source repository |
+| `repo_username`, `repo_password` | yes | Artifactory: an account that can run AQL on the repository; Artifact Keeper: a user name and an API token. With the repo-adapter on, only its container receives them. |
 | `github_username`, `github_token` | online, recommended | GitHub user and token; empty files mean "not set" |
 | `download_proxy_auth` | only with `DOWNLOAD_ACCESS=proxy` | `username:password` of a read-only Artifactory account |
 
````

- [ ] **Step 2: Amend both specs**

````diff
diff --git a/docs/superpowers/specs/2026-09-22-jenkins-update-center-design.md b/docs/superpowers/specs/2026-09-22-jenkins-update-center-design.md
index c272565..8cd792a 100644
--- a/docs/superpowers/specs/2026-09-22-jenkins-update-center-design.md
+++ b/docs/superpowers/specs/2026-09-22-jenkins-update-center-design.md
@@ -46,8 +46,11 @@ Everything below was checked against `update-center2` at `update-center2-3.18.4`
   `url` written in the signed JSON.
 - **The generator reads only small pieces of each artifact** when it builds metadata. It lists
   artifacts with an Artifactory AQL query (`ArtifactoryRepositoryImpl.java:56,166`), then fetches
-  `META-INF/MANIFEST.MF` and `index.jelly` out of each `.hpi` through Artifactory's archive-entry
-  URLs, plus the `.pom` and optionally the parent POM. It downloads whole files only when
+  each `.hpi`'s `META-INF/MANIFEST.MF` and each plugin `.jar`'s `index.jelly` (its description)
+  through Artifactory's archive-entry URLs, plus the `.pom` and optionally the parent POM. Only the
+  listing carries credentials; a `401` on a file read crashes the generator, so a repository it reads
+  directly must allow anonymous reads (repo-adapter spec §2, which removes that requirement).
+  Each artifact's timestamp is the listing's `modified`, an upload time in an operator's repository. It downloads whole files only when
   `--downloads-directory` is passed (`DirectoryTreeBuilder.java:151`). Upstream passes that flag
   to feed its mirror network (`site/publish.sh` rsyncs new releases to get.jenkins.io); this
   design does not.
@@ -147,7 +150,11 @@ These specifics are not:
 - **`web` image:** `httpd:2.4` with our config (§8). Mounts `/data` read-only and never sees the
   signing key.
 - **`scheduler`:** the `uc` image running `uc schedule`. Runs in both modes, because air-gapped
-  sites regenerate from the internal Artifactory.
+  sites regenerate from the internal repository.
+- **`repo-adapter`:** the `uc` image running `uc repo-adapter`, on an internal `repo` network with
+  `uc` and the scheduler. It answers the generator's Artifactory requests from Artifactory or
+  Artifact Keeper, and fills in POMs, descriptions and release dates from the archives. On by
+  default in air-gapped mode; see `2026-09-22-repo-adapter-design.md`.
 - **One volume:** all releases, the cache and `live` share a filesystem, so each publish is a
   single `rename(2)`.
 - **Release ids** are UTC timestamps, `YYYYMMDDTHHMMSSZ`.
@@ -161,6 +168,7 @@ These specifics are not:
 | `uc rollback [id]` | Point `live` at the previous release, or at `id`. |
 | `uc status` | Print `status.json`. |
 | `uc schedule` | Run `uc generate` once immediately if no release is live, then run `supercronic` with `GENERATE_SCHEDULE` → `uc generate`. |
+| `uc repo-adapter [--port 8081]` | The repo-adapter service (repo-adapter spec §3); idle when `REPO_ADAPTER` is off. |
 | `uc pki init \| rotate \| show` | Signing PKI (§9). |
 
 The CLI is Python 3, standard library only. All logic that isn't glue is unit-tested (§13).
@@ -280,11 +288,14 @@ requests) and caches them. Later runs fetch only what's new (spike S6 measures t
 
 | Setting | `online` default | `airgapped` default |
 |---|---|---|
-| `ARTIFACTORY_URL` | `https://repo.jenkins-ci.org/` | **required** |
-| `ARTIFACTORY_API_URL` | `${ARTIFACTORY_URL}api/` | same |
-| `ARTIFACTORY_REPOSITORY` | `releases` | **required** |
-| `DOWNLOAD_BACKEND` | `mirror` | `artifactory` |
-| `DOWNLOAD_BACKEND_URL` | `https://get.jenkins.io` | `${ARTIFACTORY_URL}${ARTIFACTORY_REPOSITORY}` |
+| `REPO_TYPE` | `artifactory` | `artifactory` (or `artifact-keeper`) |
+| `REPO_URL` | `https://repo.jenkins-ci.org/` | **required** |
+| `REPO_API_URL` | `${REPO_URL}api/` (Artifactory) | same |
+| `REPO_KEY` | `releases` | **required** |
+| `REPO_ADAPTER` | `off` | `on` (always on for Artifact Keeper) |
+| `CORE_AGE_DAYS` | `400` | `0` (every stored core a plugin requires gets a tier) |
+| `DOWNLOAD_BACKEND` | `mirror` | `artifactory` (any Maven-layout repository) |
+| `DOWNLOAD_BACKEND_URL` | `https://get.jenkins.io` | `${REPO_URL}${REPO_KEY}`; Artifact Keeper: `${REPO_URL}maven/${REPO_KEY}` |
 | `DOWNLOAD_ACCESS` | `redirect` | `redirect` |
 | `RESOURCES_SOURCE` | `github` | `bundled` (set `url` for procured copies) |
 | `POPULARITY_JSON_URL` | upstream's URL | empty (skip) |
@@ -311,13 +322,13 @@ private online Artifactory works by overriding the `ARTIFACTORY_*` settings and
 | Links to plugins.jenkins.io and the wiki | present in the JSON, dead in the air gap (cosmetic) |
 
 **Fast-fail for blocked hosts.** `compose.airgapped.yaml` adds `extra_hosts` entries mapping every
-blocked host to `127.0.0.1` for the `uc` and `scheduler` services. Connections are refused
+blocked host to `127.0.0.1` for the `uc`, `scheduler` and `repo-adapter` services. Connections are refused
 immediately instead of stalling on a firewall that drops packets, and each source's own error
 handling produces a warning. The container's normal resolver still handles every other name,
 including the internal Artifactory. (`-Djdk.net.hosts.file` was rejected: it replaces the JVM's
 resolver entirely, so every unlisted host, including Artifactory, would fail to resolve.)
 
-### 7.4 The one upstream patch
+### 7.4 The upstream patches
 
 `patches/0001-popularity-configurable-and-optional.patch`, against `Popularities.java`:
 
@@ -326,7 +337,11 @@ resolver entirely, so every unlisted host, including Artifactory, would fail to
 - An empty URL, or any `IOException`/`RuntimeException` while fetching, logs a warning and uses an
   empty map (popularity 0), matching how `HealthScores` behaves.
 
-This is submitted upstream as a PR. When a release contains it, the patch and the `git apply` step
+`patches/0002-core-age-configurable.patch`, against `json/TieredUpdateSitesGenerator.java`:
+`CORE_AGE_DAYS` becomes `Environment.getInteger("CORE_AGE_DAYS", 400)`, and `0` means no age limit
+(repo-adapter spec §12).
+
+Both are submitted upstream as PRs. When a release contains them, the patches and the `git apply` step
 are dropped and the jar can come from the released `-bin.zip` (pinned by SHA-256) instead of a
 source build. The pinned clone stays either way: the `-bin` assembly contains only jars, while
 `site/generate-htaccess.sh` and `resources/` exist only in the git tree.
@@ -442,7 +457,7 @@ Compose file-based secrets, mounted under `/run/secrets/`:
 |---|---|
 | `update-center.key`, `update-center.cert` | `uc` generate |
 | `root-ca.crt` | `uc` generate (not secret, but distributed alongside) |
-| `artifactory_username`, `artifactory_password` | `uc` generate and doctor |
+| `repo_username`, `repo_password` | `repo-adapter`; also `uc` generate and doctor when `REPO_ADAPTER` is off |
 | `github_username`, `github_token` | `uc` generate (optional) |
 | `download_proxy_auth` | `web` (mounted always; read only when `DOWNLOAD_ACCESS=proxy`) |
 
@@ -478,13 +493,15 @@ What the source Artifactory repository must contain:
 3. **Core:** `org/jenkins-ci/main/jenkins-war/<v>/jenkins-war-<v>.war` and `.pom` for each core
    version to offer. Without them the site offers no core and has no tiers.
 4. A **service account** that can run AQL against the repository (spike S1).
-   The generator sends the AQL query with credentials up front, but it answers file-download
-   challenges only when the port in `ARTIFACTORY_URL` equals the connection's port
-   (`ArtifactoryRepositoryImpl.java`, `AuthenticatorImpl`). `URI.getPort()` is -1 for a URL
-   without an explicit port, so `https://artifactory.corp/...` would never send credentials for
-   manifests and POMs. `uc` therefore always passes `ARTIFACTORY_URL` to the jar with an explicit
-   port (`:443` / `:80` added when missing). Redirects to `DOWNLOAD_BACKEND_URL` still use the URL
-   as configured.
+   The generator sends the AQL query with credentials up front but cannot authenticate file reads:
+   `AuthenticatorImpl` throws on the null requesting site Java's `HttpClient` passes (§2; found by
+   the repo-adapter's integration test, 2026-09-23). With the repo-adapter on, the air-gapped
+   default, this does not matter. With it off, the repository must allow anonymous reads of POMs
+   and archive entries. `uc` still passes `REPO_URL` to the jar with an explicit port (`:443` / `:80`
+   added when missing), which that authenticator's port check would need. Redirects to
+   `DOWNLOAD_BACKEND_URL` still use the URL as configured.
+   With the repo-adapter, items 2 and 3 relax: POMs are optional wherever the archive embeds one
+   (repo-adapter spec §4.2).
 5. Parent POMs are optional. Without them, SCM links for some plugins may be missing (a warning
    from the generator).
 
@@ -512,7 +529,9 @@ It exits non-zero when anything will be dropped. `uc generate` includes a summar
 | `GENERATE_SCHEDULE` | `0 */6 * * *` | cron, used by the scheduler |
 | `RESOURCES_SOURCE`, `RESOURCES_REF`, `RESOURCES_URL`, `RESOURCES_SHA256` | §6, §7.1 | |
 | `POPULARITY_JSON_URL`, `TOOL_INSTALLERS_URL` | §7.1 | |
-| `ARTIFACTORY_URL`, `ARTIFACTORY_API_URL`, `ARTIFACTORY_REPOSITORY` | §7.1 | passed to the jar |
+| `REPO_TYPE`, `REPO_URL`, `REPO_API_URL`, `REPO_KEY` | §7.1 | the repository; the jar gets the adapter's URL when it is on |
+| `REPO_ADAPTER`, `REPO_ADAPTER_URL`, `REPO_ADAPTER_DIR`, `REPO_CA_FILE`, `REPO_MAX_ARCHIVE_MB` | repo-adapter spec §6 | |
+| `CORE_AGE_DAYS` | §7.1 | passed to the jar (patch 0002) |
 | `DOWNLOAD_BACKEND`, `DOWNLOAD_BACKEND_URL`, `DOWNLOAD_ACCESS` | §7.1 | |
 | `DOWNLOAD_PROXY_CA_FILE`, `DOWNLOAD_PROXY_AUTH_FILE` | system CA bundle, `/run/secrets/download_proxy_auth` | `web`, only with `DOWNLOAD_ACCESS=proxy` |
 | `RETAIN_RELEASES` | `5` | |
@@ -530,15 +549,16 @@ Replaces the current `generator/`, `httpd/`, `docker-compose.yml`, `README.md` a
 
 ```
 Makefile                 build · test · it-online · it-airgapped · it-full · upstream-diff
-compose.yaml             web + scheduler; uc under profile "tools" for one-shot commands
-compose.airgapped.yaml   override: extra_hosts fast-fail entries for uc and scheduler
+compose.yaml             web + scheduler + repo-adapter; uc under profile "tools" for one-shot commands
+compose.airgapped.yaml   override: extra_hosts fast-fail entries; repository credentials for the adapter only
 .env.online.example
 .env.airgapped.example
 uc/
   Dockerfile             multi-stage: patched jar build → runtime
-  patches/0001-popularity-configurable-and-optional.patch
+  patches/0001-popularity-configurable-and-optional.patch, 0002-core-age-configurable.patch
   uc/                    Python package (cli, preflight, resources, htaccess, args, layout,
-                         downloadmap, validate, publish, status, doctor, pki, schedule)
+                         downloadmap, validate, publish, status, doctor, pki, pom, schedule)
+    repo_adapter/        archive, cache, backends, listing, server
   java/VersionTool.java
   templates/             index.html, htaccess-static
 web/
@@ -550,7 +570,7 @@ config/
 tests/
   unit/                  pytest
   routing/               fixture tree + generated .htaccess against the web image
-  integration/           compose files, fake Artifactory, seed script, Jenkins controllers
+  integration/           compose files, fake Artifactory and Artifact Keeper, seed script, Jenkins controllers
 docs/
   01-05 (ChatGPT, unchanged) · operations.md · superpowers/specs/
 secrets/README.md        everything else in secrets/ is gitignored
@@ -601,6 +621,8 @@ All tests run in containers through `make`, so no local Python or Java is needed
     routed to has `requiredCore` ≤ that controller's version. `jenkins-plugin-cli` installs the
     same plugin.
   - `uc rollback` restores the previous release.
+  - It runs once per repository type, through the repo-adapter, against the same seeded repository
+    served by a fake Artifactory and a fake Artifact Keeper (repo-adapter spec §9).
 - **Full catalog (`make it-full`, run by hand):** a complete online generation that records
   duration, heap use, cache size and site size.
 
@@ -630,6 +652,7 @@ operator checkpoint once those commands exist.
 | S5 | ~~Apache symlink swap~~ **Resolved during planning (2026-09-22):** a `live` swap and the `RewriteMap` behind it are picked up with no restart. Kept as a routing test. | - |
 | S6 | Full-catalog first-run duration, heap and cache size. | Tune `JAVA_TOOL_OPTIONS` defaults; document sizing. |
 | S7 | Does GitHub GraphQL accept username plus token over basic auth (upstream's method)? | Document the token type needed. |
+| S8 | Does the repo-adapter publish from the operator's real Artifact Keeper (repo-adapter spec §9)? | Fix the Artifact Keeper backend against what the real server returns. |
 
 Also verified during planning: the patched 3.18.4 build passes all 45 upstream tests.
 
diff --git a/docs/superpowers/specs/2026-09-22-repo-adapter-design.md b/docs/superpowers/specs/2026-09-22-repo-adapter-design.md
index fcd8400..d257de4 100644
--- a/docs/superpowers/specs/2026-09-22-repo-adapter-design.md
+++ b/docs/superpowers/specs/2026-09-22-repo-adapter-design.md
@@ -35,6 +35,11 @@ Upstream is update-center2 3.18.4 (`b2c8ad2b`); paths below are under `src/main/
   sent with preemptive basic auth), files at `<url><repo>/<path>` (`:55`, `:338`), and archive entries
   at `<url><repo>/<path>!/<entry>` (`:54`, `:327`), of which it reads `META-INF/MANIFEST.MF` of each
   `.hpi` (`:53`, `:248`) and `index.jelly` of each plugin's `.jar`.
+- **It cannot authenticate file reads.** Only the listing carries credentials (preemptive basic auth).
+  Files and archive entries are requested without them, and a `401` challenge crashes the run:
+  `AuthenticatorImpl` (`:343-367`) calls `getRequestingSite().getHostName()`, and Java's `HttpClient`
+  always passes a null requesting site. Anything upstream reads files from must therefore answer
+  unauthenticated reads. repo.jenkins-ci.org does; a private repository may not.
 - **Pairing happens in the listing.** A `.hpi`/`.jpi`/`.war` without a `.pom` entry of the same
   coordinates in the AQL result is discarded (`:179-183`). Serving a POM on request is not enough.
 - **Descriptions** come from the POM's `/project/description`, replaced by the sanitised text of
@@ -125,16 +130,20 @@ port matters: main spec §10 item 4.
 | Request | `REPO_TYPE=artifactory` | `REPO_TYPE=artifact-keeper` |
 |---|---|---|
 | `POST /artifactory/api/search/aql` | forwarded verbatim to `REPO_API_URL`, then gap-filled | pages through `/api/v1/repositories/<REPO_KEY>/artifacts`, returns `.hpi`/`.jpi`/`.war`/`.pom` (and records `.jar` entries in the index for §4.2 rule 6), then gap-filled |
-| `GET /artifactory/<REPO_KEY>/<path>` | forwarded | `GET /maven/<REPO_KEY>/<path>` |
+| `GET /artifactory/<REPO_KEY>/<path>`, POMs only | forwarded | `GET /maven/<REPO_KEY>/<path>` |
 | `GET /artifactory/<REPO_KEY>/<path>!/<entry>` | forwarded (Artifactory extracts) | served from the cache (§5.1) |
 | `GET /healthz` | 200 | 200 |
 
-Anything else returns 404. The AQL endpoint accepts only the query shape upstream and `uc` send (a
-`repo` `$eq` plus the four extension matches), and only for `REPO_KEY`; any other query gets 400.
+Anything else returns 404, including whole archives: upstream fetches whole files only for POMs
+(`resolve()`, for a plugin's POM and its parent), so the adapter never buffers an archive for a client.
+The AQL endpoint accepts only the exact query upstream and `uc` send (`uc/uc/aql.py`'s `AQL_TEMPLATE`
+for `REPO_KEY`); any other query gets 400.
 
-**The index.** Each listing leaves an in-memory index (path to sha256 and size, plus the derived
-files) that GETs are resolved against. A GET for a path the index does not know rebuilds the listing
-once before answering 404, so an adapter restart between upstream's listing and its GETs is harmless.
+**The index.** Each listing leaves an in-memory index (the rows, derived POMs, manifests and
+descriptions) that GETs are resolved against. A GET that arrives before any listing, as after an
+adapter restart, builds one first. Otherwise a path the index does not know is a 404: upstream asks
+for a parent POM for nearly every plugin, and most are absent, so rebuilding on a miss would mean a
+full listing per plugin.
 
 **Artifact Keeper listing fields**, mapped into Artifactory's result shape:
 
@@ -203,12 +212,14 @@ built, so the listing never advertises a file the adapter cannot serve.
 
 ### 5.1 Cache
 
-- **Layout.** `/data/repo-adapter/cache/<sha256[:2]>/<sha256>/`, keyed by the archive's sha256, holds
-  `MANIFEST.MF`, `pom.xml` or the marker `pom.none`, `index.jelly` or `index.none`, `sha1`, and
-  `released` or `released.none`. `released` holds rule 9's archive date before the plausibility check;
-  the check depends on the repository's time, so it runs on every listing.
-  Verdicts on the repository's own POMs are kept at `/data/repo-adapter/cache/poms/<sha256>`. Content
-  addressed by sha256 never changes, so entries never go stale.
+- **Layout.** `/data/repo-adapter/cache/<sha256[:2]>/<sha256>/` holds what is known about that
+  content. For an archive: `MANIFEST.MF`, `coordinates`, `pom.xml`, `index.jelly` and `released`, each
+  replaced by an empty marker (`MANIFEST.none`, `coordinates.none`, `pom.none`, `index.none`,
+  `released.none`) when absent. `released`, written last, marks the entry complete; it holds rule 9's
+  archive date before the plausibility check, which depends on the repository's time and so runs on
+  every listing. For any file whose listing has no sha1: `sha1`. For a repository `.pom`:
+  `pom-verdict` (empty when usable, else the reason). For a repository `.jar`: `jar-index.jelly` or
+  `jar-index.none`. Content addressed by sha256 never changes, so entries never go stale.
 - **Writes** go to a temporary file followed by `os.replace`, so concurrent requests are safe.
 - **Artifact Keeper.** An archive whose sha256 is not cached is streamed to a temporary file under
   `/data/repo-adapter/tmp/`, its entries are extracted in one pass, and the temporary file is deleted.
@@ -236,15 +247,21 @@ Archives are treated as untrusted input.
 
 ### 5.3 Security
 
-- **Credentials.** Only the adapter holds the repository credentials: `repo_username` and
-  `repo_password`, via the existing `*_FILE` convention. For Artifact Keeper the password is the API
-  token, sent as `Bearer` to `/api/v1` and as basic auth to `/maven`.
-- **The generator's credentials** are a random 32-byte token that the adapter creates on first start at
-  `/data/repo-adapter/token`, mode 0600, owned by the `uc` user. `uc` passes it to the jar as
-  `ARTIFACTORY_PASSWORD` with username `uc`. The web container runs as uid 33 and cannot read it. The
-  implementation verifies that the `uc` image's uid differs from 33.
-- **Unauthenticated requests** other than `/healthz` get `401` with `WWW-Authenticate: Basic`, which
-  upstream needs before it sends credentials for files.
+- **Credentials.** The adapter reads the repository credentials, `repo_username` and `repo_password`,
+  via the existing `*_FILE` convention. For Artifact Keeper the password is the API token, sent as
+  `Bearer` to `/api/v1` and as basic auth to `/maven`. With the adapter on, `uc` never reads them, and
+  `compose.airgapped.yaml` (where the adapter is on by default) does not mount them into `uc` or the
+  scheduler at all.
+- **The listing needs a token:** a random 32-byte token that the adapter creates on first start at
+  `/data/repo-adapter/token`, mode 0600, owned by the `uc` user (uid 10001). `uc` passes it to the jar
+  as `ARTIFACTORY_PASSWORD` with username `uc`, and the jar sends it preemptively with the listing. A
+  listing without it gets `401` with `WWW-Authenticate: Basic`. The web container runs as uid 33 and
+  cannot read the token.
+- **File reads need none**, because upstream cannot answer a challenge (§2). They serve only POMs,
+  manifests and descriptions, never archives, and only on the internal `repo` network, which the
+  internet-facing web container is not attached to.
+- **Redirects** from the repository to another host (Artifactory's object storage) are followed
+  without the `Authorization` header.
 - **TLS** to the repository is verified against the system CA bundle, or `REPO_CA_FILE` when set.
 - **Logging.** Credentials are never logged. URLs are logged without userinfo.
 - **Trust is unchanged.** The adapter never adds content from outside the repository. Every derived
@@ -265,6 +282,7 @@ no compatibility aliases.
 | `REPO_ADAPTER` | `off` | `on` | new; forced `on` for `artifact-keeper` |
 | `REPO_CA_FILE` | system bundle | system bundle | new |
 | `REPO_MAX_ARCHIVE_MB` | `512` | `512` | new |
+| `REPO_ADAPTER_URL`, `REPO_ADAPTER_DIR` | `http://repo-adapter:8081/artifactory/`, `/data/repo-adapter` | same | new: where uc finds the adapter and its state (tests and non-default layouts) |
 | `CORE_AGE_DAYS` | `400` | `0` (no age limit) | new (§12) |
 | secrets `repo_username`, `repo_password` | Jenkins community account | service account | `artifactory_username`, `artifactory_password` |
 
@@ -407,8 +425,9 @@ stops.
   - The unit tests cover validation and the per-mode defaults.
   - `make it-airgapped` seeds two real LTS cores, both released more than 400 days ago, that plugins
     declare as their minimum. Their dates come from the WARs themselves (§4.2 rule 9). It asserts that
-    the default `0` gives both a tier, and that the tier step run with `CORE_AGE_DAYS=400` keeps only
-    the newer of the two, as the floor.
+    the default `0` gives both a tier, and that a run with `CORE_AGE_DAYS=400` drops the older one:
+    upstream keeps at most one core older than 400 days per line, as the floor, and which one depends on
+    the other cores present.
 
 ## 13. Out of scope
 
````

- [ ] **Step 3: Check that no product-named setting survives outside the jar's own environment**

Run: `git grep -n -i 'artifactory_\(url\|repository\|api_url\|username\|password\)' -- ':!docs/superpowers/plans' ':!docs/0*'`
Expected: matches in these places only, all of them correct:
- upstream's own variable names in `uc/uc/jar.py`, `tests/unit/test_jar.py` and `tests/unit/test_generate.py`, which are the environment the jar receives;
- `PUBLIC_ARTIFACTORY_URL` in `uc/uc/config.py`, the address of repo.jenkins-ci.org;
- the `artifactory_url` parameter of `ArtifactSource` in `uc/uc/doctor.py`, which reads an Artifactory-dialect URL, the adapter's included;
- the spec text that names them.

- [ ] **Step 4: Run everything once more**

Run: `make test compose-check`
Expected: PASS: `275 passed` (unit), 9 `ok:` lines, `79 passed` (routing), `compose-check` exits 0.

- [ ] **Step 5: Commit**

```bash
git add docs/operations.md .env.airgapped.example .env.online.example secrets/README.md README.md docs/spikes/README.md \
  docs/superpowers/specs/2026-09-22-repo-adapter-design.md docs/superpowers/specs/2026-09-22-jenkins-update-center-design.md
git commit -m "Docs and spec amendments for the repo-adapter"
```

## After the plan: spike S8 on the operator's Artifact Keeper

Not part of the implementation. Once merged, the operator runs spike S8 (`docs/spikes/README.md`) against their real Artifact Keeper. Their `jenkins-prod-local` repository deliberately still holds active-directory's saved error page as its `.pom`. The expected outcome is caffeine-api's POM derived, the error page replaced, and both plugins published.

The operator's local `.env` needs `ARTIFACTORY_USERNAME`/`ARTIFACTORY_PASSWORD` renamed to `REPO_USERNAME`/`REPO_PASSWORD` for `make it-online`, and `secrets/artifactory_*` renamed to `secrets/repo_*` for the stack. The implementation does not touch either.
