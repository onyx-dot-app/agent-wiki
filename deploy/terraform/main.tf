# Template Terraform — see README.md in this directory. Copy into your own
# private repo and customize before running.

locals {
  full_name    = "${var.name}-${var.environment}"
  vpc_name     = "${local.full_name}-vpc"
  cluster_name = local.full_name

  default_tags = merge(
    {
      app         = var.name
      environment = var.environment
      managed-by  = "terraform"
    },
    var.tags,
  )
}

provider "aws" {
  region = var.region

  default_tags {
    tags = local.default_tags
  }
}

data "aws_availability_zones" "available" {
  filter {
    name   = "opt-in-status"
    values = ["opt-in-not-required"]
  }
}

# ----------------------------------------------------------------------------
# VPC
# ----------------------------------------------------------------------------

module "vpc" {
  source  = "terraform-aws-modules/vpc/aws"
  version = "~> 5.0"

  name = local.vpc_name
  cidr = "10.0.0.0/16"
  azs  = slice(data.aws_availability_zones.available.names, 0, 3)

  private_subnets = ["10.0.1.0/24", "10.0.2.0/24", "10.0.3.0/24"]
  public_subnets  = ["10.0.101.0/24", "10.0.102.0/24", "10.0.103.0/24"]

  enable_nat_gateway   = true
  single_nat_gateway   = true
  enable_dns_hostnames = true

  public_subnet_tags = {
    "kubernetes.io/role/elb" = "1"
  }

  private_subnet_tags = {
    "kubernetes.io/role/internal-elb" = "1"
  }
}

# ----------------------------------------------------------------------------
# EKS
# ----------------------------------------------------------------------------

module "eks" {
  source  = "terraform-aws-modules/eks/aws"
  version = "~> 20.0"

  cluster_name    = local.cluster_name
  cluster_version = var.cluster_version

  vpc_id     = module.vpc.vpc_id
  subnet_ids = concat(module.vpc.private_subnets, module.vpc.public_subnets)

  cluster_endpoint_public_access           = true
  cluster_endpoint_public_access_cidrs     = var.cluster_endpoint_public_access_cidrs
  enable_cluster_creator_admin_permissions = true

  cluster_addons = {
    coredns    = {}
    kube-proxy = {}
    vpc-cni    = {}
    aws-ebs-csi-driver = {
      service_account_role_arn = module.ebs_csi_irsa.iam_role_arn
    }
  }

  eks_managed_node_group_defaults = {
    ami_type = "AL2023_x86_64_STANDARD"
  }

  eks_managed_node_groups = {
    main = {
      instance_types = var.node_instance_types
      min_size       = var.node_min_size
      max_size       = var.node_max_size
      desired_size   = var.node_desired_size
      # Pin to a single AZ. EBS volumes are AZ-bound; if a node replacement
      # lands in a different AZ than the original PVCs, the chart's RWO PVCs
      # can't follow and the backend pod gets stuck Pending with
      # "node(s) didn't match PersistentVolume's node affinity".
      subnet_ids = slice(module.vpc.private_subnets, 0, 1)
    }
  }
}

# IRSA for the EBS CSI driver so PVCs (gp3) can be provisioned by the addon.
module "ebs_csi_irsa" {
  source  = "terraform-aws-modules/iam/aws//modules/iam-role-for-service-accounts-eks"
  version = "~> 5.39"

  role_name             = "${local.cluster_name}-ebs-csi"
  attach_ebs_csi_policy = true

  oidc_providers = {
    eks = {
      provider_arn               = module.eks.oidc_provider_arn
      namespace_service_accounts = ["kube-system:ebs-csi-controller-sa"]
    }
  }
}

# Make sure the cluster is fully active before any helm_release tries to talk to it.
resource "null_resource" "wait_for_cluster" {
  provisioner "local-exec" {
    command = "aws eks wait cluster-active --name ${module.eks.cluster_name} --region ${var.region}"
  }

  depends_on = [module.eks]
}

data "aws_eks_cluster" "this" {
  name       = module.eks.cluster_name
  depends_on = [null_resource.wait_for_cluster]
}

data "aws_eks_cluster_auth" "this" {
  name       = module.eks.cluster_name
  depends_on = [null_resource.wait_for_cluster]
}

provider "kubernetes" {
  host                   = data.aws_eks_cluster.this.endpoint
  cluster_ca_certificate = base64decode(data.aws_eks_cluster.this.certificate_authority[0].data)
  token                  = data.aws_eks_cluster_auth.this.token
}

provider "helm" {
  kubernetes {
    host                   = data.aws_eks_cluster.this.endpoint
    cluster_ca_certificate = base64decode(data.aws_eks_cluster.this.certificate_authority[0].data)
    token                  = data.aws_eks_cluster_auth.this.token
  }
}

# ----------------------------------------------------------------------------
# Default StorageClass: gp3 (the EBS CSI add-on does not set one automatically).
# ----------------------------------------------------------------------------

