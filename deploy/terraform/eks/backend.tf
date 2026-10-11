# Remote state for the EKS root: the SAME bucket (and lock table) as 13's root, a different key, so
# `make down-eks` (terraform destroy here) can never touch 13's stack. The values are PLACEHOLDERS; the bucket is
# created once by hand (commands in ../backend.tf). Override at init time without editing the file:
#
#   terraform -chdir=deploy/terraform/eks init \
#     -backend-config="bucket=<your-state-bucket>" -backend-config="region=<region>" \
#     -backend-config="dynamodb_table=<your-lock-table>"
#
# `terraform validate` in CI runs with `init -backend=false` and never touches this backend.
terraform {
  backend "s3" {
    bucket         = "REPLACE-ME-ask-the-tower-tfstate"
    key            = "ask-the-tower/eks/terraform.tfstate"
    region         = "us-east-1"
    encrypt        = true
    dynamodb_table = "REPLACE-ME-ask-the-tower-tflock"
  }
}
