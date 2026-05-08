# Copy to terraform.tfvars (which is gitignored) and edit.
name        = "agent-workspace"
environment = "dev"
region      = "us-west-2"

# Required for the bundled letsencrypt-prod ClusterIssuer. Leave empty to skip
# (you can install your own issuer later).
cert_manager_email = ""

# Tighten control plane access for production deploys.
# cluster_endpoint_public_access_cidrs = ["1.2.3.4/32"]

# Bigger/smaller nodes if needed.
# node_instance_types = ["t3.large"]
