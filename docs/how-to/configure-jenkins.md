# Configure Jenkins controllers

How to make Jenkins controllers and jenkins-plugin-cli trust and use your update center.

Do this on every controller, after the first release is live
(`docker compose run --rm uc status` shows a `live_release`).

## Trust the root CA

Jenkins accepts update center metadata only when its signature chains to a certificate in
`$JENKINS_HOME/update-center-rootCAs/`. Copy the root CA certificate there:

```bash
mkdir -p "$JENKINS_HOME/update-center-rootCAs"
cp secrets/root-ca.crt "$JENKINS_HOME/update-center-rootCAs/root-ca.crt"
```

The file name does not matter; every certificate in the directory is trusted. For a controller in a
container, mount the file into that directory, or bake it into your controller image. Only
`root-ca.crt` goes to controllers, never a key.

`root-ca.crt` only vouches for the metadata's signatures. It has nothing to do with HTTPS: if
`PUBLIC_URL` is `https://` with a certificate from a private CA, the controller's Java runtime must
trust that CA as well, as it must for any internal HTTPS service. The same goes for the repository
when controllers download from it directly (`DOWNLOAD_ACCESS=redirect`), and for
`jenkins-plugin-cli`, which runs on the same Java runtime. For a controller built from the official
image, for example:

```dockerfile
FROM jenkins/jenkins:lts
USER root
COPY corp-ca.crt /usr/local/share/ca-certificates/corp-ca.crt
RUN keytool -importcert -noprompt -cacerts -storepass changeit \
      -alias corp-ca -file /usr/local/share/ca-certificates/corp-ca.crt
USER jenkins
```

## Point the update site at the update center

Set the default update site's URL to `$PUBLIC_URL/update-center.json`. The site's id must match
`UC_ID` (`default` unless you changed it).

In the UI: **Manage Jenkins → Plugins → Advanced settings → Update Site**, enter the URL, submit,
then **Check now** on the Updates tab.

As code, with a Groovy script in `$JENKINS_HOME/init.groovy.d/update-site.groovy`, which runs at
every start:

```groovy
import hudson.model.UpdateSite
import jenkins.model.Jenkins

def uc = Jenkins.get().updateCenter
uc.sites.replaceBy([new UpdateSite("default", "https://jenkins-updates.example.com/update-center.json")])
uc.save()
```

Jenkins adds its own version to the request (`?version=...`), and the update center redirects it to
the tier that fits: see [Version routing](../architecture/request-routing.md#version-routing).

To check it worked: the Plugins page lists available plugins, and **Manage Jenkins** shows no
"signature verification failed" error. If it does, see
[Troubleshooting](../troubleshooting.md#jenkins-controllers).

## jenkins-plugin-cli and the Jenkins Docker image

`jenkins-plugin-cli`, which the official Jenkins image uses to install plugins at build time, reads
three environment variables:

```bash
JENKINS_UC=https://jenkins-updates.example.com
JENKINS_UC_EXPERIMENTAL=https://jenkins-updates.example.com/experimental
JENKINS_PLUGIN_INFO=https://jenkins-updates.example.com/current/plugin-versions.json
```

`jenkins-plugin-cli` checks no update-center signatures: HTTPS is all that protects what it installs.
Use the `https://` URL, with your TLS certificate's CA in the truststore of the Java runtime it runs
on, and keep `JENKINS_PLUGIN_INFO` pointed at this update center: unset, it reads updates.jenkins.io.

`JENKINS_UC` is the bare origin, with no path: the tool builds download URLs from its host and
ignores any path. No download variable is needed. For example, in a controller image:

```dockerfile
FROM jenkins/jenkins:lts
ENV JENKINS_UC=https://jenkins-updates.example.com \
    JENKINS_UC_EXPERIMENTAL=https://jenkins-updates.example.com/experimental \
    JENKINS_PLUGIN_INFO=https://jenkins-updates.example.com/current/plugin-versions.json
COPY plugins.txt /usr/share/jenkins/ref/plugins.txt
RUN jenkins-plugin-cli --plugin-file /usr/share/jenkins/ref/plugins.txt
```

## Downloads

Controllers download plugins from wherever the update center sends them, which they must be able to
reach: [Outbound: controllers](../reference/network.md#outbound-controllers). When they cannot read
the repository without credentials: [Proxy plugin downloads](proxy-downloads.md).
