# Remote state: S3 with native lockfile locking (no DynamoDB lock table needed on Terraform >= 1.10; on 1.9
# keep `dynamodb_table`). The values below are PLACEHOLDERS — the bucket is created once, by hand, outside this
# configuration (state must not live in the stack it describes):
#
#   aws s3api create-bucket --bucket <your-state-bucket> --region <region> \
#     --create-bucket-configuration LocationConstraint=<region>        # omit for us-east-1
#   aws s3api put-bucket-versioning --bucket <your-state-bucket> --versioning-configuration Status=Enabled
#   aws s3api put-bucket-encryption --bucket <your-state-bucket> \
#     --server-side-encryption-configuration '{"Rules":[{"ApplyServerSideEncryptionByDefault":{"SSEAlgorithm":"aws:kms"}}]}'
#   aws s3api put-public-access-block --bucket <your-state-bucket> --public-access-block-configuration \
#     BlockPublicAcls=true,IgnorePublicAcls=true,BlockPublicPolicy=true,RestrictPublicBuckets=true
#   aws dynamodb create-table --table-name <your-lock-table> --billing-mode PAY_PER_REQUEST \
#     --attribute-definitions AttributeName=LockID,AttributeType=S --key-schema AttributeName=LockID,KeyType=HASH
#
# Then either edit the placeholders below or override them at init time without editing the file:
#
#   terraform -chdir=deploy/terraform init \
#     -backend-config="bucket=<your-state-bucket>" -backend-config="region=<region>" \
#     -backend-config="dynamodb_table=<your-lock-table>"
#
# State holds generated secrets (the mock carrier's client secrets, which AgentCore Identity also receives), so the
# bucket is private, versioned and KMS-encrypted, and state is never committed (`*.tfstate` is gitignored).
# `terraform validate` in CI runs with `init -backend=false` and never touches this backend.
terraform {
  backend "s3" {
    bucket         = "REPLACE-ME-ask-the-tower-tfstate"
    key            = "ask-the-tower/aws/terraform.tfstate"
    region         = "us-east-1"
    encrypt        = true
    dynamodb_table = "REPLACE-ME-ask-the-tower-tflock"
  }
}
