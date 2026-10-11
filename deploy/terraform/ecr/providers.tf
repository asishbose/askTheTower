terraform {
  required_version = ">= 1.9.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = ">= 6.20.0, < 7.0.0"
    }
  }
}

provider "aws" {
  region = var.region

  default_tags {
    tags = merge(
      {
        project     = "ask-the-tower"
        environment = var.environment
        stack       = "ecr"
        managed_by  = "terraform"
      },
      var.tags,
    )
  }
}
