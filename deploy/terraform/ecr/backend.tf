# Remote state for the ECR root: the same bucket and lock table as the main root (see ../backend.tf for the
# bootstrap commands), its own key. This root is applied once (`make ecr-up`) and destroyed only by `make down-all`,
# so `make down ENV=aws` (the main root) never deletes the repositories or the images in them.
#
#   terraform -chdir=deploy/terraform/ecr init \
#     -backend-config="bucket=<your-state-bucket>" -backend-config="region=<region>" \
#     -backend-config="dynamodb_table=<your-lock-table>"
#
# Nothing in this state is secret. `terraform validate` in CI runs with `init -backend=false`.
terraform {
  backend "s3" {
    bucket         = "REPLACE-ME-ask-the-tower-tfstate"
    key            = "ask-the-tower/ecr/terraform.tfstate"
    region         = "us-east-1"
    encrypt        = true
    dynamodb_table = "REPLACE-ME-ask-the-tower-tflock"
  }
}
