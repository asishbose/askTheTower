# Keys for the consent store (04 §4, 10 §2).
#
# 10 §2 says "one CMK; rotation on". KMS cannot do both jobs with one key: `line_id` is HMAC-SHA256 computed
# inside KMS (GenerateMac), which needs a key of spec HMAC_256, while `msisdn_enc` is an envelope under a
# SYMMETRIC_DEFAULT key. So there are two keys, one per job, and only the symmetric one can rotate (KMS does not
# rotate HMAC keys automatically; rotating it would change every line_id anyway, which is a re-key, not a rotation).
# docs/architecture/components/10 §2 records this.

data "aws_iam_policy_document" "key" {
  statement {
    sid       = "AccountAdmin"
    actions   = ["kms:*"]
    resources = ["*"]
    principals {
      type        = "AWS"
      identifiers = ["arn:${var.partition}:iam::${var.account_id}:root"]
    }
  }

  dynamic "statement" {
    for_each = length(var.user_role_arns) > 0 ? [1] : []
    content {
      sid       = "ServiceRolesUse"
      actions   = ["kms:Encrypt", "kms:Decrypt", "kms:GenerateDataKey", "kms:GenerateDataKey*", "kms:DescribeKey"]
      resources = ["*"]
      principals {
        type        = "AWS"
        identifiers = var.user_role_arns
      }
    }
  }

  dynamic "statement" {
    for_each = length(var.service_principals) > 0 ? [1] : []
    content {
      sid       = "AwsServicesUse"
      actions   = ["kms:Encrypt", "kms:Decrypt", "kms:ReEncrypt*", "kms:GenerateDataKey*", "kms:DescribeKey"]
      resources = ["*"]
      principals {
        type        = "Service"
        identifiers = var.service_principals
      }
    }
  }
}

data "aws_iam_policy_document" "hmac" {
  statement {
    sid       = "AccountAdmin"
    actions   = ["kms:*"]
    resources = ["*"]
    principals {
      type        = "AWS"
      identifiers = ["arn:${var.partition}:iam::${var.account_id}:root"]
    }
  }

  dynamic "statement" {
    for_each = length(var.user_role_arns) > 0 ? [1] : []
    content {
      sid       = "ServiceRolesMac"
      actions   = ["kms:GenerateMac", "kms:VerifyMac", "kms:DescribeKey"]
      resources = ["*"]
      principals {
        type        = "AWS"
        identifiers = var.user_role_arns
      }
    }
  }
}

resource "aws_kms_key" "main" {
  description              = "${var.name}: msisdn_enc envelope key, DynamoDB/SNS/logs encryption"
  key_usage                = "ENCRYPT_DECRYPT"
  customer_master_key_spec = "SYMMETRIC_DEFAULT"
  enable_key_rotation      = true
  deletion_window_in_days  = var.deletion_window_in_days
  policy                   = data.aws_iam_policy_document.key.json
}

resource "aws_kms_alias" "main" {
  name          = "alias/${var.name}-main"
  target_key_id = aws_kms_key.main.key_id
}

resource "aws_kms_key" "hmac" {
  description              = "${var.name}: line_id HMAC (GenerateMac) and audit trim-marker signatures"
  key_usage                = "GENERATE_VERIFY_MAC"
  customer_master_key_spec = "HMAC_256"
  deletion_window_in_days  = var.deletion_window_in_days
  policy                   = data.aws_iam_policy_document.hmac.json
}

resource "aws_kms_alias" "hmac" {
  name          = "alias/${var.name}-line-id-hmac"
  target_key_id = aws_kms_key.hmac.key_id
}
