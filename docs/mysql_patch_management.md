# MySQL patch management

This page checks whether an Ubuntu server can receive an in-series MySQL package update, shows the exact package changes, and can dry-run an approved plan. This version does not install packages.

The older **Patching** page is separate. That page runs a SQL file against a connected database.

## What this version supports

- Windows desktop app, saved MySQL server, SSH already stored on that server.
- Remote Ubuntu with APT.
- MySQL server packages that come only from the Ubuntu archive (`archive.ubuntu.com`, `security.ubuntu.com`, or `ports.ubuntu.com`).
- A newer package in the same MySQL series, for example 8.0.36 to 8.0.40, or the same upstream version with a newer Ubuntu package revision.

## What this version refuses

- Any operating system other than Ubuntu, or a server without APT.
- Oracle MySQL APT (`repo.mysql.com`), a mix of archives, or any other mirror.
- `mysql-community-server`.
- A release-series change such as 8.0 to 8.4, or a major change such as 8.x to 9.x.
- A candidate older than the installed version.
- A simulation that removes a package or changes a package outside the Ubuntu MySQL set.
- `apt-get upgrade`, `apt full-upgrade`, and `apt-get install` without `-s`.
- A second patch job for the same server while one is already running.
- Automatic retry, reboot, downgrade, or backup restore.

Apply Patch runs `apt-get -s` only. The history row stays `dry_run_passed` and `packages_installed` is no.

## Before you start

1. Save the MySQL server under **Servers** with SSH enabled, a Linux user, and the SSH host.
2. Take a backup with **Backup** and keep that file.
3. The precheck only checks that the file can be read. It does not prove the backup can be restored.
4. Plan a maintenance window. An Ubuntu MySQL package update commonly restarts MySQL. This app does not know your change calendar.
5. An unknown SSH host key is rejected. Read the fingerprint on the server and type it into the trust dialog. The presented fingerprint is not saved by itself. A changed key is rejected and is not replaced.

The remote user must be root or have passwordless `sudo`. The app will not ask for a sudo password.

## Using the page

Open **Operations → Patching**, then choose **MySQL Patch Management** in the menu under that row.

1. Choose the saved server and the backup file.
2. **Run Prechecks.** Pass, warning, and failure lines appear in the results box. Disk must have at least 1 GiB free on `/`, `/var`, and the filesystem that holds the APT cache. That 1 GiB floor is this app's rule, not a number published by MySQL.
3. **Refresh Patch Information** after prechecks are ready. This is the package-change preview.
4. **Generate Patch Plan.**
5. **Review and Approve.** Tick the risk box and enter your name. Approval lasts 30 minutes.
6. **Apply Patch** stays disabled until the precheck is ready, the preview is accepted, and that approval is recorded.

Apply rechecks the server before the dry-run. If the candidate version, repository URL, or package set changed, the dry-run stops.

After the dry-run, verification reads the installed package, the MySQL service, and `dpkg` audit. `SELECT 1` runs only when a database probe is supplied. The desktop page does not supply one, so verification is not marked successful. That is expected while Apply does not install.

## History and reports

Jobs are appended to `var/data/mysql_patch_jobs.json`. SQL-script patch history stays in `var/logs/patch_history.json`.

The file stores the job id, server name, user, times, versions, plan id, plan digest, approver, stage, execution result, verification result, and a redacted error. Passwords are not stored.

**View Details**, **Export CSV**, and **Export PDF** use the selected history row.

An interrupted, failed, partial, unknown, or verification-failed job for that server blocks the next precheck. A finished dry-run does not.

## If a check fails

Do not downgrade MySQL or restore a backup from this page. Read the stop reason, the MySQL service state, and `dpkg` audit on the server. Start a new precheck only after the server state is understood.

A timed-out or disconnected dry-run did not send an install. The app does not retry it. You can start a new dry-run after the lock is released.

## Automated tests

Normal tests use fake SSH output. They do not connect to a server and they do not install packages.

```powershell
python -m pytest tests/test_mysql_patch_discovery.py tests/test_mysql_patch_precheck.py tests/test_mysql_patch_preview.py tests/test_mysql_patch_plan.py tests/test_mysql_patch_executor.py tests/test_mysql_patch_verification.py tests/test_mysql_patch_page.py tests/test_mysql_patch_integration.py -q
```

`tests/test_mysql_patch_integration.py` is skipped unless you opt in.

## Opt-in check on a disposable Ubuntu VM

Use a test virtual machine, not a production server. The check only reads the server. It still opens SSH.

```powershell
$env:DB_AUTOPRO_MYSQL_PATCH_IT = "1"
$env:DB_AUTOPRO_MYSQL_PATCH_HOST = "192.168.1.50"
$env:DB_AUTOPRO_MYSQL_PATCH_USER = "ubuntu"
$env:DB_AUTOPRO_MYSQL_PATCH_PASSWORD = "the-test-password"
$env:DB_AUTOPRO_MYSQL_PATCH_PORT = "22"
python -m pytest tests/test_mysql_patch_integration.py -q
```

Unset `DB_AUTOPRO_MYSQL_PATCH_IT` when you are finished. Do not put that password in a commit, a log, or a report.
