# AICS MySQL Cutover Runbook

This runbook migrates only AI Customer Service data into `aics_*` tables in
`wecom_cs`. It must not modify any existing platform table.

## Preconditions

- RDS SSL is enabled and the server can establish an encrypted connection.
- The RDS CA certificate is installed and configured with `AICS_MYSQL_SSL_CA`
  for certificate verification. Encryption is still enforced without a CA,
  but hostname verification is unavailable in that fallback mode.
- `AICS_MYSQL_SSL_REQUIRED=true`.
- `OUTREACH_AUTO_SEND_ENABLED=false`.
- The deployed commit has passed SQLite and MySQL storage contract tests.
- A 15-minute maintenance window is approved.

The migration command refuses an unencrypted connection and refuses to run
`apply` without `--confirm-service-stopped`.

## Preflight

From the deployed `ai_paths` directory:

```bash
PYTHONPATH=. python scripts/migrate_sqlite_to_mysql.py preflight \
  --sqlite-path /opt/ai-paths/data/ai_paths.db \
  --output-dir /opt/ai-paths/data/migration
```

Review `preflight.json`. JSON errors must be zero. Keep the platform schema
fingerprint for post-migration comparison.

## Cutover

```bash
sudo systemctl stop ai-paths.service

PYTHONPATH=. python scripts/migrate_sqlite_to_mysql.py apply \
  --sqlite-path /opt/ai-paths/data/ai_paths.db \
  --output-dir /opt/ai-paths/data/migration \
  --batch-size 300 \
  --confirm-service-stopped
```

The command creates a SQLite backup, archives six legacy configuration tables,
runs Alembic, migrates the 12 active tables, and verifies counts and internal
relationships.

Only after `apply.json` reports no verification errors, update the server
environment:

```env
AICS_STORAGE_BACKEND=mysql
AICS_MYSQL_SSL_REQUIRED=true
AICS_TABLE_PREFIX=aics_
AICS_SQLITE_MIRROR_ENABLED=true
```

Then start and verify:

```bash
sudo systemctl start ai-paths.service
sudo systemctl is-active ai-paths.service
curl -fsS http://127.0.0.1:8000/health
```

Keep the SQLite mirror enabled for 24 hours. MySQL is the only read and task
claim source during this period.

## Verification

- `/health` returns HTTP 200.
- Ordinary isolated replies persist runs and traces.
- SOP and Outreach logs load from MySQL.
- No pending task is claimed twice.
- `OUTREACH_AUTO_SEND_ENABLED` remains false.
- Existing non-`aics_*` platform schema/index fingerprint is unchanged.

## Rollback Within 24 Hours

```bash
sudo systemctl stop ai-paths.service
```

Set:

```env
AICS_STORAGE_BACKEND=sqlite
AICS_SQLITE_MIRROR_ENABLED=false
```

Restart the service. Do not delete or overwrite `aics_*` tables. Preserve the
MySQL state and compare the failure-window increments before scheduling another
cutover.

After 24 stable hours, disable the mirror. After seven stable days, compress
and archive the SQLite backup; do not delete it during the initial rollout.
