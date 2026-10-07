#!/usr/bin/env bash
# Checks for uc/bin/uc-setup, the setup service's entrypoint. Runs as root inside the uc image, with
# /setup laid out as compose.yaml mounts it (config/, secrets/, procurement/) on a Linux filesystem
# where ownership and ACLs are real (Docker Desktop bind mounts ignore both), and acts as each party
# with setpriv: the host user (1000, who owns config/), the uc containers (10001) and anyone else
# (2000). Usage, from the Makefile: make test-setup
set -uo pipefail
setup=/scripts/uc-setup
config=/setup/config
secrets=/setup/secrets
procurement=/setup/procurement
host=1000:1000
uc=10001:10001
other=2000:2000
usage_line='usage: uc-setup [secrets | procurement | pki-init [--out DIR] [--display-dir NAME] | pki-rotate [--root-display-dir NAME]]'
failures=0
output=''
rc=0

as() { # as UID:GID COMMAND - run COMMAND as that user, with no supplementary groups
    setpriv --reuid="${1%%:*}" --regid="${1##*:}" --clear-groups bash -c "$2" 2> /dev/null
}

pass() { echo "ok: $1"; }

fail() {
    echo "FAIL: $1"
    failures=$((failures + 1))
}

allow() { # allow DESCRIPTION UID:GID COMMAND - COMMAND must succeed
    if as "$2" "$3"; then pass "$1"; else fail "$1"; fi
}

deny() { # deny DESCRIPTION UID:GID COMMAND - COMMAND must fail
    if as "$2" "$3"; then fail "$1"; else pass "$1"; fi
}

same() { # same DESCRIPTION EXPECTED ACTUAL - an exact comparison
    if [[ "$3" == "$2" ]]; then pass "$1"; else fail "$1 (expected '$2', got '$3')"; fi
}

stat_is() { # stat_is DESCRIPTION FORMAT EXPECTED PATH - stat -c FORMAT PATH prints EXPECTED
    local actual
    actual="$(stat -c "$2" "$4" 2>&1)"
    same "$1" "$3" "${actual}"
}

run() { # run [ARG...] - uc-setup as the setup service runs it, as root; sets rc and output
    output="$(UC_CONFIG_FILE="${config}/uc.toml" sh "${setup}" "$@" 2>&1)"
    rc=$?
}

says() { # says DESCRIPTION TEXT - the last run's output holds TEXT
    if [[ "${output}" == *"$2"* ]]; then pass "$1"; else fail "$1 (no '$2' in: ${output})"; fi
}

names_host_paths() { # names_host_paths LABEL - the last run's output names no file by its /setup/ path
    if [[ "${output}" == *"/setup/"* ]]; then fail "$1: the output names /setup/... (${output})"; else pass "$1: the output names the files as they are on the host"; fi
}

expect_usage() { # expect_usage ARG... - uc-setup ARG... exits 2 with the usage line
    run "$@"
    if [[ ${rc} -eq 2 && "${output}" == "${usage_line}" ]]; then pass "usage: uc-setup $*"; else fail "usage: uc-setup $* (exit ${rc}: ${output})"; fi
}

secrets_permissions() { # secrets_permissions LABEL DIR - the owners and modes secrets-permissions.sh sets (tests/secrets)
    local file
    for file in update-center.key repo_username repo_password github_username github_token repo_push_auth; do
        [[ -e "$2/${file}" ]] || continue
        stat_is "$1: ${file} is yours, group 10001, 0640" %u:%g:%a 1000:10001:640 "$2/${file}"
    done
    stat_is "$1: update-center.cert is yours, group 10001, 0644" %u:%g:%a 1000:10001:644 "$2/update-center.cert"
    stat_is "$1: root-ca.crt is yours, group 10001, 0644" %u:%g:%a 1000:10001:644 "$2/root-ca.crt"
    stat_is "$1: root/ is yours alone, 0700" %u:%g:%a 1000:1000:700 "$2/root"
    stat_is "$1: root/root-ca.key is yours alone, 0600" %u:%g:%a 1000:1000:600 "$2/root/root-ca.key"
}

fresh() { # /setup as Docker leaves it on a fresh host: config/ is the host user's, secrets/ and procurement/ empty and root's
    find /setup -mindepth 1 -maxdepth 1 -exec rm -rf {} +
    mkdir "${config}" "${secrets}" "${procurement}"
    chown "${host}" "${config}"
}

