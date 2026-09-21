# Starting the stack

## First build:

```shell
docker compose build
```

## Then generate:

```shell
docker compose run --rm generator
```

## Then start Apache:

```shell
docker compose up -d httpd
```

## You should then have:

```
http://localhost:8080/update-center.json
http://localhost:8080/update-center.json?version=2.555.3
http://localhost:8080/current/update-center.json
http://localhost:8080/current/plugin-versions.json
http://localhost:8080/current/release-history.json
http://localhost:8080/current/plugin-documentation-urls.json
http://localhost:8080/current/latestCore.txt
```

The important bit is that update-center.json?version=... gets routed to a tiered site. That's the same basic mechanism used by updates.jenkins.io.

# Getting the certs to your Jenkins instance

## On the Jenkins controller:

```
$JENKINS_HOME/
└── update-center-rootCAs/
    └── update-center.crt
```

## Then configure the update site as:

`https://jenkins-update-center.example.com/update-center.json`

The Jenkins documentation for the generator specifically describes putting the signing certificate into update-center-rootCAs/. GGitHub

After restarting Jenkins, it should be able to validate your signed update-center metadata.


# Docker-based Jenkins controllers

## There's another useful part here.

## The Jenkins Docker image's plugin CLI recognizes:

```
JENKINS_UC
JENKINS_UC_DOWNLOAD
JENKINS_PLUGIN_INFO
JENKINS_UC_EXPERIMENTAL
```

## and the default values point at updates.jenkins.io. GGitHub

## So for a Jenkins Docker controller, you'd want:

```
environment:
  JENKINS_UC: https://jenkins-update-center.example.com
  JENKINS_UC_DOWNLOAD: https://jenkins-update-center.example.com/download
  JENKINS_PLUGIN_INFO: https://jenkins-update-center.example.com/current/plugin-versions.json
  JENKINS_UC_EXPERIMENTAL: https://jenkins-update-center.example.com/experimental
```

This is especially useful because it makes both Jenkins runtime and jenkins-plugin-cli use your infrastructure.
