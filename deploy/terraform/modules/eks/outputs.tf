output "cluster_name" {
  value = aws_eks_cluster.this.name
}

output "cluster_endpoint" {
  value = aws_eks_cluster.this.endpoint
}

output "cluster_certificate_authority_data" {
  description = "Base64 CA bundle of the API server (helm/kubernetes providers, kubeconfig)."
  value       = aws_eks_cluster.this.certificate_authority[0].data
}

output "cluster_arn" {
  value = aws_eks_cluster.this.arn
}

output "cluster_security_group_id" {
  description = "The EKS-created cluster security group (control plane <-> nodes; the nodes' only group)."
  value       = aws_eks_cluster.this.vpc_config[0].cluster_security_group_id
}

output "oidc_provider_arn" {
  value = aws_iam_openid_connect_provider.this.arn
}

output "node_role_arn" {
  value = aws_iam_role.node.arn
}

output "irsa_role_arns" {
  description = "Service account -> IRSA role ARN (eks.amazonaws.com/role-arn annotation)."
  value = {
    for k in ["tower-mcp", "binding-page", "alerts", "ref-client", "aws-load-balancer-controller"] : k => aws_iam_role.irsa[k].arn
  }
}

output "irsa_policies" {
  description = "Service -> rendered inline policy JSON (asserted by tests/irsa.tftest.hcl)."
  value       = { for k, p in local.irsa_policies : k => jsonencode(p) }
}

output "irsa_trust_policies" {
  description = "Service account -> rendered trust policy JSON (asserted by tests/irsa.tftest.hcl)."
  value       = local.irsa_trust
}
