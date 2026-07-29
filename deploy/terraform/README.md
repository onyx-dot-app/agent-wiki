# Terraform — starting template

This directory is a **template**: a small reference Terraform module that
shows one way to stand up the infrastructure agent-wiki needs (VPC, EKS,
ingress-nginx, cert-manager). It's intentionally generic — copy it into
your own private repo and customize for your environment.

It is **not** the source of truth for the agent-wiki team's own deploys;
that lives in a private repo and is intentionally not linked from here.

## What it provisions

- VPC across 3 AZs (`terraform-aws-modules/vpc/aws`)
- EKS cluster with one managed node group (`terraform-aws-modules/eks/aws`)
- EBS CSI driver add-on with IRSA, plus a `gp3` default `StorageClass`
- `ingress-nginx` (NLB-backed) and `cert-manager` as `helm_release`s
- Optional `letsencrypt-prod` `ClusterIssuer` if `cert_manager_email` is set

No RDS, Redis, or S3 — this template only provisions the cluster + ingress.
agent-wiki itself needs a stock Postgres 17 for app state (BM25 search runs
on OpenSearch, task queues on Redis Streams — no Postgres extensions
required), plus an RWO PVC for the wiki git working tree.
Provision Postgres separately (RDS, Cloud SQL, or self-managed) and wire
it via `DATABASE_URL` on the chart. See `local_data/wiki/infra/infra.md`.

## Use it

```bash
cp example.tfvars terraform.tfvars  # edit for your env
terraform init
terraform apply
```

Then install the chart from `../helm/agent-workspace/`. See
[`../README.md`](../README.md) for the full walkthrough.

## When you outgrow it

For real deploys you'll likely want:
- Remote state (S3 + DynamoDB lock) — local state is fine for a single operator
- A real DNS record (this template leaves DNS as a manual CNAME step)
- Tighter `cluster_endpoint_public_access_cidrs`
- Backups for the wiki PVC

Customize as needed; the template is meant to be edited, not consumed verbatim.
