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
  # Local state for the hackathon sandbox: the account's service control policy denies s3:CreateBucket and
  # dynamodb:CreateTable, so the S3 backend documented above cannot be bootstrapped there. State lives in
  # this directory (`*.tfstate` is gitignored); one operator, no locking needed. To move back to S3 on an
  # unrestricted account: restore the "s3" block from the header comment and run `terraform init -migrate-state`.
  backend "local" {
    path = "terraform-ecr.tfstate"
  }
}
