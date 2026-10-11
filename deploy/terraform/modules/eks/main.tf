# EKS portability target (prompt 14): the same five images that run on compose and on AgentCore/Fargate/Lambda,
# installed from Helm onto one small cluster, against 13's DynamoDB tables, KMS keys and SNS topic.
#
#   control plane   EKS (var.cluster_version), API auth mode (access entries), API/audit/authenticator logs to
#                   CloudWatch (log group created first, so its retention applies)
#   nodes           one managed node group: 2 x t4g.medium (arm64, AL2023), ON_DEMAND
#   addons          vpc-cni, kube-proxy, coredns; amazon-cloudwatch-observability when enable_container_logs
#   IRSA            OIDC provider + one role per service account (irsa.tf)
#
# Networking: 13's VPC has no NAT by default and its public subnets do not map public IPs on launch. The node group
# therefore uses a launch template whose network interface asks for a public IPv4 address (nodes_public_ip), so
# nodes in the public subnets reach ECR, STS and the EKS API through the internet gateway. Inbound to the nodes is
# only what the EKS cluster security group allows (the control plane and the other nodes; the AWS Load Balancer
# Controller adds the ALB -> pod rules). With 13's enable_nat_gateway = true, pass the private subnets instead.

locals {
  managed_policy = "arn:${var.partition}:iam::aws:policy"
  imdsv2_only    = "required" # http_tokens: IMDSv2 session required (a local, so the inline-secret lint stays quiet)
}

# --- control-plane logs (before the cluster, so EKS does not create the group without retention) ------------------

resource "aws_cloudwatch_log_group" "cluster" {
  name              = "/aws/eks/${var.name}/cluster"
  retention_in_days = var.log_retention_days
  tags              = var.tags
}

# --- cluster role -------------------------------------------------------------------------------------------------

resource "aws_iam_role" "cluster" {
  name = "${var.name}-cluster"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "eks.amazonaws.com" }
      Action    = ["sts:AssumeRole", "sts:TagSession"]
    }]
  })
  tags = var.tags
}

resource "aws_iam_role_policy_attachment" "cluster" {
  role       = aws_iam_role.cluster.name
  policy_arn = "${local.managed_policy}/AmazonEKSClusterPolicy"
}

# --- cluster ------------------------------------------------------------------------------------------------------

resource "aws_eks_cluster" "this" {
  name     = var.name
  version  = var.cluster_version
  role_arn = aws_iam_role.cluster.arn

  enabled_cluster_log_types = var.control_plane_log_types

  access_config {
    authentication_mode                         = "API"
    bootstrap_cluster_creator_admin_permissions = true
  }

  vpc_config {
    subnet_ids              = var.subnet_ids
    endpoint_private_access = true
    endpoint_public_access  = true # helm/kubectl from the laptop; narrow with endpoint_public_access_cidrs
    public_access_cidrs     = var.endpoint_public_access_cidrs
  }

  tags = var.tags

  depends_on = [
    aws_cloudwatch_log_group.cluster,
    aws_iam_role_policy_attachment.cluster,
  ]
}

# Extra cluster admins (the identity that ran `terraform apply` is admin through
# bootstrap_cluster_creator_admin_permissions).
resource "aws_eks_access_entry" "admin" {
  for_each      = toset(var.admin_principal_arns)
  cluster_name  = aws_eks_cluster.this.name
  principal_arn = each.value
  type          = "STANDARD"
}

resource "aws_eks_access_policy_association" "admin" {
  for_each      = aws_eks_access_entry.admin
  cluster_name  = aws_eks_cluster.this.name
  principal_arn = each.value.principal_arn
  policy_arn    = "arn:${var.partition}:eks::aws:cluster-access-policy/AmazonEKSClusterAdminPolicy"

  access_scope {
    type = "cluster"
  }
}

# --- node role ----------------------------------------------------------------------------------------------------

resource "aws_iam_role" "node" {
  name = "${var.name}-node"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "ec2.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
  tags = var.tags
}

