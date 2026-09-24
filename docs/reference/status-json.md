# status.json reference

Every field of status.json, the public report of the last run and the live release.

## Where to read it

- `$PUBLIC_URL/status.json`, served by the web container with `Cache-Control: no-store`. It is
  public: it holds counts and error class names, never paths, URLs or messages from the repository.
- `docker compose run --rm uc status` prints the same document.
- On the data volume it is `/data/status.json`.

A successful `uc generate` rewrites the whole document. A failed one changes only `mode`, `last_run`
and `warnings`, so the other fields keep describing the live release. `uc rollback` updates
`live_release` and `plugin_count`. Before the first run the document is empty (`{}`).

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
| `last_run.error` | string or null | On failure, the error's class only, as `<ErrorClass>: see the generator log`. The message is in the container log. |
| `last_run.finished_at` | string | When it finished (ISO 8601, UTC). |
| `generated_at` | string | When the live release's generation started (ISO 8601, UTC). |
| `update_center2` | string | The generator version, for example `3.18.4+patches`. |
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
| `warnings` | list of strings | What the last run could not do but published anyway, for example "GitHub unavailable" or dropped artifacts. Review them. |
| `doctor` | object or null | With `DOWNLOAD_BACKEND=artifactory` (the air-gapped default), counts from the same checks as `uc doctor`; `null` otherwise. |
| `doctor.plugins` | integer | Plugins with a POM in the repository listing. |
| `doctor.cores` | list of strings | Core versions with a POM. |
| `doctor.hpi_without_pom`, `doctor.war_without_pom`, `doctor.non_maven`, `doctor.stub_poms`, `doctor.missing_dependencies` | integer | How many of each problem `uc doctor` would list. |
| `doctor.unchecked` | integer | Items whose POM or manifest was not in the cache, so were not checked. |
| `doctor.repo_adapter` | object or null | With the repo-adapter on, counts of `derived_poms`, `replaced_poms`, `needs_pom`, `misplaced`, `skipped`, `dates_from_repository` (see [Report](../architecture/repo-adapter.md#report)); `null` when it is off. |

What to alert on: [Monitor](../how-to/monitor.md).

## Example

An air-gapped deployment after a successful run:

```json
{
  "doctor": {
    "cores": ["2.528.1", "2.528.3"],
    "hpi_without_pom": 0,
    "missing_dependencies": 1,
    "non_maven": 0,
    "plugins": 212,
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
    "age_days": 12,
    "data_date": "2026-09-12T09:14:05Z",
    "fetched_at": "2026-09-24T06:00:03Z",
    "overlay": ["artifact-ignores.properties"],
    "ref": "b2c8ad2b2feb425b471e6dcc9174c50dc3b3c1dd",
    "source": "bundled",
    "stale": false
  },
  "signing_cert": {
    "days_left": 301,
    "not_after": "2027-07-22T10:00:00Z",
    "subject": "CN=Jenkins Update Center Signing,O=Jenkins Update Center"
  },
  "tiers": {"stable": ["2.528.1", "2.528.3"], "weekly": []},
  "update_center2": "3.18.4+patches",
  "warnings": [
    "1 artifact(s) in the source are dropped by the generator; run 'uc doctor'"
  ]
}
```
