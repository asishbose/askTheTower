# IRSA: one IAM role per Kubernetes service account, trusted only for that exact `system:serviceaccount:<ns>:<sa>`
# and audience sts.amazonaws.com. Service-account names are fixed and equal to the chart names.
#
# The app policies are plain objects in local.irsa_policies, rendered with jsonencode() (not
# aws_iam_policy_document), so `terraform test` with a mock provider can assert the rendered JSON
# (tests/irsa.tftest.hcl) and tests/aws/test_eks_terraform.py can grep this file. Least privilege mirrors
# modules/lambdas and modules/agentcore_runtime in 13: every Resource is an ARN from 13's outputs (tables and their
# GSIs, the two KMS keys, the SNS topics, one Bedrock model), with ONE exception, commented below.

resource "aws_iam_openid_connect_provider" "this" {
  url            = aws_eks_cluster.this.identity[0].oidc[0].issuer
  client_id_list = ["sts.amazonaws.com"]
  # thumbprint_list omitted: optional on aws provider 6.x; IAM verifies the EKS issuer against its trusted CAs.
  tags = var.tags
}

locals {
  oidc_host = trimprefix(aws_eks_cluster.this.identity[0].oidc[0].issuer, "https://")

  service_accounts = merge(
    {
      "tower-mcp"                    = { namespace = var.namespace, name = "tower-mcp" }
      "binding-page"                 = { namespace = var.namespace, name = "binding-page" }
      "alerts"                       = { namespace = var.namespace, name = "alerts" }
      "ref-client"                   = { namespace = var.namespace, name = "ref-client" }
      "aws-load-balancer-controller" = { namespace = "kube-system", name = "aws-load-balancer-controller" }
    },
    var.enable_container_logs ? {
      "cloudwatch-agent" = { namespace = "amazon-cloudwatch", name = "cloudwatch-agent" }
    } : {},
  )

  # --- DynamoDB: the consent store (04 §4) for Tower and the binding page; plus AlertsState for Alerts ---------
  consent_tables = ["Users", "Lines", "Grants", "Watches", "Audit", "BindTokens"]
  data_tables = {
    # Tower: resolve/consent/audit/watch registration across the six consent tables.
    "tower-mcp" = local.consent_tables
    # Binding page: bind tokens, lines, users, grants, audit view, and the admin view's scan_all over every consent
    # table (Watches included), so it keeps the same six as Tower rather than a narrower set.
    "binding-page" = local.consent_tables
    # Alerts: the six plus its own escalation state.
    "alerts" = concat(local.consent_tables, ["AlertsState"])
  }
  dynamodb_actions = [
    "dynamodb:GetItem", "dynamodb:PutItem", "dynamodb:UpdateItem", "dynamodb:DeleteItem", "dynamodb:Query",
    "dynamodb:ConditionCheckItem", "dynamodb:TransactWriteItems", "dynamodb:BatchGetItem",
    # Scan: tower_consent.store.scan_all (admin/privacy views) and alerts.state's sweep. Still table-scoped.
    "dynamodb:Scan",
  ]
  table_resources = {
    for svc, names in local.data_tables : svc => flatten([
      for n in names : [var.table_arns[n], "${var.table_arns[n]}/index/*"]
    ])
  }

  # --- KMS: envelope key (msisdn_enc) and HMAC key (line_id) ------------------------------------------------------
  kms_statements = [
    {
      Sid      = "EnvelopeKey"
      Effect   = "Allow"
      Action   = ["kms:Encrypt", "kms:Decrypt", "kms:GenerateDataKey", "kms:DescribeKey"]
      Resource = [var.kms_key_arn]
    },
    {
      Sid      = "LineIdMac"
      Effect   = "Allow"
      Action   = ["kms:GenerateMac", "kms:VerifyMac", "kms:DescribeKey"]
      Resource = [var.kms_hmac_key_arn]
    },
  ]

  # --- Bedrock: one model for the reference client ---------------------------------------------------------------
  # A geo prefix ("us.", "eu.", "apac.", ...) names a cross-region inference profile: allow the profile and the
  # underlying foundation model in var.region. (A profile may route to other regions; if a call is denied there,
  # add those regions' foundation-model ARNs — unverified until the first EKS run.)
  bedrock_geo        = can(regex("^(us|us-gov|eu|apac|jp|au|ca|global)\\.", var.bedrock_model_id))
  bedrock_base_model = local.bedrock_geo ? join(".", slice(split(".", var.bedrock_model_id), 1, length(split(".", var.bedrock_model_id)))) : var.bedrock_model_id
  bedrock_resources = concat(
    ["arn:${var.partition}:bedrock:${var.region}::foundation-model/${local.bedrock_base_model}"],
    local.bedrock_geo ? ["arn:${var.partition}:bedrock:${var.region}:${var.account_id}:inference-profile/${var.bedrock_model_id}"] : [],
  )

  consent_store_statement = {
    for svc in keys(local.data_tables) : svc => {
      Sid      = "ConsentStore"
      Effect   = "Allow"
      Action   = local.dynamodb_actions
      Resource = local.table_resources[svc]
    }
  }

  irsa_policies = {
    "tower-mcp" = {
      Version   = "2012-10-17"
      Statement = concat([local.consent_store_statement["tower-mcp"]], local.kms_statements)
    }
    "binding-page" = {
      Version   = "2012-10-17"
      Statement = concat([local.consent_store_statement["binding-page"]], local.kms_statements)
    }
    "alerts" = {
      Version = "2012-10-17"
      Statement = concat(
        [local.consent_store_statement["alerts"]],
        local.kms_statements,
        length(var.sns_topic_arns) > 0 ? [{
          Sid      = "Topics"
          Effect   = "Allow"
          Action   = ["sns:Publish"]
          Resource = var.sns_topic_arns
        }] : [],
        [{
          # THE one wildcard in these four policies: an SMS published to a phone number (PhoneNumber=...) has no
          # resource ARN, so IAM can only express it as "*" — the same statement as 13's alerts Lambda.
          Sid      = "SmsToPhoneNumber"
          Effect   = "Allow"
          Action   = ["sns:Publish"]
          Resource = ["*"]
        }],
      )
    }
    "ref-client" = {
      Version = "2012-10-17"
      Statement = [{
        Sid      = "InvokeModel"
        Effect   = "Allow"
        Action   = ["bedrock:InvokeModel", "bedrock:InvokeModelWithResponseStream"]
        Resource = local.bedrock_resources
      }]
    }
  }
}