resource "kubernetes_storage_class_v1" "gp3" {
  metadata {
    name = "gp3"
    annotations = {
      "storageclass.kubernetes.io/is-default-class" = "true"
    }
  }

  storage_provisioner    = "ebs.csi.aws.com"
  reclaim_policy         = "Retain"
  volume_binding_mode    = "WaitForFirstConsumer"
  allow_volume_expansion = true

  parameters = {
    type   = "gp3"
    fsType = "ext4"
  }

  depends_on = [module.eks]
}

# ----------------------------------------------------------------------------
# Cluster services: ingress-nginx + cert-manager
# ----------------------------------------------------------------------------

resource "helm_release" "ingress_nginx" {
  name             = "ingress-nginx"
  namespace        = "ingress-nginx"
  create_namespace = true

  repository = "https://kubernetes.github.io/ingress-nginx"
  chart      = "ingress-nginx"
  version    = "4.11.3"

  set {
    name  = "controller.service.type"
    value = "LoadBalancer"
  }

  # Use the AWS Load Balancer Controller (NOT the legacy in-tree NLB).
  # `type=external` + `nlb-target-type=ip` makes the LBC manage the LB,
  # target pods directly (bypassing NodePort), and open the necessary
  # security-group ingress. The in-tree NLB defaults to scheme=internal AND
  # doesn't open NodePort to the public, so the external LB ends up
  # unreachable from the internet and cert-manager's HTTP-01 challenge fails.
  set {
    name  = "controller.service.annotations.service\\.beta\\.kubernetes\\.io/aws-load-balancer-type"
    value = "external"
  }

  set {
    name  = "controller.service.annotations.service\\.beta\\.kubernetes\\.io/aws-load-balancer-nlb-target-type"
    value = "ip"
  }

  set {
    name  = "controller.service.annotations.service\\.beta\\.kubernetes\\.io/aws-load-balancer-scheme"
    value = "internet-facing"
  }

  depends_on = [module.eks, kubernetes_storage_class_v1.gp3]
}

# AWS provisions the NLB asynchronously after the Service is created. Sleep
# briefly so the data source below has a hostname to read; first-apply edge
# cases are handled by the try() in outputs.tf.
resource "time_sleep" "wait_for_ingress_lb" {
  depends_on      = [helm_release.ingress_nginx]
  create_duration = "60s"
}

data "kubernetes_service" "ingress_nginx" {
  metadata {
    name      = "ingress-nginx-controller"
    namespace = "ingress-nginx"
  }

  depends_on = [time_sleep.wait_for_ingress_lb]
}

resource "helm_release" "cert_manager" {
  name             = "cert-manager"
  namespace        = "cert-manager"
  create_namespace = true

  repository = "https://charts.jetstack.io"
  chart      = "cert-manager"
  version    = "v1.15.3"

  set {
    name  = "crds.enabled"
    value = "true"
  }

  depends_on = [module.eks]
}

# Optional: a Let's Encrypt ClusterIssuer wired up if cert_manager_email is set.
# Kept inline so the user gets a working ACME issuer out of the box.
resource "kubernetes_manifest" "letsencrypt_issuer" {
  count = var.cert_manager_email != "" ? 1 : 0

  manifest = {
    apiVersion = "cert-manager.io/v1"
    kind       = "ClusterIssuer"
    metadata = {
      name = "letsencrypt-prod"
    }
    spec = {
      acme = {
        server = "https://acme-v02.api.letsencrypt.org/directory"
        email  = var.cert_manager_email
        privateKeySecretRef = {
          name = "letsencrypt-prod"
        }
        solvers = [
          {
            http01 = {
              ingress = {
                class = "nginx"
              }
            }
          },
        ]
      }
    }
  }

  depends_on = [helm_release.cert_manager]
}

# ----------------------------------------------------------------------------
# Monitoring — kube-prometheus-stack (Prometheus + Grafana)
#
# Optional. Set monitoring_enabled = true in your tfvars to install.
# Installs Prometheus Operator, Prometheus, and Grafana into the agent-wiki
# namespace. Prometheus scrapes the backend via the ServiceMonitor defined in
# the agent-workspace Helm chart (enable monitoring.serviceMonitor in values).
#
# Prerequisites before running `terraform apply` with monitoring_enabled = true:
#   1. Create the grafana-oauth-secret if using OAuth:
#      kubectl -n agent-wiki create secret generic grafana-oauth-secret \
#        --from-literal=GF_AUTH_GOOGLE_CLIENT_ID=<id> \
#        --from-literal=GF_AUTH_GOOGLE_CLIENT_SECRET=<secret>
#   2. Set root_url in monitoring-values.yaml to match your domain.
# ----------------------------------------------------------------------------

resource "helm_release" "monitoring" {
  count = var.monitoring_enabled ? 1 : 0

  name             = "agent-wiki-monitoring"
  repository       = "https://prometheus-community.github.io/helm-charts"
  chart            = "kube-prometheus-stack"
  version          = "82.10.5"
  namespace        = "agent-wiki"
  create_namespace = false

  values = [file("${path.module}/monitoring-values.yaml")]

  depends_on = [module.eks]
}
