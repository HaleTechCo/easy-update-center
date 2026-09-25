# Troubleshooting

Symptoms, causes and fixes, grouped by where the symptom shows up.

Start with `docker compose run --rm uc status`. `status.json` names only a failure's class (as
`<ErrorClass>: see the generator log`); the message quoted below is in the output of the run:

- a scheduled run: `docker compose logs --tail 200 scheduler`;
- a run you started with `docker compose run --rm uc generate`: that command's own output;
- air-gapped, repository errors (authentication, certificates, timeouts) also appear in
  `docker compose logs --tail 200 repo-adapter`.

Air-gapped, these commands assume `COMPOSE_FILE` is set in `.env`
([Deploy air-gapped](getting-started/airgapped.md#3-configure)).

## Generation

| Symptom | Cause and fix |
|---|---|
| `Repository credentials missing: provide /run/secrets/repo_username and /run/secrets/repo_password` | The credential files are empty or missing. Write them: [online](getting-started/online.md#3-add-credentials), [air-gapped](getting-started/airgapped.md#4-add-credentials). Air-gapped with `REPO_ADAPTER=off`, also add them back to the `secrets` lists in `compose.airgapped.yaml`. |
| The listing fails with HTTP 403 (online) | Anonymous or wrong credentials: repo.jenkins-ci.org needs a Jenkins community account ([Accounts](getting-started/requirements.md#accounts)). |
| The listing fails with HTTP 403 (Artifactory) | The account cannot read the repository. Give it read permission on the repository (no admin rights are needed), or use another account. |
| A stack trace from `GitHubSource`: `java.io.IOException: This endpoint requires you to be authenticated` | Not an error: the warning for a missing GitHub token. The run continues without labels from GitHub topics ([GitHub token](reference/secrets.md#github-token)). |
| `Permission denied` writing to `secrets/` (Linux) | `make pki-init` gave `secrets/` to the containers' user. Write with `sudo` ([File ownership on Linux](reference/secrets.md#file-ownership-on-linux)). |
| `make pki-init` starts building the image, and fails without internet | It rebuilds the image first. On a host that loaded pre-built images, run `make -o build-uc pki-init`. |
| `PUBLIC_URL must be a bare origin such as https://uc.example.com` | `PUBLIC_URL` has a path, query or fragment. Give the update center its own hostname ([A hostname for PUBLIC_URL](getting-started/requirements.md#a-hostname-for-public_url)). |
| `PUBLIC_URL points at localhost, which Jenkins controllers cannot reach` | Use a name controllers can resolve. `ALLOW_LOCAL_PUBLIC_URL=true` exists for tests only. |
| `uc: another generation holds /data/.lock` (exit code 75) | A generation or rollback is already running, often the scheduler's. Wait for it; `docker compose logs -f scheduler` shows it. |
| `could not obtain resources (...) and no last-good copy exists` | The first run could not fetch upstream data. Online: check access to api.github.com and codeload.github.com, or set `RESOURCES_SOURCE=bundled`. Air-gapped with `RESOURCES_SOURCE=url`: check `RESOURCES_URL` and `RESOURCES_SHA256`. |
| Warning `resources fetch failed (...); using last-good copy` | The run published with the previous data. Fix access as above; `status.json` shows the data's age. |
| `every overlay warning needs an 'id'`, or `duplicate warning id(s) in the overlay` | Fix `config/overlay/warnings.json` ([Add a security warning](how-to/block-plugins-and-add-warnings.md#add-a-security-warning)). |
| `plugin count N < 0.9 × M of the live release` | The source shrank by more than 10%. Check `uc doctor` and the repository; if the shrink is intended, run `uc generate --force` once ([Accept a smaller catalog](how-to/generate-and-roll-back.md#accept-a-smaller-catalog)). |
| `plugin count 0 is below MIN_PLUGINS=1` | The generator saw no plugins: wrong `REPO_KEY`, an empty repository, or every plugin dropped. Run `uc doctor --fetch`. |
| `download map has no entry for ...` | With `DOWNLOAD_BACKEND=artifactory`, a version the metadata offers is not in the repository listing. Run `uc doctor`; usually a file outside Maven layout. |
| `upstream generate-htaccess.sh changed` | Only after changing the pinned update-center2: its routing script no longer has the shape this project edits. See [Upgrading update-center2](development/upgrading-upstream.md). |
| The generator dies with `OutOfMemoryError` | Raise the heap: `JAVA_TOOL_OPTIONS=-Xmx6g` ([Host sizing](getting-started/requirements.md#host-sizing)). |
| A plugin is missing from the site | Run `uc doctor --fetch`. It lists plugins dropped for having no POM, being misplaced, skipped, or outside Maven layout, each with the fix. Also check `config/overlay/artifact-ignores.properties`. |

## Signing

| Symptom | Cause and fix |
|---|---|
| `missing signing material: /run/secrets/update-center.key (see 'uc pki init')` | Run `make pki-init`. On Linux, check the files belong to uid 10001 ([File ownership on Linux](reference/secrets.md#file-ownership-on-linux)). |
| `signing certificate expires in N days (minimum 30); run 'uc pki rotate'` | [Rotate the signing certificate](how-to/rotate-signing-certificate.md), then recreate the scheduler. |
| `... update-center.key does not match ... update-center.cert` | The key and certificate come from different runs of `uc pki`. Restore the matching pair, or rotate. |
| `... update-center.cert is not issued by ... root-ca.crt` | `root-ca.crt` was replaced without the signing certificate (or the reverse). Restore the matching files. |
| Generation still uses the old certificate after rotating | The scheduler was started before the rotation: `docker compose up -d --force-recreate scheduler`. |

## Downloads

| Symptom | Cause and fix |
|---|---|
| Plugin downloads return 404 | The version is not in the download map. Check `uc doctor` and the repository. Also compare `status.json`'s `download_backend` with the current `DOWNLOAD_BACKEND`: if the setting changed since the live release was generated, run `docker compose run --rm uc generate`. |
| Controllers get 401 or 403 from the repository | They follow the redirect to a repository that needs credentials. Use [proxy mode](how-to/proxy-downloads.md). |
| `/download/...` returns 502 with `DOWNLOAD_ACCESS=proxy` | The backend's certificate does not verify against `DOWNLOAD_PROXY_CA_FILE` ([Backend with a private CA](how-to/proxy-downloads.md#backend-with-a-private-ca)), or the backend is unreachable from the web container. |
| Download URLs point at an old hostname | `PUBLIC_URL` changed after the live release was generated: run `docker compose run --rm uc generate`. |

## Web container

| Symptom | Cause and fix |
|---|---|
| It keeps logging `waiting for the first published release in /data/live` | No release is live yet. Watch `docker compose logs -f scheduler`: the first run may still be going (online, it takes hours), or it failed. |
| It exits with `entrypoint: PUBLIC_URL must be set` or `MODE must be online or airgapped` | `.env` is missing or not the file Compose loads (`UC_ENV_FILE`). |
| It exits with `DOWNLOAD_ACCESS=proxy needs username:password in /run/secrets/download_proxy_auth` | Write `secrets/download_proxy_auth` as `username:password`; on Linux make it readable by uid 33. |
| It exits with `TLS_CERT_FILE and TLS_KEY_FILE must both be readable` | Set both, and mount both files readable by uid 33 ([Enable TLS](how-to/enable-tls.md)). |
| It exits with `set DOWNLOAD_BACKEND_URL, or REPO_URL and REPO_KEY` | `DOWNLOAD_BACKEND=artifactory` needs to know the repository. |
| Redirects name the wrong host or port | `PUBLIC_URL` is not the origin clients use. Redirects always use `PUBLIC_URL`. |

## repo-adapter

| Symptom | Cause and fix |
|---|---|
| `repo-adapter token missing: /data/repo-adapter/token` | The repo-adapter has not started. Check `docker compose logs repo-adapter`. |
| `REPO_TYPE=artifact-keeper needs REPO_ADAPTER=on` | Only the adapter can read Artifact Keeper. Remove `REPO_ADAPTER=off`. |
| Its log shows `listing failed: ... HTTP 401` | Wrong repository credentials. For Artifact Keeper, `repo_password` must be an API token, not the password. |
| Its log shows certificate verification errors | The repository uses a private CA: [Repository behind a private CA](getting-started/airgapped.md#repository-behind-a-private-ca). |
| `uc doctor` lists a plugin as misplaced | It is stored under another group than its manifest's `Group-Id`. Move it to the path shown. |
| `uc doctor`: "no POM in the repository or the archive" | A plugin built with Gradle. Procure its real `.pom` from the URL shown and store it beside the `.hpi`. |
| Release dates look wrong for some plugins | They fell back to the upload time; `uc doctor` lists them under "dates from the repository" with the reason ([Release dates](architecture/repo-adapter.md#release-dates)). |

## Jenkins controllers

| Symptom | Cause and fix |
|---|---|
| "signature verification failed" in Manage Jenkins | `root-ca.crt` is not in `$JENKINS_HOME/update-center-rootCAs/`, or the controller's clock is far off ([Trust the root CA](how-to/configure-jenkins.md#trust-the-root-ca)). |
| The update site shows no plugins, or an old list | Click **Check now** on the Plugins page. Check `status.json`'s `live_release` and `last_run`. |
| Plugins are offered that the controller cannot install | Their dependencies are missing from the repository. `uc doctor` lists them under dependency problems. |
| `jenkins-plugin-cli` downloads from the wrong host | `JENKINS_UC` has a path; set it to the bare origin ([jenkins-plugin-cli](how-to/configure-jenkins.md#jenkins-plugin-cli-and-the-jenkins-docker-image)). |
| The controller cannot reach the update center at all | From the controller's host, `curl https://jenkins-updates.example.com/uctest.json` (your `PUBLIC_URL`) must print `{}`. |
