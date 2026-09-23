# Spikes needing an operator (spec §15)

S3 was replaced by the fake Artifactory. S4 is covered by `make it-airgapped`. S5 was resolved
during planning. On 2026-09-22, S1a and S7 were answered against the public services, and the AQL
half of S1b and S2 against the operator's own Artifactory. Still open: the download half of S1b and S2
(`uc doctor --fetch`), and S6.

| # | Question | Procedure | Result (date, who, outcome) |
|---|---|---|---|
| S1a | Can a Jenkins community account run the generator's AQL query on repo.jenkins-ci.org? | `export REPO_USERNAME=… REPO_PASSWORD=…; make it-online`. The first test fails with `HTTP 403` if not. | **Yes** — 2026-09-22, operator's community account. `make it-online` passed (6 passed, 1 skipped; the skip is S7). Anonymous AQL returns 403, the same account authenticated returns 200. Online mode needs no private Artifactory mirror. |
| S1b | Can a **non-admin** service account run it on the internal Artifactory? | With `.env` from `.env.airgapped.example` and the service account in `secrets/`: `docker compose run --rm uc doctor --fetch`. An AQL error names the HTTP status. | **Yes for AQL; downloads not yet checked** — 2026-09-22, operator's own non-admin account (not yet a dedicated service account), local repository. The hand probe below returned 3 results with `repo`, `path` and `name` populated. Still to run: `uc doctor --fetch`, which also downloads POMs and reads files inside each `.hpi`. |
| S2 | Does AQL work against a **virtual** repository? | Repeat S1b with `REPO_KEY=<virtual repo>`; compare `Plugins with a POM` with the local repository's count. | **Yes for AQL; count and downloads not yet checked** — 2026-09-22, same account: the hand probe against a virtual repository returned correct results. Still to run: the count comparison (a virtual can aggregate several repositories, remote caches included) and downloads through the virtual, both via `uc doctor --fetch`. |
| S6 | Full-catalog first run: duration, heap, cache and site size. | `make it-full` (hours); copy `tests/integration/.results/online-*.json` here. If it fails with `OutOfMemoryError`, raise `-Xmx` in the `it-full` target and rerun. | |
| S7 | Does GitHub GraphQL accept username + token over basic auth? | `export GITHUB_USERNAME=… GITHUB_TOKEN=…` (a token with no scopes), then `make it-online`; `test_github_labels_when_a_token_is_given` must pass rather than skip. | **Yes** — 2026-09-22, operator's account with a scopeless classic token. `make it-online` passed 7/7 with no skip. A direct `POST https://api.github.com/graphql` with `--user name:token` returned 200 and real `repositoryTopics`. |

## Notes from answered spikes

- **S1a:** AQL needs authentication (anonymous is 403) but no special grant — it returns whatever the
  account can read, and everyone can read `releases`. Artifactory rejects any AQL query that does not
  `include` all of `repo`, `path` and `name`, with `HTTP 400 "For permissions reasons AQL items domain
  demands the following fields"`. `AQL_TEMPLATE` in `uc/uc/aql.py` already includes all three; keep it
  that way when editing the query.
- **S7:** a **classic token with no scopes** is enough; GitHub's GraphQL endpoint accepts it as the
  password in basic auth, which is what upstream's `GitHubSource` sends. Labels cost about 70 s on a
  25-plugin run and grow the site slightly (24,685,552 → 24,708,724 bytes) — that growth is the
  labels landing in the JSON.
- **S1b, S2:** a non-admin account needs only Read on the repository. AQL doesn't fail without it;
  it returns an empty `results` list, which the `MIN_PLUGINS` gate then refuses to publish. `uc`
  ignores the `repo` field in AQL results and downloads from the configured `REPO_KEY`,
  so a virtual repository that reports items under an underlying repository's name is harmless.
- **Probing AQL by hand.** Put the query in a file rather than typing it inline. cmd and Windows
  PowerShell 5.1 strip the inner double quotes before curl sees them, and Artifactory answers
  `Failed to parse query`. `aql.txt`, one line, straight quotes:

  ```
  items.find({"repo":"YOUR_REPO","name":{"$match":"*.hpi"}}).include("repo","path","name").limit(3)
  ```

  Then, from the same folder. This works in cmd, PowerShell 5.1 and 7; use `curl` on Linux and macOS.
  `-u` without `:password` makes curl prompt, so the password stays out of shell history.

  ```
  curl.exe -u YOUR_ACCOUNT -H "Content-Type: text/plain" --data-binary "@aql.txt" https://YOUR_ARTIFACTORY/artifactory/api/search/aql
  ```

  200 with results: AQL works. 200 with `[]`: no Read on that repository. 401: wrong credentials, or
  an SSO account that needs a token as its password. 403: API access blocked.
- Capped reference runs (25 plugins, `-Xmx2g`): 247 s without GitHub, 316 s with; 466 KB cache,
  ~23.5 MB site, 5 stable and 5 weekly tiers. Not a substitute for S6, which measures the full
  catalogue.

## If an answer is "no"

- **S1b no:** document the Artifactory permission the service account needs, then repeat S1b.
- **S2 no:** keep the documented contract (a single local repository).
- **S6:** set the `JAVA_TOOL_OPTIONS` guidance and volume sizing in docs/operations.md from the numbers.
