# Troubleshooting

Symptoms, causes and fixes, grouped by where the symptom shows up.

Start with `docker compose run --rm uc status`. `status.json` names only a failure's class (as
`<ErrorClass>: see the generator log`); the message quoted below is in the output of the run:

- a scheduled run: `docker compose logs --tail 200 scheduler`;
- a run you started with `docker compose run --rm uc generate`: that command's own output;
- air-gapped, repository errors (authentication, certificates, timeouts) also appear in
  `docker compose logs --tail 200 repo-adapter`.

Air-gapped, these commands assume `COMPOSE_FILE` is set in `.env` ([Compose](reference/configuration.md#compose)).

## Users and volumes

| Symptom | Cause and fix |
|---|---|
| `uc must not run as root`, `setup must not run as root` or `web must not run as root` | The container runs as uid 0. With Compose, set `UC_UID` and `UC_GID` in `.env` to your own user (`id -u`, `id -g`); with `docker run`, pass `--user $(id -u):$(id -g)`. |
| `UC_UID must not be 0: every container would run as root` (or `UC_GID`) | Set it to the output of `id -u` (`id -g` for `UC_GID`) in `.env`. If the volumes already hold data, give them to that user ([Compose](reference/configuration.md#compose)). |
| `/data is not writable by uid N` or `/data/repo-adapter is not writable by uid N` | `UC_UID` changed after the volumes were created. Set it back to the volumes' owner, or give both volumes to the new user ([Compose](reference/configuration.md#compose)). |

## Generation

| Symptom | Cause and fix |
|---|---|
| `Repository credentials missing: provide /run/secrets/repo_username and /run/secrets/repo_password` | The credential files are empty or missing. Write them: [online](getting-started/online.md#3-add-credentials), [air-gapped](getting-started/airgapped.md#4-add-credentials). Air-gapped with `REPO_ADAPTER=off`, also add them back to the `secrets` lists in `compose.airgapped.yaml`. |
| The listing fails with HTTP 403 (online) | Anonymous or wrong credentials: repo.jenkins-ci.org needs a Jenkins community account ([Accounts](getting-started/requirements.md#accounts)). |
| The listing fails with HTTP 403 (Artifactory) | The account cannot read the repository. Give it read permission on the repository (no admin rights are needed), or use another account. |
| A stack trace from `GitHubSource`: `java.io.IOException: This endpoint requires you to be authenticated` | Not an error: the warning for a missing GitHub token. The run continues without labels from GitHub topics ([GitHub token](reference/secrets.md#github-token)). |
| `Permission denied` writing to `secrets/` (Linux), or `secrets/ is not writable by uid N` | The containers run as another user than the owner of `secrets/`: set `UC_UID` and `UC_GID` in `.env` to your `id -u` and `id -g`, then run `docker compose run --rm setup secrets` ([File modes](reference/secrets.md#file-modes)). |
| A container logs `Permission denied` reading a file under `/run/secrets/` (Linux) | The file was replaced (copied over, or saved by an editor that writes a new file) and lost its mode, or it belongs to another user than `UC_UID`. Run `docker compose run --rm setup secrets`, then `docker compose up -d`. |
| `PUBLIC_URL must be a bare origin such as https://uc.example.com` | `PUBLIC_URL` has a path, query or fragment. Give the update center its own hostname ([A hostname for PUBLIC_URL](getting-started/requirements.md#a-hostname-for-public_url)). |
| `PUBLIC_URL points at localhost, which Jenkins controllers cannot reach` | Use a name controllers can resolve. `ALLOW_LOCAL_PUBLIC_URL=true` exists for tests only. |
| `uc: another generation holds /data/.lock` (exit code 75) | A generation or rollback is already running, often the scheduler's. Wait for it; `docker compose logs -f scheduler` shows it. |
| `could not obtain resources (...) and no last-good copy exists` | The first run could not fetch upstream data. Online: check access to api.github.com and codeload.github.com, or set `RESOURCES_SOURCE=bundled`. Air-gapped with `RESOURCES_SOURCE=url`: check `RESOURCES_URL` and `RESOURCES_SHA256`. |
| Behind an outbound proxy: `could not obtain resources`, or tool installers not mirrored, while the generator itself works | The proxy is set only for the JVM. Set `HTTPS_PROXY` (and `NO_PROXY=repo-adapter`) in `.env` too ([Read by bundled software](reference/configuration.md#read-by-bundled-software)). |
| Warning `resources fetch failed (...); using last-good copy` | The run published with the previous data. Fix access as above; `status.json` shows the data's age. |
| `config/overlay/warnings.json: ...` (`not valid JSON`, `not UTF-8`, `every overlay warning needs an 'id'`, `duplicate warning id(s) in the overlay: ...`) | Fix `config/overlay/warnings.json` ([Add a security warning](how-to/block-plugins-and-add-warnings.md#add-a-security-warning)). |
| `plugin count N < 0.9 × M of the live release` | The source shrank by more than 10%. Check `uc doctor` and the repository; if the shrink is intended, run `uc generate --force` once ([Accept a smaller catalog](how-to/generate-and-roll-back.md#accept-a-smaller-catalog)). |
| `the release has no plugins` | The generator saw no plugins: wrong `REPO_KEY`, an empty repository, or every plugin dropped. Run `uc doctor --fetch`. |
| `download map has no entry for ...` | With `DOWNLOAD_BACKEND=artifactory`, a version the metadata offers is not in the repository listing. Run `uc doctor`; usually a file outside Maven layout. |
| `upstream generate-htaccess.sh changed` | Only after changing the pinned update-center2: its routing script no longer has the shape this project edits. See [Upgrading update-center2](development/upgrading-upstream.md). |
| The generator dies with `OutOfMemoryError` | Raise the heap: `JAVA_TOOL_OPTIONS=-Xmx6g` ([Host sizing](getting-started/requirements.md#host-sizing)). |
| A plugin is missing from the site | Run `uc doctor --fetch`. It lists plugins dropped for having no POM, being misplaced, skipped, or outside Maven layout, each with the fix. Also check `config/overlay/artifact-ignores.properties`. |

## Signing

| Symptom | Cause and fix |
|---|---|
| `missing signing material: /run/secrets/update-center.key (see 'uc pki init')` | Run `docker compose run --rm setup`: it creates the signing keys when `secrets/update-center.key` is missing. On Linux, if the files exist, run `docker compose run --rm setup secrets` ([File modes](reference/secrets.md#file-modes)). |
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
| HTTP 403 `Artifact download is blocked by the repository's scan policy` | An Artifact Keeper scan policy on the repository blocks downloads, for example of artifacts not scanned yet. It blocks the POMs and archives the repo-adapter reads as well as the files controllers download, so generations drop plugins too. Change the policy so it does not block this repository's downloads. |
| Core downloads through Artifact Keeper stop partway | Its request timeout (`GLOBAL_REQUEST_TIMEOUT_SECS`, 120 seconds by default) also cuts downloads from its Maven path, which `DOWNLOAD_ACCESS=proxy` fetches from: raise it ([Large files](how-to/procure-missing-files.md#large-files)). |

## Web container

| Symptom | Cause and fix |
|---|---|
| It keeps logging `waiting for the first published release in /data/live` | No release is live yet. Watch `docker compose logs -f scheduler`: the first run may still be going (online, it takes hours), or it failed. |
| It exits with `entrypoint: PUBLIC_URL must be set` or `MODE must be online or airgapped` | Set `PUBLIC_URL`, and `MODE` to `online` or `airgapped`, in `.env` in the project directory, which Compose passes to web; then `docker compose up -d`. |
| It exits with `DOWNLOAD_ACCESS=proxy needs username:password in /run/secrets/download_proxy_auth` | Write `secrets/download_proxy_auth` as `username:password`, then run `docker compose run --rm setup secrets`. |
| It exits with `TLS_CERT_FILE and TLS_KEY_FILE must both be readable` | Set both, and mount both files readable by `UC_UID` ([Enable TLS](how-to/enable-tls.md)). |
| It exits with `DOWNLOAD_BACKEND=artifactory needs DOWNLOAD_BACKEND_URL` | Set `DOWNLOAD_BACKEND_URL` in `.env` to your repository by Maven path ([Downloads](reference/configuration.md#downloads)), then `docker compose up -d`. Air-gapped mode uses the artifactory backend unless `DOWNLOAD_BACKEND` says otherwise. |
| Redirects name the wrong host or port | `PUBLIC_URL` is not the origin clients use. Redirects always use `PUBLIC_URL`. |
| The browser gets an empty response (`NS_ERROR_NET_EMPTY_RESPONSE`; curl: `Empty reply from server`) while the container is healthy and its log shows no request | Docker Desktop's forwarding of the published port went stale, often after sleep or a network change. Run `docker compose up -d --force-recreate web`; if that does not help, restart Docker Desktop. |

## repo-adapter

| Symptom | Cause and fix |
|---|---|
| `repo-adapter token missing: /data/repo-adapter/token` | The repo-adapter has not started. Check `docker compose logs repo-adapter`. |
| `repo-adapter token refused: /data/repo-adapter/token is a symbolic link` (or `is not a regular file`, `is larger than 128 bytes`, `is not 64 lower-case hex digits`) | The token is not what the repo-adapter writes. Something other than the adapter may have written its volume: find out what, then remove the file and run `docker compose restart repo-adapter`, which writes a new one. |
| `REPO_TYPE=artifact-keeper needs REPO_ADAPTER=on` | Only the adapter can read Artifact Keeper. Remove `REPO_ADAPTER=off`. |
| Its log shows `listing failed: ... HTTP 401` | Wrong repository credentials. For Artifact Keeper, `repo_password` must be an API token, not the password. |
| Its log shows certificate verification errors | The repository uses a private CA: [Repository behind a private CA](getting-started/airgapped.md#repository-behind-a-private-ca). |
| `uc doctor` lists a plugin as misplaced | It is stored under another group than its manifest's `Group-Id`. Move it to the path shown. |
| `uc doctor`: "no POM in the repository or the archive" | A plugin built with Gradle. Procure its real `.pom` from the URL shown and store it beside the `.hpi`. |
| Release dates look wrong for some plugins | They fell back to the upload time; `uc doctor` lists them under "dates from the repository" with the reason ([Release dates](architecture/repo-adapter.md#release-dates)). |

## Procurement

The commands of [Procure missing POMs, dependencies and cores](how-to/procure-missing-files.md).

| Symptom | Cause and fix |
|---|---|
| `uc procure stage`: `nothing to stage: choose --poms, ...` (exit 2) | No POMs, dependencies or cores are selected: add `--poms`, `--dependencies` or `--war` ([Stage](how-to/procure-missing-files.md#2-stage)). |
| `uc procure`: `cannot write to /procurement: ... Permission denied` | The uc containers cannot write `./procurement`: set `UC_UID` and `UC_GID` in `.env` to the owner of `./procurement` (your `id -u` and `id -g`). |
| `uc procure record`: `REFUSED ... it belongs at <path>; --fix moves it there` | A file you added is not at the Maven path its manifest or POM declares. Move it there, or run `uc procure record --fix` ([Files you place by hand](how-to/procure-missing-files.md#files-you-place-by-hand)). |
| `uc procure record`: `REFUSED ... not a Maven path to a .hpi, .jpi, .war or .pom` | The file declares no coordinates to place it by, such as a plugin whose manifest names no group. Find its group and move it to its Maven path by hand. |
| Push lists files as `unlisted` and skips them | They were added by hand and not recorded: run `docker compose run --rm uc procure record`. |
| Push: `FAILED ... HTTP 503: Server overloaded, please retry: request timed out`, a broken pipe, a dropped connection or an HTTP 502, on large files | Artifact Keeper's request timeout, or a proxy's upload limit ([Large files](how-to/procure-missing-files.md#large-files)). |
| Push: `FAILED ... HTTP 409: Artifact version already exists and is immutable` after you deleted the file | Artifact Keeper keeps the deleted release as a tombstone: run its storage garbage collection ([Review](how-to/procure-missing-files.md#3-review)). |
| Push: `DIFFERENT ... Artifact Keeper stored other bytes here without recording them` | An earlier upload left other bytes at that path. Delete them from Artifact Keeper's storage on its server, then push again ([Large files](how-to/procure-missing-files.md#large-files)). |
| Push or clean: `ERROR ... the repository holds this path but publishes no checksum for it` | Artifactory holds the file without a `.sha1`: compare it with the staged file by hand. |
| `uc doctor`: `older required core(s) below <version> not listed` | Information: plugins require cores older than the oldest one stored. Stage them only for controllers that old; the next lines give the `--since` that does it and what storing them does to the floor ([Stage](how-to/procure-missing-files.md#2-stage)). |
| `the repository holds no Jenkins core, so "required" has no floor` | Add `--since <oldest version your controllers run>` to `uc procure stage --war required`, or set `DOCTOR_SINCE`. |

## Jenkins controllers

| Symptom | Cause and fix |
|---|---|
| "signature verification failed" in Manage Jenkins | `root-ca.crt` is not in `$JENKINS_HOME/update-center-rootCAs/`, or the controller's clock is far off ([Trust the root CA](how-to/configure-jenkins.md#trust-the-root-ca)). |
| The update site shows no plugins, or an old list | Click **Check now** on the Plugins page. Check `status.json`'s `live_release` and `last_run`. |
| Plugins are offered that the controller cannot install | Their dependencies are missing from the repository. `uc doctor` lists them under dependency problems. |
| `jenkins-plugin-cli` downloads from the wrong host | `JENKINS_UC` has a path; set it to the bare origin ([jenkins-plugin-cli](how-to/configure-jenkins.md#jenkins-plugin-cli-and-the-jenkins-docker-image)). |
| The controller cannot reach the update center at all | From the controller's host, `curl https://jenkins-updates.example.com/uctest.json` (your `PUBLIC_URL`) must print `{}`. |
| A controller in a container on the update center's machine gets `java.net.ConnectException` checking the update site | Inside the container, `localhost` is the container itself: set `PUBLIC_URL` to a name it can reach ([A hostname for PUBLIC_URL](getting-started/requirements.md#a-hostname-for-public_url)), run `docker compose up -d` and `docker compose run --rm uc generate`, and use that URL for the update site. |