resource "aws_iam_role_policy_attachment" "node" {
  for_each = toset([
    "AmazonEKSWorkerNodePolicy",
    "AmazonEC2ContainerRegistryReadOnly", # pull the five images from 13's ECR repositories
    "AmazonEKS_CNI_Policy",               # vpc-cni runs on the node role (host network)
  ])
  role       = aws_iam_role.node.name
  policy_arn = "${local.managed_policy}/${each.key}"
}

# --- node group ---------------------------------------------------------------------------------------------------

resource "aws_launch_template" "node" {
  name_prefix            = "${var.name}-node-"
  update_default_version = true

  block_device_mappings {
    device_name = "/dev/xvda" # AL2023 root volume
    ebs {
      volume_size           = var.node_disk_gb
      volume_type           = "gp3"
      encrypted             = true
      delete_on_termination = true
    }
  }

  # IMDSv2 only, one hop: pods (not on the host network) cannot reach the node role's credentials, so each
  # workload has exactly its IRSA role. vpc-cni and kube-proxy run on the host network and are unaffected.
  metadata_options {
    http_endpoint               = "enabled"
    http_tokens                 = local.imdsv2_only
    http_put_response_hop_limit = 1
  }

  # A public IPv4 address without touching 13's subnets (map_public_ip_on_launch = false there). Security groups
  # set here replace the ones EKS would add, so the cluster security group is named explicitly.
  network_interfaces {
    associate_public_ip_address = var.nodes_public_ip
    delete_on_termination       = true
    security_groups             = [aws_eks_cluster.this.vpc_config[0].cluster_security_group_id]
  }

  tag_specifications {
    resource_type = "instance"
    tags          = merge(var.tags, { Name = "${var.name}-node" })
  }

  tags = var.tags
}

resource "aws_eks_node_group" "default" {
  cluster_name    = aws_eks_cluster.this.name
  node_group_name = "${var.name}-default"
  node_role_arn   = aws_iam_role.node.arn
  subnet_ids      = var.subnet_ids

  ami_type       = var.node_ami_type
  capacity_type  = var.capacity_type
  instance_types = var.node_instance_types

  scaling_config {
    desired_size = var.node_desired
    min_size     = var.node_min
    max_size     = var.node_max
  }

  update_config {
    max_unavailable = 1
  }

  launch_template {
    id      = aws_launch_template.node.id
    version = aws_launch_template.node.latest_version
  }

  tags = var.tags

  depends_on = [
    aws_iam_role_policy_attachment.node,
    aws_eks_addon.pre_nodes, # nodes become Ready only once the CNI is there
  ]
}

# --- addons -------------------------------------------------------------------------------------------------------
# No addon_version: EKS picks the default version for the cluster version. OVERWRITE adopts the self-managed copies
# EKS bootstraps with the cluster.

resource "aws_eks_addon" "pre_nodes" {
  for_each = toset(["vpc-cni", "kube-proxy"])

  cluster_name = aws_eks_cluster.this.name
  addon_name   = each.key
  # The charts ship NetworkPolicies; the VPC CNI's network-policy agent enforces them (off by default).
  configuration_values        = each.key == "vpc-cni" ? jsonencode({ enableNetworkPolicy = "true" }) : null
  resolve_conflicts_on_create = "OVERWRITE"
  resolve_conflicts_on_update = "OVERWRITE"
  tags                        = var.tags
}

resource "aws_eks_addon" "coredns" {
  cluster_name                = aws_eks_cluster.this.name
  addon_name                  = "coredns"
  resolve_conflicts_on_create = "OVERWRITE"
  resolve_conflicts_on_update = "OVERWRITE"
  tags                        = var.tags

  depends_on = [aws_eks_node_group.default] # a Deployment: needs nodes to become ACTIVE
}

resource "aws_eks_addon" "cloudwatch" {
  count = var.enable_container_logs ? 1 : 0

  cluster_name                = aws_eks_cluster.this.name
  addon_name                  = "amazon-cloudwatch-observability"
  service_account_role_arn    = aws_iam_role.irsa["cloudwatch-agent"].arn
  resolve_conflicts_on_create = "OVERWRITE"
  resolve_conflicts_on_update = "OVERWRITE"
  tags                        = var.tags

  depends_on = [aws_eks_node_group.default]
}
