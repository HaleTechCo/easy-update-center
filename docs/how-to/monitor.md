# Monitor

What to watch and alert on so a stale or failing update center is noticed before controllers are affected.

A failing update center does not break Jenkins at once: controllers keep the last release they
loaded. It shows up days later as missing security warnings or plugins that do not update, so
watch it actively.

## Health checks

| Check | What it tells you |
|---|---|
| `GET $PUBLIC_URL/uctest.json` returns 200 | The web container is up and a release is live. Use it for external probes; Jenkins itself uses it as its connection check. |
| The web container's Docker health status | Runs the same check from inside, plus that `/data/live` exists. |
| The repo-adapter's Docker health status | Its `/healthz` answers. The `scheduler` and `uc` services wait for it. |
| `GET $PUBLIC_URL/status.json` | Everything below. It is sent with `Cache-Control: no-store`. |

## What to alert on

From [status.json](../reference/status-json.md):

| Condition | Why | What to do |
|---|---|---|
| `last_run.result` is `failed` | Generation stopped; controllers get an ageing release. | Read the scheduler log (air-gapped, the repo-adapter's too); see [Troubleshooting](../troubleshooting.md#generation). |
| `last_run.finished_at` older than twice the schedule (12 hours by default) | The scheduler is not running, or runs are hanging. | `docker compose ps`, `docker compose logs scheduler`. |
| `signing_cert.days_left` below 45 | At 30, generation stops. | [Rotate the signing certificate](rotate-signing-certificate.md). |
| `resources.stale` is `true` | Security warnings are older than `STALE_AFTER_DAYS`. | Online: check GitHub access. Air-gapped: [procure a fresh copy](keep-upstream-data-fresh.md). |
| `warnings` not empty | The run published, but dropped or degraded something. | Review each line; `uc doctor` explains repository drops. |
| `doctor.missing_dependencies` above 0 | Some offered plugins cannot be installed. | Procure the missing plugins. |
| `doctor.missing_cores` above 0 | Plugins require cores the repository lacks, so those tiers are missing. | [Procure missing files](procure-missing-files.md). |
| `doctor.checked_at` older than twice your `DOCTOR_SCHEDULE` interval | The scheduled report stopped (only when `DOCTOR_SCHEDULE` is set). | `docker compose logs scheduler`. |

The `doctor` counts come from each generation with `DOWNLOAD_BACKEND=artifactory`, and from the
scheduled report when `DOCTOR_SCHEDULE` is set
([Change the schedule](generate-and-roll-back.md#change-the-schedule)).

A small script covering the first four is enough, for example (with your own `PUBLIC_URL`):

```bash
PUBLIC_URL=https://jenkins-updates.example.com
curl -fsS "$PUBLIC_URL/status.json" | jq -e '
  .last_run.result == "success"
  and (now - (.last_run.finished_at | fromdateiso8601)) < 43200
  and .signing_cert.days_left >= 45
  and (.resources.stale | not)'
```

`jq -e` exits non-zero when the expression is false, so this works in cron or a monitoring agent.
`43200` seconds is twice the default six-hour schedule; adjust it to yours.

Air-gapped on the image's bundled security data, `resources.stale` turns `true` 30 days after that
data's date (`resources.data_date`), whatever the image's build date:
[procure fresh copies](keep-upstream-data-fresh.md) rather than silencing the check.

## Logs

All containers log to standard output:

```bash
docker compose logs -f scheduler web repo-adapter
```

The web container writes a combined-format access log; Jenkins' requests carry its version in the
user agent, which shows which controller versions use the update center.