if [[ ! -f "${setup}" ]]; then
    echo "FAIL: ${setup} does not exist (make test-setup mounts uc/bin at /scripts)"
    exit 1
fi
mkdir -p /setup
if ! probe="$(setfacl -m u:10001:rwx /setup 2>&1 && setfacl -b /setup 2>&1)"; then
    echo "FAIL: setfacl does not work on /setup, so the ACL checks cannot run here: ${probe}"
    exit 1
fi

# 1. A fresh host: Docker created secrets/ and procurement/ as root. setup gives both to the owner of
#    config/, creates the credential files empty and the PKI, and names the files as they are on the host.
fresh
run
same "fresh: setup exits 0" 0 "${rc}"
stat_is "fresh: secrets/ is the host user's" %u:%g "${host}" "${secrets}"
stat_is "fresh: procurement/ is the host user's" %u:%g "${host}" "${procurement}"
for file in repo_username repo_password github_username github_token download_proxy_auth repo_push_auth; do
    stat_is "fresh: ${file} exists, empty" %s 0 "${secrets}/${file}"
done
secrets_permissions "fresh" "${secrets}"
stat_is "fresh: download_proxy_auth is yours, group 33, 0640" %u:%g:%a 1000:33:640 "${secrets}/download_proxy_auth"
allow "fresh: you write a credential file setup created" "${host}" "printf pw > ${secrets}/repo_password"
says "fresh: says to move the root key offline" "move secrets/root/root-ca.key to offline storage"
names_host_paths "fresh"

# 2. procurement/ is shared through ACLs: you and the uc containers get rwx on it and, as defaults, on
#    everything created in it later. uc stages with its own code (uc/uc/procure/staging.py) as uid
#    10001: mkstemp in the folder, mkdir (mode 0o777) for each Maven directory, chmod 0o664.
acl="$(getfacl --absolute-names --numeric --omit-header "${procurement}" 2>&1)"
for entry in user:1000:rwx user:10001:rwx default:user:1000:rwx default:user:10001:rwx; do
    if grep -qx "${entry}" <<< "${acl}"; then pass "acl: procurement/ has ${entry}"; else fail "acl: procurement/ lacks ${entry} (${acl})"; fi
done
stage='
from pathlib import Path
from uc.procure.staging import StagedFile, Staging
staging = Staging(Path("/setup/procurement"), "https://repo.example/", {})
part = staging.temporary()
part.write_bytes(b"staged")
staging.commit(StagedFile("io/jenkins/plugins/demo/1.0/demo-1.0.hpi", "https://repo.example/demo-1.0.hpi", "0" * 40, 6, "dependency", "checks", "2026-10-07T00:00:00Z"), part)
staging.save()
'
staged="${procurement}/io/jenkins/plugins/demo/1.0/demo-1.0.hpi"
allow "acl: uc stages a file and writes procurement.json" "${uc}" "umask 022 && python3 -c '${stage}'"
allow "acl: you edit a file uc staged" "${host}" "echo edited >> ${staged}"
allow "acl: you edit procurement.json" "${host}" "echo >> ${procurement}/procurement.json"
allow "acl: you rename a file uc staged" "${host}" "mv ${staged} ${staged}.renamed"
allow "acl: you delete a file uc staged" "${host}" "rm ${staged}.renamed"
allow "acl: you delete the directories uc created" "${host}" "rm -r ${procurement}/io"
allow "acl: you place a file by hand, with umask 022" "${host}" "umask 022 && mkdir -p ${procurement}/org/x/1.0 && printf hpi > ${procurement}/org/x/1.0/x-1.0.hpi"
allow "acl: uc moves a file you placed to another directory" "${uc}" "umask 022 && mkdir -p ${procurement}/io/jenkins/plugins/x/1.0 && mv ${procurement}/org/x/1.0/x-1.0.hpi ${procurement}/io/jenkins/plugins/x/1.0/"
allow "acl: uc removes the directories you created, once empty" "${uc}" "rmdir ${procurement}/org/x/1.0 ${procurement}/org/x ${procurement}/org"
allow "acl: uc deletes a file you placed" "${uc}" "rm ${procurement}/io/jenkins/plugins/x/1.0/x-1.0.hpi"
deny "acl: another user cannot write in procurement/" "${other}" "touch ${procurement}/x"
deny "acl: another user cannot write in a directory in it" "${other}" "touch ${procurement}/io/x"

