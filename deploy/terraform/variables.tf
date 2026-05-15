variable "name" {
  type        = string
  description = "Base name used to derive cluster, VPC, and other resource names. Override per-deploy."
  default     = "agent-wiki"
}

variable "region" {
  type        = string
  description = "AWS region to deploy into."
  default     = "us-west-2"
}

variable "environment" {
  type        = string
  description = "Environment suffix (dev, staging, prod). Appended to resource names."
  default     = "dev"
}

variable "cluster_version" {
  type        = string
  description = "EKS control plane version."
  default     = "1.30"
}

variable "node_instance_types" {
  type        = list(string)
  description = "EC2 instance types for the managed node group. t3.large supports 35 pods/node — t3.medium's 17-pod cap couldn't fit cluster services + the chart's backend + worker + frontend with headroom."
  default     = ["t3.large"]
}

variable "node_min_size" {
  type    = number
  default = 1
}

variable "node_max_size" {
  type    = number
  default = 3
}

variable "node_desired_size" {
  type    = number
  default = 2
}

variable "cluster_endpoint_public_access_cidrs" {
  type        = list(string)
  description = "CIDRs allowed to reach the EKS control plane. Defaults to the world; tighten for production."
  default     = ["0.0.0.0/0"]
}

variable "cert_manager_email" {
  type        = string
  description = "Email used by Let's Encrypt for ACME certificate registration. Required if you want auto-issued TLS via the bundled ClusterIssuer."
  default     = ""
}

variable "monitoring_enabled" {
  type        = bool
  description = "Install kube-prometheus-stack (Prometheus + Grafana) into the agent-wiki namespace."
  default     = false
}

variable "tags" {
  type        = map(string)
  description = "Default tags applied to all AWS resources."
  default     = {}
}
