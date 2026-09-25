## What happened

<!-- What you did, what you expected, what happened instead. -->

## Environment

- Commit (`git rev-parse --short HEAD`):
- Mode (`online` or `airgapped`):
- Repository type (`artifactory` or `artifact-keeper`), if air-gapped:
- Docker and Compose versions (`docker version --format '{{.Server.Version}}'`, `docker compose version`):

## Evidence

<!-- Remove credentials, tokens and internal hostnames before pasting. -->

`status.json` (`last_run`, `warnings`, `signing_cert.days_left`):

~~~json
~~~

`uc doctor` output, if relevant:

~~~text
~~~

Logs (`docker compose logs --tail 200 scheduler web repo-adapter`):

~~~text
~~~

/label ~bug
