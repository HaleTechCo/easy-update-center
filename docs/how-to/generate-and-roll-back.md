# Generate and roll back

Everyday commands: publish a release now, accept a deliberate shrink, go back to an earlier release, read the logs.

Air-gapped, these commands assume `COMPOSE_FILE` is set in `.env`, as in
[Deploy air-gapped](../getting-started/airgapped.md#3-configure); otherwise add
`-f compose.yaml -f compose.airgapped.yaml` to each.

## Generate now

The scheduler generates on `GENERATE_SCHEDULE` (every six hours by default). To publish at once,
for example after procuring plugins or changing the overlay:

```bash
docker compose run --rm uc generate
```

It prints the generator's progress and ends with `published release <id>`. If it fails, the live
release is unchanged and the last lines say why; see [Troubleshooting](../troubleshooting.md#generation).
A second run while one is in progress exits with code 75.

## Change the schedule

Set `GENERATE_SCHEDULE` (a cron expression, or an `@macro` such as `@daily`) in `.env`, then
recreate the services so the scheduler reads it:

```bash
docker compose up -d
```

The scheduler can also run a read-only `uc doctor` report on its own schedule, `DOCTOR_SCHEDULE`
(for example `30 5 * * *`): it saves the report and puts its counts in `status.json`, so monitoring
sees missing cores or dropped plugins between generations ([Monitor](monitor.md#what-to-alert-on)).
It never stages, pushes or cleans.

## Accept a smaller catalog

A release offering fewer than `MIN_PLUGIN_RATIO` (90%) of the live release's plugins is refused, so
a half-empty listing never reaches controllers. When the shrink is deliberate, for example after
removing plugins from the repository, run once with `--force`:

```bash
docker compose run --rm uc generate --force
```

Check what changed first with `uc doctor`.

## Roll back

To go back to the release before the live one:

```bash
docker compose run --rm uc rollback
```

To pick a release, list them and pass its id:

```bash
docker compose run --rm --entrypoint ls uc /data/releases
docker compose run --rm uc rollback 20260922T030000Z
```

The web container serves the chosen release at once. The next scheduled generation publishes a new
release as usual; to stay on the old one, fix the cause first (or stop the scheduler with
`docker compose stop scheduler`).

## Retained releases

The newest `RETAIN_RELEASES` (5) releases stay under `/data/releases/`; older ones are deleted after
each successful generation, never the live one. The last failed attempt is kept as
`/data/releases/.failed-<id>`, for inspection.

## Logs

```bash
docker compose logs -f scheduler      # scheduled runs, with the generator's full output
docker compose logs -f web            # access log and web errors
docker compose logs -f repo-adapter   # repository access (air-gapped)
```

`status.json` names only the class of a failure (`last_run.error`); the full message is in the
scheduler's log, or on your terminal for `docker compose run --rm uc generate`.