# 3. Running it again keeps the signing keys.
before="$(sha256sum "${secrets}/update-center.key" 2>&1)"
run
after="$(sha256sum "${secrets}/update-center.key" 2>&1)"
same "again: setup exits 0" 0 "${rc}"
says "again: says the signing keys are left as they are" "the signing keys exist; left as they are"
same "again: update-center.key is unchanged" "${before}" "${after}"

# 4. A filesystem without ACLs (setfacl fails, as it does there): nothing to do when uid 10001 can
#    already write procurement/; otherwise an error naming the make targets that hand it over per run.
mkdir -p /tmp/noacl
cat > /tmp/noacl/setfacl << 'EOF'
#!/bin/sh
echo "setfacl: Operation not supported" >&2
exit 1
EOF
chmod 0755 /tmp/noacl/setfacl
rm -rf "${procurement}" && mkdir -m 0777 "${procurement}"
output="$(PATH="/tmp/noacl:${PATH}" UC_CONFIG_FILE="${config}/uc.toml" sh "${setup}" procurement 2>&1)"
rc=$?
same "no ACLs, 0777: setup procurement exits 0" 0 "${rc}"
says "no ACLs, 0777: says nothing is needed" "nothing to do"
left="$(ls -A "${procurement}" 2>&1)"
same "no ACLs, 0777: leaves nothing in procurement/" "" "${left}"
chmod 0755 "${procurement}" && chown "${host}" "${procurement}"
output="$(PATH="/tmp/noacl:${PATH}" UC_CONFIG_FILE="${config}/uc.toml" sh "${setup}" procurement 2>&1)"
rc=$?
same "no ACLs, 0755: setup procurement exits 1" 1 "${rc}"
says "no ACLs, 0755: names make procure-stage" "make procure-stage"

# 5. pki-init elsewhere, as make pki-init OUT=newpki runs it: the PKI in that directory, named as it is
#    on the host, with secrets-permissions.sh's owners and modes; a second run refuses to overwrite it.
mkdir /setup/out && chown "${host}" /setup/out
run pki-init --out /setup/out --display-dir newpki
same "pki-init --out: exits 0" 0 "${rc}"
says "pki-init --out: names newpki/root-ca.crt" "created newpki/root-ca.crt"
says "pki-init --out: says to move newpki/root/root-ca.key offline" "move newpki/root/root-ca.key to offline storage"
names_host_paths "pki-init --out"
secrets_permissions "pki-init --out" /setup/out
run pki-init --out /setup/out --display-dir newpki
same "pki-init --out again: exits 1" 1 "${rc}"
says "pki-init --out again: refuses to overwrite" "refusing to overwrite existing files: newpki/"

# 6. pki-rotate: the root key must be mounted at /setup/secrets/root; with it there, a new signing
#    pair, the previous one kept as *.old.
mv "${secrets}/root" /setup/offline-root
run pki-rotate
same "pki-rotate without root/: exits 1" 1 "${rc}"
says "pki-rotate without root/: says where to mount it" "mount the directory holding root-ca.key read-only at /setup/secrets/root"
mv /setup/offline-root "${secrets}/root"
before="$(sha256sum < "${secrets}/update-center.key" 2>&1)"
run pki-rotate
old="$(sha256sum < "${secrets}/update-center.key.old" 2>&1)"
same "pki-rotate: exits 0" 0 "${rc}"
says "pki-rotate: names secrets/update-center.cert" "issued a new secrets/update-center.cert"
same "pki-rotate: keeps the previous key as update-center.key.old" "${before}" "${old}"
stat_is "pki-rotate: update-center.key.old is yours, group 10001, 0640" %u:%g:%a 1000:10001:640 "${secrets}/update-center.key.old"
stat_is "pki-rotate: update-center.cert.old is yours, group 10001, 0644" %u:%g:%a 1000:10001:644 "${secrets}/update-center.cert.old"
secrets_permissions "pki-rotate" "${secrets}"

# 7. Usage.
expect_usage bogus
expect_usage secrets extra
expect_usage procurement extra
expect_usage pki-init --out
expect_usage pki-rotate --bogus value

if [[ ${failures} -ne 0 ]]; then
    echo "${failures} check(s) failed"
    exit 1
fi