locals {
  # Rendered here (not inline in the role) so the test can read it at plan time.
  irsa_trust = {
    for k, sa in local.service_accounts : k => jsonencode({
      Version = "2012-10-17"
      Statement = [{
        Effect    = "Allow"
        Principal = { Federated = aws_iam_openid_connect_provider.this.arn }
        Action    = "sts:AssumeRoleWithWebIdentity"
        Condition = {
          StringEquals = {
            "${local.oidc_host}:sub" = "system:serviceaccount:${sa.namespace}:${sa.name}"
            "${local.oidc_host}:aud" = "sts.amazonaws.com"
          }
        }
      }]
    })
  }
}

resource "aws_iam_role" "irsa" {
  for_each = local.service_accounts

  name               = "${var.name}-${each.key}"
  assume_role_policy = local.irsa_trust[each.key]
  tags               = var.tags
}

resource "aws_iam_role_policy" "irsa" {
  for_each = local.irsa_policies

  name   = "app"
  role   = aws_iam_role.irsa[each.key].id
  policy = jsonencode(each.value)
}

# AWS Load Balancer Controller: the upstream v2.11.0 policy, verbatim (docs/install/iam_policy.json), with the
# partition substituted. It uses "*" resources by design (it creates and tags ALBs, target groups and security
# groups whose ARNs do not exist yet, scoped by the elbv2.k8s.aws/cluster tag conditions). It is not one of our
# services, so it is exempt from the least-privilege test; keep it in step with the chart version in ../../eks.
resource "aws_iam_policy" "lb_controller" {
  name   = "${var.name}-aws-load-balancer-controller"
  policy = replace(file("${path.module}/lb-controller-iam-policy.json"), "arn:aws:", "arn:${var.partition}:")
  tags   = var.tags
}

resource "aws_iam_role_policy_attachment" "lb_controller" {
  role       = aws_iam_role.irsa["aws-load-balancer-controller"].name
  policy_arn = aws_iam_policy.lb_controller.arn
}

# Container logs addon (optional): the CloudWatch agent / Fluent Bit write to CloudWatch with the AWS managed policy.
resource "aws_iam_role_policy_attachment" "cloudwatch_agent" {
  count      = var.enable_container_logs ? 1 : 0
  role       = aws_iam_role.irsa["cloudwatch-agent"].name
  policy_arn = "${local.managed_policy}/CloudWatchAgentServerPolicy"
}
