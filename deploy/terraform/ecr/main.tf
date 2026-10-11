# Container registry for all five images (`make push` fills it). Its own root and state: created once by
# `make ecr-up`, kept by `make down ENV=aws`, removed only by `make down-all`. The main root reads these
# repositories with `data "aws_ecr_repository"` and never manages them.

locals {
  name     = "${var.name_prefix}-${var.environment}"
  services = toset(["tower-mcp", "mock-carrier", "binding-page", "alerts", "ref-client"])
}

resource "aws_ecr_repository" "service" {
  for_each = local.services

  name                 = "${local.name}/${each.key}"
  image_tag_mutability = "MUTABLE"
  force_delete         = true # `make down-all` must return the account to zero

  image_scanning_configuration {
    scan_on_push = true
  }

  encryption_configuration {
    encryption_type = "AES256" # a CMK here would need grants for every puller (Lambda, Runtime, Fargate)
  }
}

resource "aws_ecr_lifecycle_policy" "service" {
  for_each   = aws_ecr_repository.service
  repository = each.value.name
  policy = jsonencode({
    rules = [{
      rulePriority = 1
      description  = "keep the last 10 images"
      selection    = { tagStatus = "any", countType = "imageCountMoreThan", countNumber = 10 }
      action       = { type = "expire" }
    }]
  })
}
