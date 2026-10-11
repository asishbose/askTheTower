# The mock carrier on Fargate behind an internal ALB (10 §1-2, 08). Same image and scenarios as local (prompt 13
# guardrail: no second mock path for AWS).
#
# Exposure:
# - The ALB is internal. AgentCore Gateway and Identity reach it through their managed VPC resource; the phone
#   reaches only /oauth2/authorize, through the API Gateway VPC link (modules/lambdas).
# - Listener rules forward only the CAMARA API prefixes, /oauth2/* and discovery. /_admin/* (the simulation aid,
#   MOCK_ADMIN=1) is never forwarded: scripts/aws_seed.py reaches it from inside the task via ECS Exec.
# TLS: HTTPS on the ALB with an ACM certificate for `domain_name` — DNS-validated in a public Route 53 zone you own
# (the record then resolves to the ALB's private addresses), or an existing certificate ARN. A private CA would
# avoid the public name but costs more than the rest of the demo together; documented in deploy/terraform/README.md.

locals {
  container_name = "mock-carrier"
  port           = 8443
  create_cert    = var.certificate_arn == "" && var.route53_zone_id != "" && var.domain_name != ""
  cert_arn       = var.certificate_arn != "" ? var.certificate_arn : (local.create_cert ? aws_acm_certificate_validation.this[0].certificate_arn : "")
  https          = local.cert_arn != ""
  base_url       = var.domain_name != "" ? "https://${var.domain_name}" : "http://${aws_lb.this.dns_name}"

  # The mock's OAuth client registry (it issues credentials; 10 §4). Secrets are generated at the root and also
  # handed to AgentCore Identity, which is the only consumer.
  clients_yaml = yamlencode({
    clients = {
      tower = {
        secret      = var.client_secrets["tower"]
        grant_types = ["client_credentials"]
        scopes = [
          "sim-swap", "sim-swap-subscriptions", "call-forwarding-signal",
          "device-reachability-status", "device-reachability-status-subscriptions",
        ]
      }
      alerts = {
        secret      = var.client_secrets["alerts"]
        grant_types = ["client_credentials"]
        scopes = [
          "sim-swap", "sim-swap-subscriptions", "call-forwarding-signal",
          "device-reachability-status", "device-reachability-status-subscriptions",
        ]
      }
      "binding-page" = {
        secret                = var.client_secrets["binding-page"]
        grant_types           = ["authorization_code"]
        redirect_uri_prefixes = var.binding_redirect_prefixes
        scopes                = ["number-verification"]
      }
    }
  })

  forwarded_paths = [
    ["/sim-swap/*", "/sim-swap-subscriptions/*", "/call-forwarding-signal/*", "/number-verification/*", "/device-reachability-status/*"],
    ["/device-reachability-status-subscriptions/*", "/oauth2/*", "/.well-known/*", "/healthz"],
  ]
}

data "aws_iam_policy_document" "ecs_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["ecs-tasks.amazonaws.com"]
    }
  }
}

# --- secrets (the mock's own; nothing carrier-side for Tower lives here) ------------------------------------------

resource "random_password" "jwt" {
  length  = 48
  special = false
}

resource "aws_secretsmanager_secret" "registry" {
  name                    = "${var.name}/mock-carrier/registry"
  description             = "The mock carrier's own OAuth client registry and token-signing secret"
  kms_key_id              = var.kms_key_arn
  recovery_window_in_days = 0 # `make down` must leave nothing behind
}

resource "aws_secretsmanager_secret_version" "registry" {
  secret_id = aws_secretsmanager_secret.registry.id
  secret_string = jsonencode({
    clients_yaml = local.clients_yaml
    jwt_secret   = random_password.jwt.result
  })
}

# --- TLS ------------------------------------------------------------------------------------------------------------

resource "aws_acm_certificate" "this" {
  count             = local.create_cert ? 1 : 0
  domain_name       = var.domain_name
  validation_method = "DNS"

  lifecycle {
    create_before_destroy = true
  }
}

resource "aws_route53_record" "validation" {
  for_each = local.create_cert ? {
    for o in aws_acm_certificate.this[0].domain_validation_options : o.domain_name => o
  } : {}

  zone_id         = var.route53_zone_id
  name            = each.value.resource_record_name
  type            = each.value.resource_record_type
  records         = [each.value.resource_record_value]
  ttl             = 60
  allow_overwrite = true
}

resource "aws_acm_certificate_validation" "this" {
  count                   = local.create_cert ? 1 : 0
  certificate_arn         = aws_acm_certificate.this[0].arn
  validation_record_fqdns = [for r in aws_route53_record.validation : r.fqdn]
}

