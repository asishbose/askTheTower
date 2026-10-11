terraform {
  required_version = ">= 1.9.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = ">= 6.20.0, < 7.0.0"
    }
    helm = {
      source = "hashicorp/helm"
      # 2.x: the `kubernetes { ... }` block syntax below (3.x turned it into an attribute).
      version = "~> 2.17"
    }
  }
}

provider "aws" {
  region = var.region

  default_tags {
    tags = merge(
      {
        project    = "ask-the-tower"
        stack      = "eks"
        managed_by = "terraform"
      },
      var.tags,
    )
  }
}

# Helm talks to the cluster this root creates. The bearer comes from `aws eks get-token` at run time (the AWS CLI
# must be on PATH with the same credentials), so no kubeconfig and nothing long-lived lands in state.
provider "helm" {
  kubernetes {
    host                   = module.eks.cluster_endpoint
    cluster_ca_certificate = base64decode(module.eks.cluster_certificate_authority_data)

    exec {
      api_version = "client.authentication.k8s.io/v1beta1"
      command     = "aws"
      args        = ["eks", "get-token", "--cluster-name", module.eks.cluster_name, "--region", var.region]
    }
  }
}
