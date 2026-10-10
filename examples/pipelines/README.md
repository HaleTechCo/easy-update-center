# Example pipeline

`Jenkinsfile` runs procurement with an approval before anything is pushed: check, stage, review,
push, generate, verify, clean. Copy it into your own pipeline repository; nothing in this repository
runs it. It runs on a Jenkins agent labelled `uc-host` on the update center's Docker host, in the
deployment directory `UC_DIR` (a parameter, default `/opt/easy-update-center`).

How to set it up, the approval and what each stage does:
[Run procurement from a pipeline](../../docs/how-to/run-procurement-from-a-pipeline.md).