resource "aws_route53_record" "alb" {
  count   = var.route53_zone_id != "" && var.domain_name != "" ? 1 : 0
  zone_id = var.route53_zone_id
  name    = var.domain_name
  type    = "A"

  alias {
    name                   = aws_lb.this.dns_name
    zone_id                = aws_lb.this.zone_id
    evaluate_target_health = false
  }
}

# --- load balancer --------------------------------------------------------------------------------------------------

resource "aws_security_group" "alb" {
  name        = "${var.name}-mock-alb"
  description = "Mock carrier internal ALB: HTTPS from inside the VPC only"
  vpc_id      = var.vpc_id

  ingress {
    description = "HTTPS from the VPC (Gateway/Identity managed VPC resource, VPC link)"
    from_port   = local.https ? 443 : 80
    to_port     = local.https ? 443 : 80
    protocol    = "tcp"
    cidr_blocks = [var.vpc_cidr]
  }

  egress {
    description = "to the task"
    from_port   = local.port
    to_port     = local.port
    protocol    = "tcp"
    cidr_blocks = [var.vpc_cidr]
  }
}

resource "aws_security_group" "task" {
  name        = "${var.name}-mock-task"
  description = "Mock carrier task: inbound only from its ALB"
  vpc_id      = var.vpc_id

  ingress {
    description     = "from the ALB"
    from_port       = local.port
    to_port         = local.port
    protocol        = "tcp"
    security_groups = [aws_security_group.alb.id]
  }

  egress {
    description = "ECR pulls, logs, webhook deliveries to the public hooks URL"
    from_port   = 443
    to_port     = 443
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }
}

resource "aws_lb" "this" {
  name                       = substr("${var.name}-mock", 0, 32)
  internal                   = true
  load_balancer_type         = "application"
  subnets                    = var.alb_subnet_ids
  security_groups            = [aws_security_group.alb.id]
  drop_invalid_header_fields = true
}

resource "aws_lb_target_group" "this" {
  name        = substr("${var.name}-mock", 0, 32)
  port        = local.port
  protocol    = "HTTP"
  target_type = "ip"
  vpc_id      = var.vpc_id

  health_check {
    path                = "/healthz"
    matcher             = "200"
    interval            = 15
    healthy_threshold   = 2
    unhealthy_threshold = 3
  }

  deregistration_delay = 5
}

resource "aws_lb_listener" "this" {
  load_balancer_arn = aws_lb.this.arn
  port              = local.https ? 443 : 80
  protocol          = local.https ? "HTTPS" : "HTTP"
  certificate_arn   = local.https ? local.cert_arn : null
  ssl_policy        = local.https ? "ELBSecurityPolicy-TLS13-1-2-2021-06" : null

  default_action {
    type = "fixed-response"
    fixed_response {
      content_type = "application/json"
      status_code  = "404"
      message_body = "{\"status\":404,\"code\":\"NOT_FOUND\",\"message\":\"not routed\"}"
    }
  }

  lifecycle {
    precondition {
      condition     = var.domain_name != "" && local.https
      error_message = "The mock needs mock_domain_name plus mock_route53_zone_id or mock_certificate_arn: AgentCore Gateway and Identity call it over HTTPS."
    }
  }
}

resource "aws_lb_listener_rule" "forward" {
  count        = length(local.forwarded_paths)
  listener_arn = aws_lb_listener.this.arn
  priority     = 10 + count.index

  action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.this.arn
  }

  condition {
    path_pattern {
      values = local.forwarded_paths[count.index]
    }
  }
}

# --- ECS ------------------------------------------------------------------------------------------------------------

resource "aws_cloudwatch_log_group" "this" {
  name              = "/ecs/${var.name}/mock-carrier"
  retention_in_days = var.log_retention_days
  kms_key_id        = var.kms_key_arn
}

resource "aws_cloudwatch_log_group" "exec" {
  name              = "/ecs/${var.name}/mock-carrier-exec"
  retention_in_days = var.log_retention_days
  kms_key_id        = var.kms_key_arn
}

resource "aws_ecs_cluster" "this" {
  name = "${var.name}-mock"

  configuration {
    execute_command_configuration {
      kms_key_id = var.kms_key_arn
      logging    = "OVERRIDE"
      log_configuration {
        cloud_watch_encryption_enabled = true
        cloud_watch_log_group_name     = aws_cloudwatch_log_group.exec.name
      }
    }
  }

  setting {
    name  = "containerInsights"
    value = "disabled"
  }
}

resource "aws_iam_role" "execution" {
  name               = "${var.name}-mock-execution"
  assume_role_policy = data.aws_iam_policy_document.ecs_assume.json
}

