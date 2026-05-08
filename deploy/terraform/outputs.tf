output "cluster_name" {
  value = module.eks.cluster_name
}

output "cluster_endpoint" {
  value = module.eks.cluster_endpoint
}

output "region" {
  value = var.region
}

output "kubeconfig_command" {
  description = "Run this to point kubectl at the new cluster."
  value       = "aws eks update-kubeconfig --name ${module.eks.cluster_name} --region ${var.region}"
}

output "ingress_lb_hostname" {
  description = "Public NLB hostname for the ingress-nginx LoadBalancer Service. Create a CNAME pointing your app's host (e.g. dev-wiki.onyx.app) at this. Empty string on a fresh apply if AWS hasn't finished provisioning yet — re-run `terraform refresh` or use the kubectl fallback below."
  value       = try(data.kubernetes_service.ingress_nginx.status[0].load_balancer[0].ingress[0].hostname, "")
}

output "ingress_hostname_command" {
  description = "Fallback: kubectl one-liner to read the NLB hostname directly. Useful if `ingress_lb_hostname` came back empty on the first apply."
  value       = "kubectl -n ingress-nginx get svc ingress-nginx-controller -o jsonpath='{.status.loadBalancer.ingress[0].hostname}'"
}
