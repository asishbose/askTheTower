# IRSA least privilege, asserted on the rendered JSON (prompt 14 step 4). Offline: a mock AWS provider, no
# credentials, no API calls. Run with `terraform -chdir=deploy/terraform/modules/eks init -backend=false` then
# `terraform -chdir=deploy/terraform/modules/eks test` (tests/aws/test_eks_terraform.py does both).
#
# The ARNs are obviously fake (account "fake-acct", or "aws" where the provider validates the account field);
# no account-id-shaped digit runs.

mock_provider "aws" {
  # The trust-policy run applies against the mock (nothing real is created), and apply-time validation wants
  # ARN-shaped values; the account field also accepts the literal "aws", which keeps digit runs out of the file.
  mock_resource "aws_iam_role" {
    defaults = { arn = "arn:aws:iam::aws:role/mock" }
  }
  mock_resource "aws_iam_policy" {
    defaults = { arn = "arn:aws:iam::aws:policy/mock" }
  }
  mock_resource "aws_eks_cluster" {
    defaults = { arn = "arn:aws:eks:us-east-1:aws:cluster/mock" }
  }
  mock_resource "aws_launch_template" {
    defaults = { id = "lt-mock" }
  }
  # The trust policies embed the issuer host, so pin it instead of a random mock string.
  override_resource {
    target = aws_eks_cluster.this
    values = {
      identity              = [{ oidc = [{ issuer = "https://oidc.eks.us-east-1.amazonaws.com/id/FAKEISSUER" }] }]
      certificate_authority = [{ data = "ZmFrZQ==" }]
    }
  }
  override_resource {
    target = aws_iam_openid_connect_provider.this
    values = {
      arn = "arn:aws:iam::aws:oidc-provider/oidc.eks.us-east-1.amazonaws.com/id/FAKEISSUER"
    }
  }
}

variables {
  name       = "att-test-eks"
  region     = "us-east-1"
  account_id = "fake-acct"
  partition  = "aws"
  vpc_id     = "vpc-fake"
  subnet_ids = ["subnet-fake-a", "subnet-fake-b"]
  namespace  = "ask-the-tower"
  table_arns = {
    Users       = "arn:aws:dynamodb:us-east-1:fake-acct:table/att-test-Users"
    Lines       = "arn:aws:dynamodb:us-east-1:fake-acct:table/att-test-Lines"
    Grants      = "arn:aws:dynamodb:us-east-1:fake-acct:table/att-test-Grants"
    Watches     = "arn:aws:dynamodb:us-east-1:fake-acct:table/att-test-Watches"
    Audit       = "arn:aws:dynamodb:us-east-1:fake-acct:table/att-test-Audit"
    BindTokens  = "arn:aws:dynamodb:us-east-1:fake-acct:table/att-test-BindTokens"
    AlertsState = "arn:aws:dynamodb:us-east-1:fake-acct:table/att-test-AlertsState"
  }
  kms_key_arn      = "arn:aws:kms:us-east-1:fake-acct:key/fake-envelope"
  kms_hmac_key_arn = "arn:aws:kms:us-east-1:fake-acct:key/fake-hmac"
  sns_topic_arns   = ["arn:aws:sns:us-east-1:fake-acct:att-test-sms-replies"]
  bedrock_model_id = "amazon.nova-micro-v1:0"
}