resource "aws_iam_role_policy_attachment" "execution" {
  role       = aws_iam_role.execution.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy"
}

data "aws_iam_policy_document" "execution" {
  statement {
    actions   = ["secretsmanager:GetSecretValue"]
    resources = [aws_secretsmanager_secret.registry.arn]
  }
  statement {
    actions   = ["kms:Decrypt"]
    resources = [var.kms_key_arn]
  }
}

resource "aws_iam_role_policy" "execution" {
  name   = "registry-secret"
  role   = aws_iam_role.execution.id
  policy = data.aws_iam_policy_document.execution.json
}

resource "aws_iam_role" "task" {
  name               = "${var.name}-mock-task"
  assume_role_policy = data.aws_iam_policy_document.ecs_assume.json
}

data "aws_iam_policy_document" "task" {
  statement {
    sid = "EcsExec"
    actions = [
      "ssmmessages:CreateControlChannel", "ssmmessages:CreateDataChannel",
      "ssmmessages:OpenControlChannel", "ssmmessages:OpenDataChannel",
    ]
    resources = ["*"]
  }
  statement {
    sid       = "ExecLogs"
    actions   = ["logs:CreateLogStream", "logs:PutLogEvents", "logs:DescribeLogStreams"]
    resources = ["${aws_cloudwatch_log_group.exec.arn}:*"]
  }
  statement {
    sid       = "ExecKms"
    actions   = ["kms:Decrypt", "kms:GenerateDataKey"]
    resources = [var.kms_key_arn]
  }
}

resource "aws_iam_role_policy" "task" {
  name   = "ecs-exec"
  role   = aws_iam_role.task.id
  policy = data.aws_iam_policy_document.task.json
}

resource "aws_ecs_task_definition" "this" {
  family                   = "${var.name}-mock-carrier"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = 256
  memory                   = 512
  execution_role_arn       = aws_iam_role.execution.arn
  task_role_arn            = aws_iam_role.task.arn

  runtime_platform {
    operating_system_family = "LINUX"
    cpu_architecture        = "ARM64"
  }

  container_definitions = jsonencode([{
    name      = local.container_name
    image     = var.image
    essential = true
    # The registry arrives as a secret env var; the mock reads a file (MOCK_CLIENTS_FILE), so write it to /tmp.
    entryPoint   = ["sh", "-c"]
    command      = ["umask 077 && printf '%s' \"$MOCK_CLIENTS_YAML\" > /tmp/clients.yaml && unset MOCK_CLIENTS_YAML && exec python -m mock_carrier"]
    portMappings = [{ containerPort = local.port, protocol = "tcp" }]
    environment = [
      { name = "MOCK_PORT", value = tostring(local.port) },
      { name = "MOCK_BASE_URL", value = local.base_url },
      { name = "MOCK_SCENARIO", value = var.scenario },
      { name = "MOCK_CLIENTS_FILE", value = "/tmp/clients.yaml" },
      { name = "MOCK_ADMIN", value = "1" }, # reachable only from inside the task (ALB never forwards /_admin)
      # 08 §3 (D-G): the task cannot see the phone's network, so it assumes this client id is on mobile data.
      { name = "MOCK_ASSUME_MOBILE_DATA", value = var.assume_mobile_data ? "1" : "0" },
      { name = "MOCK_ASSUME_CLIENT_ID", value = var.assume_client_id },
    ]
    secrets = [
      { name = "MOCK_CLIENTS_YAML", valueFrom = "${aws_secretsmanager_secret.registry.arn}:clients_yaml::" },
      { name = "MOCK_JWT_SECRET", valueFrom = "${aws_secretsmanager_secret.registry.arn}:jwt_secret::" },
    ]
    logConfiguration = {
      logDriver = "awslogs"
      options = {
        awslogs-group         = aws_cloudwatch_log_group.this.name
        awslogs-region        = var.region
        awslogs-stream-prefix = "mock"
      }
    }
  }])

  depends_on = [aws_secretsmanager_secret_version.registry]
}

resource "aws_ecs_service" "this" {
  name                   = "mock-carrier"
  cluster                = aws_ecs_cluster.this.id
  task_definition        = aws_ecs_task_definition.this.arn
  desired_count          = 1
  launch_type            = "FARGATE"
  enable_execute_command = true
  propagate_tags         = "SERVICE"

  network_configuration {
    subnets          = var.task_subnet_ids
    security_groups  = [aws_security_group.task.id]
    assign_public_ip = var.assign_public_ip
  }

  load_balancer {
    target_group_arn = aws_lb_target_group.this.arn
    container_name   = local.container_name
    container_port   = local.port
  }

  depends_on = [aws_lb_listener.this]
}
