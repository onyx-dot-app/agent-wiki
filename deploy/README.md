# deploy/

Two-step deploy for agent-workspace:

1. **`terraform/`** — provisions a small EKS cluster (VPC, EKS, EBS CSI add-on,
   gp3 default StorageClass, ingress-nginx, cert-manager, optional
   `letsencrypt-prod` ClusterIssuer).
2. **`helm/agent-workspace/`** — installs the app (backend, worker, frontend,
   two PVCs, Ingress) onto the cluster from step 1.

Terraform is run once per environment; Helm is the loop you run on every app
deploy. State is local (`terraform.tfstate` is gitignored) — fine for a single
operator. Move it to S3 when more than one person operates the cluster.

## Prereqs

- AWS credentials with permission to create VPC + EKS + IAM roles.
- `terraform >= 1.5`, `kubectl`, `helm >= 3.13`, `aws` CLI.
- A DNS zone where you can point a record at the ingress NLB.

## 1. Provision the cluster

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

## 2. Build & push images

The chart pulls from a public registry — defaults are `ghcr.io/CHANGE-ME/...`.
Build and push:

```bash
# from repo root
docker build -t ghcr.io/<you>/agent-workspace-backend:<tag>  ./backend
docker build -t ghcr.io/<you>/agent-workspace-frontend:<tag> ./frontend
docker push ghcr.io/<you>/agent-workspace-backend:<tag>
docker push ghcr.io/<you>/agent-workspace-frontend:<tag>
```

If the repo is private, create an `imagePullSecret` and pass it via
`--set imagePullSecrets[0].name=<secret>`.

## 3. Install the chart

```bash
cd deploy/helm

helm upgrade --install agent-workspace ./agent-workspace \
  --namespace agent-workspace --create-namespace \
  --set secretKey="$(openssl rand -hex 32)" \
  --set image.backend.repository=ghcr.io/<you>/agent-workspace-backend \
  --set image.backend.tag=<tag> \
  --set image.frontend.repository=ghcr.io/<you>/agent-workspace-frontend \
  --set image.frontend.tag=<tag> \
  --set ingress.host=agent-workspace.example.com \
  --set ingress.clusterIssuer=letsencrypt-prod \
  --set ingress.tls.enabled=true
```

Or write a `values.yaml` per environment and use `-f`.

## 4. Wire DNS

Get the ingress NLB hostname:

```bash
$(cd deploy/terraform && terraform output -raw ingress_hostname_command)
```

Create a CNAME from `ingress.host` to that hostname. cert-manager will issue a
Let's Encrypt cert once DNS resolves (give it a minute).

## 5. Sign in

Open `https://<ingress.host>`. The first account you sign up is auto-promoted
to admin (see `app/auth/users.py`). Configure the LLM provider/keys from
**Admin → LLM**.

## Day-2

- **App update** — push new images, then `helm upgrade` with the new tag.
- **Cluster update** — `terraform apply` after bumping `cluster_version` or
  module versions.
- **Backups** — not wired in this scaffold. The `wiki-data` PVC is a real git
  repo; cron a `git push --mirror` to a remote for the durable content. The
  `app-data` PVC holds SQLite + the Huey queue; `VACUUM INTO` snapshots are
  cheap.
- **Tear down** — `helm uninstall agent-workspace -n agent-workspace`, then
  `terraform destroy`. PVCs use `Retain` reclaim policy, so EBS volumes
  survive `helm uninstall` and need to be deleted manually if you want them
  gone.

## What's deliberately not here

- **No RDS / Redis / S3.** The app is SQLite-on-PVC by design — see
  `local_data/wiki/infra/infra.md` and `CLAUDE.md`.
- **No multi-replica backend/worker.** SQLite is single-writer; replicas would
  fight over the wiki working tree. The chart pins both to one replica with a
  `Recreate` strategy and a `podAffinity` rule that co-schedules them.
- **No remote Terraform state.** Local for now (gitignored). Add an S3 backend
  when this graduates beyond a single-operator deploy.
