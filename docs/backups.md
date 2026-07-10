# Backups

Agent Wiki's state lives in two places, and **both must be backed up and
restored together**:

- **The wiki git repository** (the `wiki-data` volume) — every page, with
  full edit history.
- **The Postgres database** — everything that is deliberately *not* in the
  repo: users, per-page permissions and owners, groups, update policies,
  comments, triggers, admin settings, task queues.

Backing up only the volume silently loses all permissions and comments.
Backing up only the database loses the pages. The other two stateful
services need nothing: the search index (OpenSearch) is fully rebuildable
from the wiki + database, and Redis holds only transient queue state.

## Enabling scheduled backups (Helm)

The chart ships an opt-in CronJob that uploads an application-consistent
pair — a `git bundle` of the wiki repo and a `pg_dump` of the database —
to any S3-compatible bucket (AWS S3, MinIO, Cloudflare R2, GCS interop):

```yaml
backup:
  enabled: true
  schedule: "0 8 * * *"   # daily, cluster timezone
  s3:
    bucket: my-wiki-backups
    prefix: agent-wiki-backups
    endpoint: ""          # set for MinIO / R2 / GCS; empty = AWS S3
    region: us-west-2
    # Either static credentials (stored in the chart's secret) ...
    accessKeyId: AKIA...
    secretAccessKey: "..."
    # ... or leave both empty and bind an IAM identity to the backup
    # CronJob's dedicated service account (<release>-agent-workspace-backup)
    # via backup.serviceAccount.annotations (e.g. EKS IRSA). The job only
    # needs s3:PutObject + s3:GetObject on the prefix — deliberately no
    # delete rights, so a compromised cluster can't destroy its backups.
  serviceAccount:
    annotations: {}       # e.g. eks.amazonaws.com/role-arn: arn:aws:iam::...
```

Each run writes `s3://<bucket>/<prefix>/<timestamp>/wiki-<timestamp>.bundle`
and then `db-<timestamp>.dump` — in that order, so **a timestamp group
missing `db-*.dump` is a failed run, not a backup**; ignore it and don't
restore from it.

**Retention is the bucket's job**: set a lifecycle rule on the prefix
(e.g. expire after 14 days). The job itself only ever writes.

**Encryption is the bucket's job too**: the job uploads plain objects over
TLS and relies on the bucket's at-rest encryption — on by default on AWS S3
(SSE-S3) and configurable to SSE-KMS with your own key; on MinIO/R2/GCS,
enable default encryption on the bucket/server. The wiki bundle contains
every page in the clear, so scope bucket read access accordingly.
Application-encrypted database columns (LLM provider keys and other
secrets) remain ciphertext inside the dump regardless.

**Alert on two signals**: `kube_job_status_failed` for the
`<release>-backup` CronJob catches failing runs, and a freshness check —
`kube_cronjob_status_last_successful_time` (or the age of the newest
object under the prefix) — catches backups that silently stop being
scheduled at all (suspended CronJob, `backup.enabled` lost in an upgrade).

The backup pod mounts the wiki volume read-only and co-schedules with the
backend (the volume is ReadWriteOnce), and runs `pg_dump` against the same
`DATABASE_URL` the app uses. Nothing is paused during backup: `git bundle`
is a read operation and `pg_dump` dumps from a snapshot.

To take a one-off backup outside the schedule:

```bash
kubectl create job --from=cronjob/<release>-agent-workspace-backup manual-backup-1
```

## Restore

Restore both halves **from the same timestamp group** (one that contains
both files), then let the app rebuild its derived state.

**Prerequisite — the encryption key is NOT in the backup.** Encrypted
database columns (LLM provider credentials and other secrets) are
AES-encrypted with a key derived from `SECRET_KEY` /
`ENCRYPTION_KEY_SECRET`, which live in the Helm secret, not in the dump.
A restore onto a freshly generated key succeeds — with every encrypted
column unreadable. Preserve the original Helm secret values (password
manager, sealed secret, cloud secret store) alongside your backups, and
install the restored deployment with them.

1. **Stop writes** — scale the backend and workers to zero.

2. **Restore the wiki repo** onto a fresh volume (or an emptied one):

   ```bash
   git clone wiki-<ts>.bundle restored-wiki
   ```

   The clone contains the working tree and full history. Place its
   contents (including `.git`) at the root of the `wiki-data` volume.

3. **Restore the database** into an empty database:

   ```bash
   pg_restore --no-owner --dbname "$DATABASE_URL" db-<ts>.dump
   ```

4. **Clear derived state**: flush Redis (`FLUSHALL` — it only holds queue
   state that predates the restore) and delete the OpenSearch indices
   (stale entries for pages newer than the backup would otherwise linger
   in search results).

5. **Scale back up.** On boot the backend re-indexes every page, rebuilds
   the comment search index, and re-converges the trigger cache from the
   restored repo — no manual steps.

What you lose is bounded: changes made after the backup timestamp, and any
in-flight queue work (regenerated by the next trigger/ingest event).

## Alternatives and complements

- **Volume snapshots** (EBS/PD/CSI) are a good *complement* — fast,
  incremental, infra-level — but remember they cover only the wiki repo
  half; pair them with database backups (e.g. RDS point-in-time recovery)
  restored to the same timestamp.
- **Bucket versioning/replication** adds protection the CronJob doesn't
  (accidental deletion, region failure), and object-lock/WORM settings can
  make backups immutable for compliance or ransomware-grade protection.
