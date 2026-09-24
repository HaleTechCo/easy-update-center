# Upgrade

How to move an installation to a newer revision of this project, and back.

Your settings (`.env`), secrets (`secrets/`), overlay (`config/overlay/`) and the data volume carry
over; only the images and the Compose files change. Read `CHANGELOG.md` for the revision you move to
first.

## Upgrade to a newer revision

```bash
git fetch
git log --oneline HEAD..origin/master     # what changes
git pull
make build
docker compose up -d                      # air-gapped: add -f compose.yaml -f compose.airgapped.yaml
docker compose run --rm uc generate       # publish with the new generator now, instead of at the next schedule
docker compose run --rm uc status
```

`docker compose up -d` recreates the services whose image or configuration changed. Check that
`last_run.result` is `success` and that `update_center2` names the version you expect.

Air-gapped hosts without internet access: build on a connected machine and move the images, as in
[Build the images](../getting-started/airgapped.md#2-build-the-images).

## Go back to the previous revision

```bash
git checkout <previous commit or tag>
make build
docker compose up -d
```

If the problem is the release the new revision published, not the software, roll the site back
too: `docker compose run --rm uc rollback`. See [Generate and roll back](generate-and-roll-back.md).
