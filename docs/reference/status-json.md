# status.json reference

Every field of status.json, the public report of the last run and the live release.

## Where to read it

- `$PUBLIC_URL/status.json`, served by the web container with `Cache-Control: no-store`. It is
  public: it never holds credentials, and a failure appears only as its error class. `warnings`
  say what failed and can name plugin coordinates; a detail in parentheses, such as an error's
  text, shows as `(see the generator log)`, any other URL as `<url>`, and the generator log
  (`docker compose logs scheduler`) has the full text.
- `docker compose run --rm uc status` prints the same document.
- On the data volume it is `/data/status.json`.

Each successful `uc generate` rewrites the document. A failed one changes only `mode`, `last_run`
and `warnings`, so the rest keeps describing the live release, and `uc rollback` updates
`live_release` and `plugin_count`. Before the first run the document is empty (`{}`). A generation
keeps what only `uc doctor --record` works out (`doctor.missing_cores`, `doctor.cores_unchecked`,
`doctor.recorded_at`), and the whole `doctor` block when it computes none.

## Fields

| Field | Type | Meaning |
|---|---|---|
| `mode` | string | `online` or `airgapped`. |
| `public_url` | string | `PUBLIC_URL` when the live release was generated. |
| `download_backend` | string | `DOWNLOAD_BACKEND` when the live release was generated. If it differs from the current setting, regenerate: the release's download map belongs to the old backend. |
| `live_release` | string | Id of the release being served (`YYYYMMDDTHHMMSSZ`). |
| `last_run` | object | The most recent `uc generate`, successful or not. |
| `last_run.id` | string | Its release id. |
| `last_run.result` | string | `success` or `failed`. |
| `last_run.error` | string or null | On failure, the error's class only, as `<ErrorClass>: see the generator log`. Where the message is: [Troubleshooting](../troubleshooting.md). |
| `last_run.finished_at` | string | When it finished (ISO 8601, UTC). |
| `generated_at` | string | When the live release's generation started (ISO 8601, UTC). |
| `update_center2` | string | The generator version, for example `3.18.4+patches`. |
| `uc_version` | string | The release of the uc image, for example `0.1.0`; `dev` for an image built from source. |
| `resources` | object | The upstream data (security warnings, suspensions, labels) the live release was built with. |
| `resources.source` | string | `github`, `url` or `bundled`: where this copy came from. When a fetch fails, the run uses the last good copy (and says so in `warnings`), so this field names that copy's source. |
| `resources.ref` | string | The upstream commit, or the tarball's checksum. |
| `resources.data_date` | string | When upstream last changed that data (ISO 8601, UTC). |
| `resources.fetched_at` | string | When this copy was fetched. |
| `resources.age_days` | integer | Days between `data_date` and the end of the run. |
| `resources.stale` | boolean | `true` when `age_days` is over `STALE_AFTER_DAYS`. |
| `resources.overlay` | list of strings | The files from `config/overlay/` that were applied. |
| `signing_cert` | object | The certificate that signed the live release. |
| `signing_cert.subject` | string | Its subject. |
| `signing_cert.not_after` | string | Its expiry (ISO 8601, UTC). |
| `signing_cert.days_left` | integer | Days until expiry at the end of the run. Generation stops within `CERTIFICATE_MINIMUM_VALID_DAYS`. |
| `tiers` | object | The tiered sites: `weekly` and `stable`, lists of core versions. Both empty when the repository holds no core. |
| `plugin_count` | integer | Plugins offered by the live release's `current/` site. |
| `warnings` | list of strings | What the last run could not do but published anyway, for example "GitHub credentials not set: plugin labels from GitHub topics are omitted" or dropped artifacts. Review them. |
| `doctor` | object or null | Counts from the same checks as `uc doctor`: from each generation with `DOWNLOAD_BACKEND=artifactory` (the air-gapped default), and from every `uc doctor --record` (scheduled with `DOCTOR_SCHEDULE`) whatever the backend. A generation that computes none keeps the last recorded counts. `null` until either has run. |
| `doctor.plugins` | integer | Plugins with a POM in the repository listing. |
| `doctor.cores` | list of strings | Core versions with a POM. |
| `doctor.hpi_without_pom`, `doctor.war_without_pom`, `doctor.non_maven`, `doctor.stub_poms`, `doctor.missing_dependencies` | integer | How many of each problem `uc doctor` would list. |
| `doctor.unchecked` | integer | Items whose POM or manifest was not in the cache, so were not checked. |
| `doctor.missing_cores` | integer or null | Cores plugin releases require that the repository lacks, at or above the floor, from the last `uc doctor --record`; generations keep it. `null` until a recorded report has checked cores, which it never does for the public repository. |
| `doctor.cores_unchecked` | integer or null | Plugin releases whose manifest was unavailable for the core check, from the last `uc doctor --record`; generations keep it. Above 0, `missing_cores` is incomplete. `null` until a recorded report has checked cores. |
| `doctor.checked_at` | string | When the counts were last taken, by a generation or a recorded report (ISO 8601, UTC). `missing_cores` can be older: see `recorded_at`. |
| `doctor.recorded_at` | string | When `uc doctor --record` last saved a report (ISO 8601, UTC); generations keep it. Absent until one has. With `DOCTOR_SCHEDULE` set, an old value means the scheduled report stopped ([Monitor](../how-to/monitor.md)). |
| `doctor.repo_adapter` | object or null | With the repo-adapter on, counts of `derived_poms`, `replaced_poms`, `needs_pom`, `misplaced`, `skipped`, `dates_from_repository` (see [Report](../architecture/repo-adapter.md#report)); `null` when it is off. |

What to alert on: [Monitor](../how-to/monitor.md).

## Example

An air-gapped deployment after a successful run, with the doctor report scheduled at 05:30
(`DOCTOR_SCHEDULE=30 5 * * *`):

```json
{
  "doctor": {
    "checked_at": "2026-09-24T06:03:29Z",
    "cores": ["2.528.1", "2.528.3"],
    "cores_unchecked": 0,
    "hpi_without_pom": 0,
    "missing_cores": 0,
    "missing_dependencies": 1,
    "non_maven": 0,
    "plugins": 212,
    "recorded_at": "2026-09-24T05:31:07Z",
    "repo_adapter": {
      "dates_from_repository": 2,
      "derived_poms": 37,
      "misplaced": 0,
      "needs_pom": 1,
      "replaced_poms": 3,
      "skipped": 0
    },
    "stub_poms": 0,
    "unchecked": 0,
    "war_without_pom": 0
  },
  "download_backend": "artifactory",
  "generated_at": "2026-09-24T06:00:02Z",
  "last_run": {
    "error": null,
    "finished_at": "2026-09-24T06:03:41Z",
    "id": "20260924T060002Z",
    "result": "success"
  },
  "live_release": "20260924T060002Z",
  "mode": "airgapped",
  "plugin_count": 211,
  "public_url": "https://jenkins-updates.corp.example",
  "resources": {
    "age_days": 22,
    "data_date": "2026-09-02T08:35:03Z",
    "fetched_at": "2026-09-24T06:00:03Z",
    "overlay": ["artifact-ignores.properties"],
    "ref": "b2c8ad2b2feb425b471e6dcc9174c50dc3b3c1dd",
    "source": "bundled",
    "stale": false
  },
  "signing_cert": {
    "days_left": 157,
    "not_after": "2027-02-28T10:00:00Z",
    "subject": "CN=Easy Update Center Signing,O=Easy Update Center"
  },
  "tiers": {"stable": ["2.528.1", "2.528.3"], "weekly": []},
  "update_center2": "3.18.4+patches",
  "uc_version": "0.1.0",
  "warnings": [
    "1 artifact(s) in the source are dropped by the generator; run 'uc doctor'"
  ]
}
```
