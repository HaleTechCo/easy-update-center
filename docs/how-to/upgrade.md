# Upgrade

How to move an installation to a newer revision of this project, and back.

Your settings (`.env` and `config/uc.toml`), secrets (`secrets/`), overlay (`config/overlay/`) and
the data volume carry over; only the images and the Compose files change. Read `CHANGELOG.md` for
the revision you move to first.

## Upgrade to a newer revision

```bash
git fetch
git log --oneline HEAD..origin/master                # what changes
git pull
docker compose build                                 # or: make build
docker compose run --rm --no-deps uc config --check  # your settings, as the new revision reads them
docker compose up -d                                 # air-gapped: COMPOSE_FILE in .env selects the override
docker compose run --rm uc generate                  # publish with the new generator now, instead of at the next schedule
docker compose run --rm uc status
```

Check the settings before `docker compose up -d`: a newer revision can refuse a value an older one
loaded, and a service whose settings do not load exits with an error instead of running.
`uc config --check` prints `configuration ok`, or `uc: error: ...` naming the setting to fix in
`.env` or `config/uc.toml`. Revisions since `config/uc.toml` was added refuse any `@` in `PUBLIC_URL`,
`REPO_URL`, `DOWNLOAD_BACKEND_URL` and `PROCUREMENT_SOURCE_URL`, and an `HTTPS_PROXY` or
`HTTP_PROXY` whose host and port urllib cannot use (such as `http://proxy:3128?x`, or a port that is
not a number); see `CHANGELOG.md`. Instead of `docker compose up -d` you can run `make reload`,
which checks the settings first and recreates nothing when they are invalid.

`docker compose up -d` recreates the services whose image or configuration changed. Check that
`last_run.result` is `success` and that `update_center2` names the version you expect.

Air-gapped hosts without internet access cannot `git pull`: the clone came from a bundle, so its
`origin` never changes. On a connected machine, update your clone, run `docker compose build` (or
`make build`), and create a new image archive and bundle as in
[Build the images](../getting-started/airgapped.md#2-build-the-images).
Then, on the air-gapped host:

```bash
git pull ../jenkins-uc.bundle master            # the Compose files and docs change too
gunzip -c ../jenkins-uc-images.tar.gz | docker load
docker compose run --rm --no-deps uc config --check
docker compose up -d
docker compose run --rm uc generate
```

On Linux, if `git pull` cannot update a file under `secrets/` (such as its README), an older
`make pki-init` gave `secrets/` to uid 10001: run `docker compose run --rm setup secrets`
(`make secrets`) first.

## Go back to the previous revision

Revisions before `config/uc.toml` existed ignore it: move the settings it holds back into `.env`
first, or the services start without them.

```bash
git checkout <previous commit or tag>
docker compose build      # or: make build
docker compose up -d
```

A revision from before `docker compose build` could build the images (see `CHANGELOG.md`) has no
`build:` sections, so there `docker compose build` only warns `No services to build` and
`docker compose up -d` would run the newer images: use `make build`.

If the problem is the release the new revision published, not the software, roll the site back
too: `docker compose run --rm uc rollback`. See [Generate and roll back](generate-and-roll-back.md).
