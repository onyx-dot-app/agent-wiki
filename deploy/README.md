# deploy/

Two pieces:

1. **`helm/agent-workspace/`** — the chart. Installs the app (backend, worker,
   frontend, two PVCs, Ingress) onto **any existing Kubernetes cluster**.
   Published as a Helm repo from the `gh-pages` branch — this is the primary
   way to run agent-wiki.
2. **`terraform/`** — a **starting template** for provisioning a small EKS
   cluster (VPC, EKS, EBS CSI add-on, gp3 default StorageClass, ingress-nginx,
   cert-manager). Generic; not the source of truth for the agent-wiki team's
   own deploys. See [`terraform/README.md`](terraform/README.md).

If you already have a cluster, skip to step 2. Otherwise, the terraform
template gets you one in ~15 minutes — copy it into your own private repo
first and customize.

## Prereqs

- `kubectl`, `helm >= 3.13`. For the terraform template: `terraform >= 1.5`,
  `aws` CLI, AWS credentials with permission to create VPC + EKS + IAM roles.
- A DNS zone where you can point a record at the ingress NLB.

## 1. Provision the cluster (optional — skip if you have one)

```bash
cd deploy/terraform
cp example.tfvars terraform.tfvars  # edit name, region, cert_manager_email
terraform init
terraform apply
```

Apply takes ~15 minutes (EKS control plane is the slow bit).

When it's done:

```bash
$(terraform output -raw kubeconfig_command)   # writes ~/.kube/config entry
kubectl get nodes
```

## 2. Publish images

Images are built and pushed to Docker Hub
(`onyxdotapp/agent-wiki-{backend,frontend}`) by
`.github/workflows/docker-build-push.yml`. Cut a tag to release:

```bash
git tag v0.0.1
git push --tags
```

The workflow publishes multi-arch images for `linux/amd64` + `linux/arm64`.
Verify with `docker pull onyxdotapp/agent-wiki-backend:v0.0.1`.

## 3. Wire DNS

Get the ingress NLB hostname:

```bash
# If you used the terraform template:
cd deploy/terraform && terraform output -raw ingress_lb_hostname
# Otherwise, ask your cluster directly:
kubectl -n ingress-nginx get svc ingress-nginx-controller \
  -o jsonpath='{.status.loadBalancer.ingress[0].hostname}'
```

Create a CNAME in your DNS provider pointing the host you want
(`<your-host>`) at the NLB hostname. Wait for DNS to propagate.
cert-manager issues a Let's Encrypt cert via HTTP-01 once DNS resolves
and the chart is installed.

## 4. Install the chart

```bash
helm upgrade --install agent-wiki ./deploy/helm/agent-workspace \
  --namespace agent-wiki --create-namespace \
  --set secretKey="$(openssl rand -hex 32)" \
  --set databaseUrl="postgresql://USER:PASSWORD@HOST:5432/DBNAME" \
  --set image.backend.tag=v0.0.1 \
  --set image.frontend.tag=v0.0.1 \
  --set ingress.host=<your-host> \
  --set ingress.clusterIssuer=letsencrypt-prod \
  --set ingress.tls.enabled=true
```

The image repos default to `onyxdotapp/agent-wiki-{backend,frontend}` in
`values.yaml`, so you only need to override `tag`. Or write a `values.yaml`
per environment and use `-f`.

## 5. Sign in

Open `https://<ingress.host>`. The first account you sign up is auto-promoted
to admin (see `app/auth/users.py`). Configure the LLM provider/keys from
**Admin → LLM**.

## Day-2

- **App update** — cut a new `v*` tag, then `helm upgrade --set image.*.tag=<new>`.
- **Cluster update** — `terraform apply` after bumping `cluster_version` or
  module versions.
- **Backups** — opt-in via the chart: `backup.enabled=true` schedules a
  CronJob that uploads a `git bundle` of the wiki repo **and** a `pg_dump`
  of the database together to any S3-compatible bucket. Both halves are
  needed — permissions, comments, and update policies live only in
  Postgres. Setup and restore: [`docs/backups.md`](../docs/backups.md).
- **Tear down** — `helm uninstall agent-wiki -n agent-wiki`, then
  `terraform destroy`. PVCs use `Retain` reclaim policy, so EBS volumes
  survive `helm uninstall` and need to be deleted manually if you want them
  gone.

## What's deliberately not here

- **No managed Postgres / Redis / S3 provisioning.** The chart assumes a
  stock Postgres 17 is reachable via `DATABASE_URL` (set on the backend +
  worker env). No extensions or `shared_preload_libraries` tuning are
  required — BM25 search runs on OpenSearch and task queues run on Redis
  Streams, so Postgres only holds app state. Provision it however you want
  — RDS, Cloud SQL, a self-managed instance, etc. — see
  `local_data/wiki/infra/infra.md` and `CLAUDE.md`. `docker-compose.yml`
  shows the wiring for self-managed setups.
- **No multi-replica backend/worker.** Two reasons, both still binding
  after the Postgres migration: (a) the `wiki-data` PVC is RWO and the
  pods share it, so replicas would fight over the git working tree; and
  (b) the per-queue periodic-task scheduler runs in-process — multiple
  replicas would double-fire crons. The chart pins both to one replica
  with a `Recreate` strategy and a `podAffinity` rule that co-schedules
  them.
- **No remote Terraform state.** Local for now (gitignored). Add an S3 backend
  when this graduates beyond a single-operator deploy.