run "policies_are_least_privilege" {
  command = plan

  # Every Allow statement of the four app roles names only input ARNs (or a table's /index/*), except the one
  # SmsToPhoneNumber statement of alerts.
  assert {
    condition = alltrue(flatten([
      for svc in ["tower-mcp", "binding-page", "alerts", "ref-client"] : [
        for s in jsondecode(output.irsa_policies[svc]).Statement : [
          for r in s.Resource : s.Sid == "SmsToPhoneNumber" || contains(concat(
            values(var.table_arns),
            [for a in values(var.table_arns) : "${a}/index/*"],
            [var.kms_key_arn, var.kms_hmac_key_arn],
            var.sns_topic_arns,
            ["arn:aws:bedrock:us-east-1::foundation-model/amazon.nova-micro-v1:0"],
          ), r)
        ]
      ]
    ]))
    error_message = "an IRSA policy names a resource that is not one of 13's ARNs"
  }

  assert {
    condition = length(flatten([
      for svc in ["tower-mcp", "binding-page", "alerts", "ref-client"] : [
        for s in jsondecode(output.irsa_policies[svc]).Statement : s.Sid if contains(s.Resource, "*") || anytrue([for r in s.Resource : strcontains(r, "*") && !endswith(r, "/index/*")])
      ]
    ])) == 1
    error_message = "exactly one wildcard statement is allowed (alerts SmsToPhoneNumber)"
  }

  assert {
    condition = alltrue([
      for s in jsondecode(output.irsa_policies["alerts"]).Statement :
      s.Action == ["sns:Publish"] if s.Sid == "SmsToPhoneNumber"
    ]) && length([for s in jsondecode(output.irsa_policies["alerts"]).Statement : s if s.Sid == "SmsToPhoneNumber"]) == 1
    error_message = "alerts must carry exactly one SmsToPhoneNumber statement, sns:Publish only"
  }

  assert {
    condition = alltrue(flatten([
      for svc in ["tower-mcp", "binding-page", "alerts", "ref-client"] : [
        for s in jsondecode(output.irsa_policies[svc]).Statement : s.Effect == "Allow"
      ]
    ]))
    error_message = "only Allow statements are expected"
  }

  # ref-client: Bedrock only, one model.
  assert {
    condition = alltrue(flatten([
      for s in jsondecode(output.irsa_policies["ref-client"]).Statement : [for a in s.Action : startswith(a, "bedrock:")]
    ]))
    error_message = "ref-client may only call Bedrock"
  }
  assert {
    condition = flatten([for s in jsondecode(output.irsa_policies["ref-client"]).Statement : s.Resource]) == [
      "arn:aws:bedrock:us-east-1::foundation-model/amazon.nova-micro-v1:0"
    ]
    error_message = "ref-client must be scoped to the one foundation model"
  }

  # tower-mcp and binding-page: DynamoDB + KMS only (no SNS, no Bedrock), and no AlertsState.
  assert {
    condition = alltrue(flatten([
      for svc in ["tower-mcp", "binding-page"] : [
        for s in jsondecode(output.irsa_policies[svc]).Statement : [
          for a in s.Action : startswith(a, "dynamodb:") || startswith(a, "kms:")
        ]
      ]
    ]))
    error_message = "tower-mcp / binding-page may only use DynamoDB and KMS"
  }
  assert {
    condition = !anytrue(flatten([
      for svc in ["tower-mcp", "binding-page"] : [
        for s in jsondecode(output.irsa_policies[svc]).Statement : [for r in s.Resource : strcontains(r, "AlertsState")]
      ]
    ]))
    error_message = "only alerts may touch AlertsState"
  }

  # alerts: all seven tables, its SNS topic.
  assert {
    condition = length(setsubtract(
      toset(values(var.table_arns)),
      toset(flatten([for s in jsondecode(output.irsa_policies["alerts"]).Statement : s.Resource])),
    )) == 0
    error_message = "alerts needs all seven tables"
  }
  assert {
    condition     = contains(flatten([for s in jsondecode(output.irsa_policies["alerts"]).Statement : s.Resource]), var.sns_topic_arns[0])
    error_message = "alerts needs the SNS topic"
  }

  # KMS actions stay key-scoped: the HMAC key never gets Decrypt, the envelope key never gets GenerateMac.
  assert {
    condition = alltrue(flatten([
      for svc in ["tower-mcp", "binding-page", "alerts"] : [
        for s in jsondecode(output.irsa_policies[svc]).Statement : (
          s.Resource == [var.kms_hmac_key_arn] ? alltrue([for a in s.Action : contains(["kms:GenerateMac", "kms:VerifyMac", "kms:DescribeKey"], a)]) :
          s.Resource == [var.kms_key_arn] ? alltrue([for a in s.Action : contains(["kms:Encrypt", "kms:Decrypt", "kms:GenerateDataKey", "kms:DescribeKey"], a)]) :
          true
        )
      ]
    ]))
    error_message = "KMS actions must match the key's job"
  }

  # The node group: 2 x t4g.medium, arm64, on-demand.
  assert {
    condition     = aws_eks_node_group.default.instance_types == tolist(["t4g.medium"]) && aws_eks_node_group.default.capacity_type == "ON_DEMAND" && aws_eks_node_group.default.ami_type == "AL2023_ARM_64_STANDARD"
    error_message = "node group must be t4g.medium / ON_DEMAND / AL2023 arm64"
  }
  assert {
    condition     = aws_eks_node_group.default.scaling_config[0].desired_size == 2
    error_message = "two nodes"
  }

  # The charts' NetworkPolicies are enforced by the VPC CNI's network-policy agent.
  assert {
    condition     = jsondecode(aws_eks_addon.pre_nodes["vpc-cni"].configuration_values).enableNetworkPolicy == "true"
    error_message = "vpc-cni must enable the network-policy agent"
  }
}

run "trust_is_pinned_to_one_service_account" {
  command = apply # against the mock: Terraform 1.9 applies overrides to computed values only at apply

  assert {
    condition = alltrue([
      for sa, ns in {
        "tower-mcp"                    = "ask-the-tower"
        "binding-page"                 = "ask-the-tower"
        "alerts"                       = "ask-the-tower"
        "ref-client"                   = "ask-the-tower"
        "aws-load-balancer-controller" = "kube-system"
        } : jsondecode(output.irsa_trust_policies[sa]).Statement[0].Condition.StringEquals == {
        "oidc.eks.us-east-1.amazonaws.com/id/FAKEISSUER:sub" = "system:serviceaccount:${ns}:${sa}"
        "oidc.eks.us-east-1.amazonaws.com/id/FAKEISSUER:aud" = "sts.amazonaws.com"
      }
    ])
    error_message = "each IRSA role must trust exactly system:serviceaccount:<ns>:<sa> with aud sts.amazonaws.com"
  }
  assert {
    condition = alltrue([
      for sa, doc in output.irsa_trust_policies : jsondecode(doc).Statement[0].Action == "sts:AssumeRoleWithWebIdentity"
    ])
    error_message = "IRSA trust must be web identity only"
  }
  assert {
    condition     = length(output.irsa_role_arns) == 5
    error_message = "five IRSA roles: four services + the load balancer controller"
  }
}

run "geo_prefixed_model_adds_the_inference_profile" {
  command = plan

  variables {
    bedrock_model_id = "us.amazon.nova-micro-v1:0"
  }

  assert {
    condition = flatten([for s in jsondecode(output.irsa_policies["ref-client"]).Statement : s.Resource]) == [
      "arn:aws:bedrock:us-east-1::foundation-model/amazon.nova-micro-v1:0",
      "arn:aws:bedrock:us-east-1:fake-acct:inference-profile/us.amazon.nova-micro-v1:0",
    ]
    error_message = "a geo-prefixed id needs the profile ARN plus the base foundation-model ARN, nothing else"
  }
}
