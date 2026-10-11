terraform {
  # 1.9 is what the build machine has; write-only (`*_wo`) attributes would need 1.11, so the carrier client
  # secrets handed to AgentCore Identity sit in (encrypted, never committed) state instead. See README.md.
  required_version = ">= 1.9.0"

  required_providers {
    aws = {
      source = "hashicorp/aws"
      # 6.x carries the native `aws_bedrockagentcore_*` resources (Runtime, Gateway, targets, Identity).
      version = ">= 6.20.0, < 7.0.0"
    }
    random = {
      source  = "hashicorp/random"
      version = "~> 3.6"
    }
    archive = {
      # zips the web chat proxy (services/web-chat/proxy/handler.py) for modules/web_chat
      source  = "hashicorp/archive"
      version = "~> 2.4"
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
        managed_by  = "terraform"
      },
      var.tags,
    )
  }
}

# WAF web ACLs for CloudFront must live in us-east-1 whatever the main region is.
provider "aws" {
  alias  = "us_east_1"
  region = "us-east-1"

  default_tags {
    tags = merge(
      {
        project     = "ask-the-tower"
        environment = var.environment
        managed_by  = "terraform"
      },
      var.tags,
    )
  }
}
