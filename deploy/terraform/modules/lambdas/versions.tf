terraform {
  required_providers {
    aws = {
      source                = "hashicorp/aws"
      configuration_aliases = [aws.us_east_1]
    }
    archive = {
      # zips admin_gate/handler.py (the demo admin path's authorizer, 10 §2.1)
      source = "hashicorp/archive"
    }
  }
}
