# AWS Deployment — Infrastructure Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stand up the AWS infrastructure and CI pipeline that deploy the `use_mem0` container image as CloudFront → streaming Lambda Function URL → Neon, at ~$0/month with no users.

**Architecture:** One Terraform root module in `infra/`, no VPC (a Lambda Function URL cannot stream from inside one), an S3-hosted SPA and a Lambda API behind a single CloudFront distribution so the browser sees one origin. GitHub Actions deploys via OIDC with no long-lived keys.

**Tech Stack:** Terraform (AWS provider v5+), AWS Lambda container images, CloudFront, S3, ECR, SSM Parameter Store, CloudWatch, GitHub Actions, Neon (managed Postgres).

**Spec:** `docs/superpowers/specs/2026-09-02-aws-serverless-deployment-design.md`

**Depends on:** `docs/superpowers/plans/2026-09-02-aws-deploy-app-changes.md` must be complete. This plan consumes the image it produces and the environment-variable contract it establishes.

## Global Constraints

- Region **us-east-1** (CloudFront certificates and Neon colocation).
- The Lambda function is **outside any VPC**. Function URLs do not support response streaming from a VPC-attached function. Do not add `vpc_config`.
- Function URL `AuthType = NONE` — OAC cannot be used, see the spec §9.3.
- CloudFront must use **`AllViewerExceptHostHeader`** on the Lambda behaviours. Forwarding the viewer Host makes the Function URL return 403.
- Lambda: **arm64**, 2048 MB, timeout **120 s**, reserved concurrency **10**.
- The CloudFront distribution carries `prevent_destroy`. Its domain is the OAuth redirect host; recreating it breaks login until the URI is re-registered by hand in Google's console.
- Secrets are seeded into SSM **out of band**. Terraform declares names only, never values.
- Terraform state lives in an encrypted S3 bucket with `use_lockfile = true` (DynamoDB is no longer required).
- Every resource carries `Project = "mem0-chatbot"` and `ManagedBy = "terraform"` default tags.

---

### Task 1: Terraform skeleton and remote state

**Files:**
- Create: `infra/versions.tf`, `infra/variables.tf`, `infra/outputs.tf`, `infra/bootstrap/main.tf`, `infra/.gitignore`

**Interfaces:**
- Produces: variables `project`, `region`, `public_base_url`, `image_uri`, `alert_email`; the state bucket every later task writes into.

- [ ] **Step 1: Create the bootstrap module for state**

The state bucket cannot live in the state it stores, so it gets its own tiny module applied once with local state.

Create `infra/bootstrap/main.tf`:

```hcl
# Applied once, with local state, to create the bucket the root module's
# remote state lives in. Chicken-and-egg: this cannot be in that state itself.
terraform {
  required_version = ">= 1.10"
  required_providers {
    aws = { source = "hashicorp/aws", version = "~> 5.0" }
  }
}

provider "aws" {
  region = "us-east-1"
}

resource "aws_s3_bucket" "state" {
  bucket = "mem0-chatbot-tfstate-${data.aws_caller_identity.current.account_id}"
}

data "aws_caller_identity" "current" {}

resource "aws_s3_bucket_versioning" "state" {
  bucket = aws_s3_bucket.state.id
  versioning_configuration { status = "Enabled" }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "state" {
  bucket = aws_s3_bucket.state.id
  rule {
    apply_server_side_encryption_by_default { sse_algorithm = "AES256" }
  }
}

resource "aws_s3_bucket_public_access_block" "state" {
  bucket                  = aws_s3_bucket.state.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

output "state_bucket" {
  value = aws_s3_bucket.state.id
}
```

- [ ] **Step 2: Apply the bootstrap**

Run: `cd infra/bootstrap && terraform init && terraform apply`
Expected: a bucket named `mem0-chatbot-tfstate-<account-id>`. Record the name; the next step needs it.

- [ ] **Step 3: Write the root module's provider and backend**

Create `infra/versions.tf`:

```hcl
terraform {
  required_version = ">= 1.10"

  required_providers {
    aws = { source = "hashicorp/aws", version = "~> 5.0" }
  }

  backend "s3" {
    bucket       = "REPLACE_WITH_BOOTSTRAP_OUTPUT"
    key          = "mem0-chatbot/prod.tfstate"
    region       = "us-east-1"
    encrypt      = true
    use_lockfile = true
  }
}

provider "aws" {
  region = var.region

  default_tags {
    tags = {
      Project   = var.project
      ManagedBy = "terraform"
    }
  }
}
```

Substitute the bucket name from Step 2.

- [ ] **Step 4: Write the variables**

Create `infra/variables.tf`:

```hcl
variable "project" {
  type        = string
  default     = "mem0-chatbot"
  description = "Name prefix and Project tag for every resource."
}

variable "region" {
  type    = string
  default = "us-east-1"
}

variable "public_base_url" {
  type        = string
  default     = ""
  description = <<-EOT
    The origin users reach, e.g. https://d111111abcdef8.cloudfront.net.
    Empty on the very first apply, because the distribution does not exist yet.
    Set it to the distribution's domain and apply again — see Task 10.
  EOT
}

variable "image_uri" {
  type        = string
  description = "ECR image URI by digest. CI supplies this on every deploy."
  default     = ""
}

variable "alert_email" {
  type        = string
  description = "Where CloudWatch alarms and budget notifications are sent."
}

variable "github_repo" {
  type        = string
  default     = "konica/practice-agent-memory"
  description = "owner/repo allowed to assume the deploy role via OIDC."
}
```

- [ ] **Step 5: Ignore local Terraform noise**

Create `infra/.gitignore`:

```
.terraform/
*.tfstate
*.tfstate.*
.terraform.lock.hcl
bootstrap/terraform.tfstate*
*.tfvars
```

Commit `.terraform.lock.hcl` in a team setting; it is ignored here because the provider version is already pinned and a single operator is expected. Remove that line if more than one person will run `terraform init`.

- [ ] **Step 6: Verify it initialises**

Run: `cd infra && terraform init && terraform validate`
Expected: `Success! The configuration is valid.`

- [ ] **Step 7: Commit**

```bash
git add infra/versions.tf infra/variables.tf infra/.gitignore infra/bootstrap/main.tf
git commit -m "feat: terraform skeleton and remote state bootstrap"
```

---

### Task 2: ECR repository

**Files:**
- Create: `infra/ecr.tf`

**Interfaces:**
- Produces: `aws_ecr_repository.app` — CI pushes here; Task 4's function pulls from it.

- [ ] **Step 1: Write the repository**

Create `infra/ecr.tf`:

```hcl
resource "aws_ecr_repository" "app" {
  name                 = var.project
  image_tag_mutability = "IMMUTABLE"

  image_scanning_configuration { scan_on_push = true }
  encryption_configuration { encryption_type = "AES256" }
}

# Images are ~1.5 GB; five of them is ~$0.75/month. Without this the repository
# grows without bound and quietly becomes the largest line on a $1 bill.
resource "aws_ecr_lifecycle_policy" "app" {
  repository = aws_ecr_repository.app.name

  policy = jsonencode({
    rules = [{
      rulePriority = 1
      description  = "Keep the last 5 images"
      selection = {
        tagStatus   = "any"
        countType   = "imageCountMoreThan"
        countNumber = 5
      }
      action = { type = "expire" }
    }]
  })
}
```

`IMMUTABLE` tags matter: CI deploys by digest, and a mutable tag would let a rebuild silently change what a released version points at.

- [ ] **Step 2: Validate and apply**

Run: `cd infra && terraform validate && terraform apply -var alert_email=<your-email>`
Expected: the repository is created. Confirm with `aws ecr describe-repositories --repository-names mem0-chatbot`.

- [ ] **Step 3: Commit**

```bash
git add infra/ecr.tf
git commit -m "feat: ECR repository with immutable tags and a 5-image lifecycle"
```

---

### Task 3: SSM parameters and KMS key

Terraform declares the parameter **names**. Values are seeded by hand with the CLI so no secret enters state.

**Files:**
- Create: `infra/ssm.tf`

**Interfaces:**
- Produces: `/mem0-chatbot/prod/*` SecureString parameters and `aws_kms_key.secrets`. Task 4's execution role reads them; the app's `CONFIG_SSM_PATH` points at the path.

- [ ] **Step 1: Write the parameters**

Create `infra/ssm.tf`:

```hcl
resource "aws_kms_key" "secrets" {
  description             = "${var.project} SSM SecureString parameters"
  enable_key_rotation     = true
  deletion_window_in_days = 7
}

resource "aws_kms_alias" "secrets" {
  name          = "alias/${var.project}-secrets"
  target_key_id = aws_kms_key.secrets.key_id
}

locals {
  # Names only. Values are seeded out of band (Task 10) so that no secret ever
  # enters Terraform state, where it would be readable by anyone with state access.
  secret_names = [
    "OPENAI_API_KEY",
    "MEM0_API_KEY",
    "LANGSMITH_API_KEY",
    "GOOGLE_CLIENT_ID",
    "GOOGLE_CLIENT_SECRET",
    "DATABASE_URL",
    "SESSION_SECRET",
  ]

  ssm_path = "/${var.project}/prod"
}

resource "aws_ssm_parameter" "secret" {
  for_each = toset(local.secret_names)

  name   = "${local.ssm_path}/${each.value}"
  type   = "SecureString"
  key_id = aws_kms_key.secrets.key_id

  # A deliberate placeholder. The real value is set with the CLI; ignore_changes
  # stops Terraform reverting it on the next apply.
  value = "PLACEHOLDER_SET_OUT_OF_BAND"

  lifecycle {
    ignore_changes = [value]
  }
}
```

`LANGSMITH_PROJECT` is deliberately absent — it is not a secret and is passed as a plain Lambda environment variable in Task 4.

- [ ] **Step 2: Apply**

Run: `cd infra && terraform apply -var alert_email=<your-email>`
Expected: seven SecureString parameters and one KMS key.

- [ ] **Step 3: Verify the placeholder pattern holds**

Run: `aws ssm put-parameter --name /mem0-chatbot/prod/SESSION_SECRET --value "$(python3 -c 'import secrets;print(secrets.token_urlsafe(32))')" --type SecureString --overwrite && cd infra && terraform plan -var alert_email=<your-email>`
Expected: `No changes.` — `ignore_changes` is working. If Terraform proposes reverting the value, the lifecycle block is wrong and every apply would break production.

- [ ] **Step 4: Commit**

```bash
git add infra/ssm.tf
git commit -m "feat: SSM SecureString parameters, names in Terraform, values out of band"
```

---

### Task 4: Lambda function, execution role, and Function URL

**Files:**
- Create: `infra/lambda.tf`, `infra/iam_lambda.tf`

**Interfaces:**
- Consumes: `aws_ecr_repository.app`, the SSM parameters, `var.image_uri`, `var.public_base_url`.
- Produces: `aws_lambda_function_url.app.function_url` — Task 6's CloudFront origin.

- [ ] **Step 1: Write the execution role**

Create `infra/iam_lambda.tf`:

```hcl
data "aws_iam_policy_document" "lambda_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["lambda.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "lambda" {
  name               = "${var.project}-lambda"
  assume_role_policy = data.aws_iam_policy_document.lambda_assume.json
}

resource "aws_iam_role_policy_attachment" "lambda_logs" {
  role       = aws_iam_role.lambda.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

# Scoped to this project's path and key. The function makes no other AWS calls.
data "aws_iam_policy_document" "lambda_secrets" {
  statement {
    actions   = ["ssm:GetParametersByPath", "ssm:GetParameters"]
    resources = ["arn:aws:ssm:${var.region}:${data.aws_caller_identity.current.account_id}:parameter${local.ssm_path}/*"]
  }
  statement {
    actions   = ["kms:Decrypt"]
    resources = [aws_kms_key.secrets.arn]
  }
}

resource "aws_iam_role_policy" "lambda_secrets" {
  name   = "${var.project}-lambda-secrets"
  role   = aws_iam_role.lambda.id
  policy = data.aws_iam_policy_document.lambda_secrets.json
}

data "aws_caller_identity" "current" {}
```

- [ ] **Step 2: Write the function**

Create `infra/lambda.tf`:

```hcl
resource "aws_cloudwatch_log_group" "app" {
  name = "/aws/lambda/${var.project}"
  # Explicit, because the default is Never Expire and that is how a $1 bill
  # becomes a $20 one.
  retention_in_days = 14
}

resource "aws_lambda_function" "app" {
  function_name = var.project
  role          = aws_iam_role.lambda.arn
  package_type  = "Image"
  image_uri     = var.image_uri
  architectures = ["arm64"]

  # CPU scales with memory, and cold start is dominated by imports, so this is
  # the main lever on time-to-first-byte. Free at low traffic: 2,400 turns a
  # month is ~48k GB-s against a 400k allowance.
  memory_size = 2048

  # Not 900. Streamed responses bill for the whole invocation and a client
  # disconnect does not stop the billing.
  timeout = 120

  # Bounds both the monthly cost blast radius and the database connection count.
  reserved_concurrent_executions = 10

  environment {
    variables = {
      CONFIG_SSM_PATH        = local.ssm_path
      PUBLIC_BASE_URL        = var.public_base_url
      FRONTEND_ORIGIN        = var.public_base_url
      LANGSMITH_PROJECT      = var.project
      DB_POOL_MAX_SIZE       = "1"
      AWS_LWA_INVOKE_MODE    = "response_stream"
      AWS_LWA_READINESS_CHECK_PATH = "/health"
      PORT                   = "8080"
    }
  }

  depends_on = [aws_cloudwatch_log_group.app]

  lifecycle {
    # CI deploys new images; Terraform must not revert to the variable's value
    # on the next infra apply.
    ignore_changes = [image_uri]
  }
}

resource "aws_lambda_function_url" "app" {
  function_name = aws_lambda_function.app.function_name

  # NONE, not AWS_IAM: locking a Function URL to CloudFront with OAC requires
  # the browser to send x-amz-content-sha256 on every POST, which the AG-UI
  # client will never do. The origin-secret header in Task 6 is the mitigation.
  authorization_type = "NONE"
  invoke_mode        = "RESPONSE_STREAM"
}

output "function_url" {
  value = aws_lambda_function_url.app.function_url
}
```

Note `AWS_LWA_READINESS_CHECK_PATH = "/health"`, not `/ready`: the adapter polls this before the function is considered warm, and readiness that depends on Postgres would fail cold starts during any database blip.

- [ ] **Step 3: Validate**

Run: `cd infra && terraform validate`
Expected: valid. Do **not** apply yet — `var.image_uri` is empty and a Lambda needs a real image. Task 9's pipeline pushes one first; Task 10 sequences the first apply.

- [ ] **Step 4: Commit**

```bash
git add infra/lambda.tf infra/iam_lambda.tf
git commit -m "feat: streaming Lambda function URL, arm64, outside any VPC

A Function URL cannot stream from inside a VPC, which is what removes
the NAT gateway and the ALB from this design entirely."
```

---

### Task 5: S3 bucket for the SPA

**Files:**
- Create: `infra/s3.tf`

**Interfaces:**
- Consumes: nothing.
- Produces: `aws_s3_bucket.spa` and `aws_cloudfront_origin_access_control.spa` for Task 6.

- [ ] **Step 1: Write the bucket**

Create `infra/s3.tf`:

```hcl
resource "aws_s3_bucket" "spa" {
  bucket = "${var.project}-spa-${data.aws_caller_identity.current.account_id}"
}

resource "aws_s3_bucket_public_access_block" "spa" {
  bucket                  = aws_s3_bucket.spa.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

# OAC, not the legacy OAI and not the website endpoint: the bucket stays fully
# private and only this distribution can read it.
resource "aws_cloudfront_origin_access_control" "spa" {
  name                              = "${var.project}-spa"
  origin_access_control_origin_type = "s3"
  signing_behavior                  = "always"
  signing_protocol                  = "sigv4"
}

data "aws_iam_policy_document" "spa_bucket" {
  statement {
    actions   = ["s3:GetObject"]
    resources = ["${aws_s3_bucket.spa.arn}/*"]

    principals {
      type        = "Service"
      identifiers = ["cloudfront.amazonaws.com"]
    }

    condition {
      test     = "StringEquals"
      variable = "AWS:SourceArn"
      values   = [aws_cloudfront_distribution.app.arn]
    }
  }
}

resource "aws_s3_bucket_policy" "spa" {
  bucket = aws_s3_bucket.spa.id
  policy = data.aws_iam_policy_document.spa_bucket.json
}

output "spa_bucket" {
  value = aws_s3_bucket.spa.id
}
```

- [ ] **Step 2: Validate**

Run: `cd infra && terraform validate`
Expected: valid. It references `aws_cloudfront_distribution.app`, which Task 6 creates — validation passes, but do not apply until Task 6 is written.

- [ ] **Step 3: Commit**

```bash
git add infra/s3.tf
git commit -m "feat: private S3 bucket for the SPA, readable only via CloudFront OAC"
```

---

### Task 6: CloudFront distribution

**Files:**
- Create: `infra/cloudfront.tf`

**Interfaces:**
- Consumes: `aws_lambda_function_url.app`, `aws_s3_bucket.spa`.
- Produces: `aws_cloudfront_distribution.app` and its `domain_name`, which becomes `PUBLIC_BASE_URL` and the Google OAuth redirect host.

- [ ] **Step 1: Write the distribution**

Create `infra/cloudfront.tf`:

```hcl
data "aws_cloudfront_cache_policy" "disabled" { name = "Managed-CachingDisabled" }
data "aws_cloudfront_cache_policy" "optimized" { name = "Managed-CachingOptimized" }

# NOT AllViewer. A Lambda Function URL rejects any request whose Host header is
# not its own domain, so forwarding the viewer's Host returns 403. This is why
# the app reads PUBLIC_BASE_URL instead of request.url_for().
data "aws_cloudfront_origin_request_policy" "all_viewer_except_host" {
  name = "Managed-AllViewerExceptHostHeader"
}

resource "random_password" "origin_secret" {
  length  = 40
  special = false
}

# Client-side routing without custom error responses. Custom error responses are
# per-distribution, not per-behaviour, so the usual 404 -> index.html trick would
# also rewrite the API's deliberate 404s — and conversations/ownership.py returns
# 404 on purpose, so another user's conversation is indistinguishable from one
# that does not exist. A function on the default behaviour only touches the SPA.
resource "aws_cloudfront_function" "spa_router" {
  name    = "${var.project}-spa-router"
  runtime = "cloudfront-js-2.0"
  publish = true

  code = <<-JS
    function handler(event) {
      var uri = event.request.uri;
      if (uri.indexOf('.') === -1) {
        event.request.uri = '/index.html';
      }
      return event.request;
    }
  JS
}

locals {
  lambda_origin_id = "lambda"
  s3_origin_id     = "spa"
  api_paths        = ["/agent", "/auth/*", "/conversations*", "/health", "/ready"]
}

resource "aws_cloudfront_distribution" "app" {
  enabled             = true
  default_root_object = "index.html"
  comment             = var.project
  price_class         = "PriceClass_100"

  origin {
    origin_id   = local.lambda_origin_id
    domain_name = replace(replace(aws_lambda_function_url.app.function_url, "https://", ""), "/", "")

    custom_origin_config {
      http_port                = 80
      https_port               = 443
      origin_protocol_policy   = "https-only"
      origin_ssl_protocols     = ["TLSv1.2"]
      origin_read_timeout      = 60
      origin_keepalive_timeout = 60
    }

    # The Function URL is publicly reachable (AuthType NONE, see the spec §9.3).
    # This header lets the app reject anything that did not come through
    # CloudFront. Obscurity, not authentication — application auth still gates
    # every route that matters.
    custom_header {
      name  = "X-Origin-Secret"
      value = random_password.origin_secret.result
    }
  }

  origin {
    origin_id                = local.s3_origin_id
    domain_name              = aws_s3_bucket.spa.bucket_regional_domain_name
    origin_access_control_id = aws_cloudfront_origin_access_control.spa.id
  }

  default_cache_behavior {
    target_origin_id       = local.s3_origin_id
    viewer_protocol_policy = "redirect-to-https"
    allowed_methods        = ["GET", "HEAD", "OPTIONS"]
    cached_methods         = ["GET", "HEAD"]
    cache_policy_id        = data.aws_cloudfront_cache_policy.optimized.id
    compress               = true

    function_association {
      event_type   = "viewer-request"
      function_arn = aws_cloudfront_function.spa_router.arn
    }
  }

  dynamic "ordered_cache_behavior" {
    for_each = local.api_paths

    content {
      path_pattern             = ordered_cache_behavior.value
      target_origin_id         = local.lambda_origin_id
      viewer_protocol_policy   = "redirect-to-https"
      allowed_methods          = ["GET", "HEAD", "OPTIONS", "PUT", "POST", "PATCH", "DELETE"]
      cached_methods           = ["GET", "HEAD"]
      cache_policy_id          = data.aws_cloudfront_cache_policy.disabled.id
      origin_request_policy_id = data.aws_cloudfront_origin_request_policy.all_viewer_except_host.id

      # Compression needs a Content-Length, which a chunked SSE response has
      # not got. It buys nothing and adds Accept-Encoding to the cache key.
      compress = false
    }
  }

  restrictions {
    geo_restriction { restriction_type = "none" }
  }

  viewer_certificate {
    cloudfront_default_certificate = true
  }

  lifecycle {
    # This domain is the OAuth redirect host and the cookie origin. Replacing
    # the distribution issues a new one and breaks login until the redirect URI
    # is re-registered by hand in Google's console.
    prevent_destroy = true
  }
}

output "public_url" {
  value = "https://${aws_cloudfront_distribution.app.domain_name}"
}
```

Add `random` to the required providers in `infra/versions.tf`:

```hcl
    random = { source = "hashicorp/random", version = "~> 3.6" }
```

- [ ] **Step 2: Validate**

Run: `cd infra && terraform init -upgrade && terraform validate`
Expected: valid.

- [ ] **Step 3: Commit**

```bash
git add infra/cloudfront.tf infra/versions.tf
git commit -m "feat: CloudFront distribution, one origin for SPA and API

AllViewerExceptHostHeader is mandatory: a Function URL 403s any request
whose Host is not its own. SPA routing uses a CloudFront Function because
custom error responses are per-distribution and would rewrite API 404s."
```

---

### Task 7: Observability and budget

**Files:**
- Create: `infra/observability.tf`

**Interfaces:**
- Consumes: `aws_lambda_function.app`, `aws_cloudfront_distribution.app`, `var.alert_email`.
- Produces: an SNS topic and five alarms.

- [ ] **Step 1: Write the alarms**

Create `infra/observability.tf`:

```hcl
resource "aws_sns_topic" "alerts" {
  name = "${var.project}-alerts"
}

resource "aws_sns_topic_subscription" "alerts_email" {
  topic_arn = aws_sns_topic.alerts.arn
  protocol  = "email"
  endpoint  = var.alert_email
}

resource "aws_cloudwatch_metric_alarm" "lambda_errors" {
  alarm_name          = "${var.project}-lambda-errors"
  namespace           = "AWS/Lambda"
  metric_name         = "Errors"
  dimensions          = { FunctionName = aws_lambda_function.app.function_name }
  statistic           = "Sum"
  period              = 300
  evaluation_periods  = 1
  threshold           = 5
  comparison_operator = "GreaterThanOrEqualToThreshold"
  alarm_actions       = [aws_sns_topic.alerts.arn]
}

# Throttles mean reserved concurrency is binding and users are being turned
# away. At this scale that is never normal.
resource "aws_cloudwatch_metric_alarm" "lambda_throttles" {
  alarm_name          = "${var.project}-lambda-throttles"
  namespace           = "AWS/Lambda"
  metric_name         = "Throttles"
  dimensions          = { FunctionName = aws_lambda_function.app.function_name }
  statistic           = "Sum"
  period              = 300
  evaluation_periods  = 1
  threshold           = 1
  comparison_operator = "GreaterThanOrEqualToThreshold"
  alarm_actions       = [aws_sns_topic.alerts.arn]
}

# Approaching the 120s timeout means turns are being cut off mid-stream.
resource "aws_cloudwatch_metric_alarm" "lambda_duration" {
  alarm_name          = "${var.project}-lambda-duration"
  namespace           = "AWS/Lambda"
  metric_name         = "Duration"
  dimensions          = { FunctionName = aws_lambda_function.app.function_name }
  extended_statistic  = "p99"
  period              = 300
  evaluation_periods  = 2
  threshold           = 100000 # milliseconds
  comparison_operator = "GreaterThanThreshold"
  alarm_actions       = [aws_sns_topic.alerts.arn]
}

resource "aws_cloudwatch_metric_alarm" "cloudfront_5xx" {
  provider            = aws
  alarm_name          = "${var.project}-cloudfront-5xx"
  namespace           = "AWS/CloudFront"
  metric_name         = "5xxErrorRate"
  dimensions          = { DistributionId = aws_cloudfront_distribution.app.id, Region = "Global" }
  statistic           = "Average"
  period              = 300
  evaluation_periods  = 2
  threshold           = 1
  comparison_operator = "GreaterThanThreshold"
  alarm_actions       = [aws_sns_topic.alerts.arn]
}

# The most important alarm here. A greenfield account is on a 6-month credit
# clock, and one forgotten resource is what burns it.
resource "aws_budgets_budget" "monthly" {
  name         = "${var.project}-monthly"
  budget_type  = "COST"
  limit_amount = "20"
  limit_unit   = "USD"
  time_unit    = "MONTHLY"

  dynamic "notification" {
    for_each = [50, 80, 100]

    content {
      comparison_operator        = "GREATER_THAN"
      threshold                  = notification.value
      threshold_type             = "PERCENTAGE"
      notification_type          = "ACTUAL"
      subscriber_email_addresses = [var.alert_email]
    }
  }
}
```

CloudFront metrics are only published in us-east-1, which this stack already targets, so no second provider alias is needed.

- [ ] **Step 2: Validate**

Run: `cd infra && terraform validate`
Expected: valid.

- [ ] **Step 3: Commit**

```bash
git add infra/observability.tf
git commit -m "feat: alarms for errors, throttles, duration, 5xx, plus a budget"
```

---

### Task 8: GitHub OIDC roles

**Files:**
- Create: `infra/iam_github.tf`

**Interfaces:**
- Produces: `gha-plan` and `gha-deploy` role ARNs for Task 9's workflows.

- [ ] **Step 1: Write the provider and roles**

Create `infra/iam_github.tf`:

```hcl
resource "aws_iam_openid_connect_provider" "github" {
  url             = "https://token.actions.githubusercontent.com"
  client_id_list  = ["sts.amazonaws.com"]
  thumbprint_list = ["6938fd4d98bab03faadb97b34396831e3780aea1"]
}

data "aws_iam_policy_document" "gha_deploy_assume" {
  statement {
    actions = ["sts:AssumeRoleWithWebIdentity"]

    principals {
      type        = "Federated"
      identifiers = [aws_iam_openid_connect_provider.github.arn]
    }

    condition {
      test     = "StringEquals"
      variable = "token.actions.githubusercontent.com:aud"
      values   = ["sts.amazonaws.com"]
    }

    # Pinned to the default branch, never wildcarded. A wildcard here lets any
    # branch or pull request in the repository deploy to production.
    condition {
      test     = "StringEquals"
      variable = "token.actions.githubusercontent.com:sub"
      values   = ["repo:${var.github_repo}:ref:refs/heads/main"]
    }
  }
}

resource "aws_iam_role" "gha_deploy" {
  name               = "${var.project}-gha-deploy"
  assume_role_policy = data.aws_iam_policy_document.gha_deploy_assume.json
}

data "aws_iam_policy_document" "gha_deploy" {
  statement {
    actions = [
      "ecr:GetAuthorizationToken",
      "ecr:BatchCheckLayerAvailability",
      "ecr:CompleteLayerUpload",
      "ecr:InitiateLayerUpload",
      "ecr:PutImage",
      "ecr:UploadLayerPart",
      "ecr:BatchGetImage",
      "ecr:DescribeImages",
    ]
    resources = ["*"]
  }

  statement {
    actions   = ["lambda:UpdateFunctionCode", "lambda:GetFunction", "lambda:PublishVersion"]
    resources = [aws_lambda_function.app.arn]
  }

  statement {
    actions   = ["s3:PutObject", "s3:DeleteObject", "s3:ListBucket"]
    resources = [aws_s3_bucket.spa.arn, "${aws_s3_bucket.spa.arn}/*"]
  }

  statement {
    actions   = ["cloudfront:CreateInvalidation"]
    resources = [aws_cloudfront_distribution.app.arn]
  }
}

resource "aws_iam_role_policy" "gha_deploy" {
  name   = "${var.project}-gha-deploy"
  role   = aws_iam_role.gha_deploy.id
  policy = data.aws_iam_policy_document.gha_deploy.json
}

output "gha_deploy_role_arn" {
  value = aws_iam_role.gha_deploy.arn
}
```

`ecr:GetAuthorizationToken` cannot be resource-scoped — AWS only accepts `*` for it. The remaining ECR actions could be narrowed to the repository ARN; left broad here for one repository, tighten if the account grows.

- [ ] **Step 2: Validate**

Run: `cd infra && terraform validate`
Expected: valid.

- [ ] **Step 3: Commit**

```bash
git add infra/iam_github.tf
git commit -m "feat: GitHub OIDC deploy role, sub pinned to refs/heads/main"
```

---

### Task 9: GitHub Actions workflows

**Files:**
- Create: `.github/workflows/test.yml`, `.github/workflows/deploy.yml`

**Interfaces:**
- Consumes: the deploy role ARN, ECR repository, Lambda function name, S3 bucket, distribution ID.
- Produces: the deployment pipeline.

- [ ] **Step 1: Write the test workflow**

Create `.github/workflows/test.yml`:

```yaml
name: test

on:
  pull_request:
  push:
    branches: [main]

jobs:
  backend:
    runs-on: ubuntu-24.04
    services:
      postgres:
        image: postgres:16
        env:
          POSTGRES_USER: app
          POSTGRES_PASSWORD: app
          POSTGRES_DB: app
        ports: ["5432:5432"]
        options: >-
          --health-cmd "pg_isready -U app"
          --health-interval 5s --health-retries 10
    steps:
      - uses: actions/checkout@v4
      - uses: astral-sh/setup-uv@v5
      - run: uv sync --all-extras
        working-directory: use_mem0/backend
      - run: uv run pytest -v
        working-directory: use_mem0/backend
        env:
          TEST_DATABASE_URL: postgresql://app:app@localhost:5432/app

  frontend:
    runs-on: ubuntu-24.04
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-node@v4
        with:
          node-version: 22
          cache: npm
          cache-dependency-path: use_mem0/frontend/package-lock.json
      - run: npm ci
        working-directory: use_mem0/frontend
      - run: npm run lint
        working-directory: use_mem0/frontend
      - run: npm run build
        working-directory: use_mem0/frontend

  terraform:
    runs-on: ubuntu-24.04
    steps:
      - uses: actions/checkout@v4
      - uses: hashicorp/setup-terraform@v3
      - run: terraform fmt -check -recursive
        working-directory: infra
```

- [ ] **Step 2: Write the deploy workflow**

Create `.github/workflows/deploy.yml`:

```yaml
name: deploy

on:
  push:
    branches: [main]
  workflow_dispatch:
    inputs:
      image_digest:
        description: "Roll back to this image digest (sha256:...)"
        required: false

concurrency:
  group: deploy-production
  cancel-in-progress: false

permissions:
  id-token: write
  contents: read

env:
  AWS_REGION: us-east-1
  ECR_REPOSITORY: mem0-chatbot
  FUNCTION_NAME: mem0-chatbot

jobs:
  deploy:
    runs-on: ubuntu-24.04-arm
    environment: production
    steps:
      - uses: actions/checkout@v4

      - uses: aws-actions/configure-aws-credentials@v4
        with:
          role-to-assume: ${{ secrets.AWS_DEPLOY_ROLE_ARN }}
          aws-region: ${{ env.AWS_REGION }}

      - id: ecr
        uses: aws-actions/amazon-ecr-login@v2

      # Native arm64 runner: no QEMU emulation, which turns a ~20 minute
      # cross-build into a couple of minutes.
      - id: build
        if: ${{ !inputs.image_digest }}
        uses: docker/build-push-action@v6
        with:
          context: use_mem0
          platforms: linux/arm64
          push: true
          tags: ${{ steps.ecr.outputs.registry }}/${{ env.ECR_REPOSITORY }}:${{ github.sha }}

      - id: digest
        run: |
          if [ -n "${{ inputs.image_digest }}" ]; then
            echo "uri=${{ steps.ecr.outputs.registry }}/${{ env.ECR_REPOSITORY }}@${{ inputs.image_digest }}" >> "$GITHUB_OUTPUT"
          else
            echo "uri=${{ steps.ecr.outputs.registry }}/${{ env.ECR_REPOSITORY }}@${{ steps.build.outputs.digest }}" >> "$GITHUB_OUTPUT"
          fi

      # Migrations run here, once, before any traffic sees the new schema.
      # Never at app startup: concurrent processes race and the loser dies.
      - name: Apply migrations
        run: |
          docker run --rm -e DATABASE_URL="${{ secrets.DATABASE_URL }}" \
            ${{ steps.digest.outputs.uri }} python -m app.db.migrate

      # API first, then the SPA: the SPA is what calls the new API.
      - name: Deploy the function
        run: |
          aws lambda update-function-code \
            --function-name "$FUNCTION_NAME" \
            --image-uri "${{ steps.digest.outputs.uri }}"
          aws lambda wait function-updated --function-name "$FUNCTION_NAME"
          aws lambda publish-version --function-name "$FUNCTION_NAME"

      - uses: actions/setup-node@v4
        with:
          node-version: 22
      - name: Build and upload the SPA
        run: |
          npm ci && npm run build
          aws s3 sync dist "s3://${{ secrets.SPA_BUCKET }}" \
            --delete --exclude index.html \
            --cache-control "public,max-age=31536000,immutable"
          aws s3 cp dist/index.html "s3://${{ secrets.SPA_BUCKET }}/index.html" \
            --cache-control "no-cache"
        working-directory: use_mem0/frontend

      # Only index.html. Hashed assets never need invalidating, and the free
      # allowance is 1,000 paths a month.
      - name: Invalidate index.html
        run: |
          aws cloudfront create-invalidation \
            --distribution-id "${{ secrets.CLOUDFRONT_DISTRIBUTION_ID }}" \
            --paths "/index.html" "/"
```

`DATABASE_URL` is a repository secret here rather than read from SSM, because the migration step runs on the runner rather than in the function. Scope it to a Neon role with DDL rights on this database only.

- [ ] **Step 3: Verify the workflows parse**

Run: `cd .github/workflows && python3 -c "import sys,yaml; [yaml.safe_load(open(f)) for f in ('test.yml','deploy.yml')]; print('both parse')"`
Expected: `both parse`

- [ ] **Step 4: Commit**

```bash
git add .github/workflows/test.yml .github/workflows/deploy.yml
git commit -m "feat: CI pipeline — test, build arm64, migrate, deploy, invalidate

Migrations run as a pipeline step so they never race. API deploys before
the SPA, because the SPA is what calls the new API."
```

---

### Task 10: First deploy runbook

The one task that cannot be automated: the ordering constraint that `PUBLIC_BASE_URL` depends on a distribution that does not exist yet, and the Google console step Terraform cannot perform.

**Files:**
- Create: `docs/runbooks/aws-first-deploy.md`

- [ ] **Step 1: Write the runbook**

Create `docs/runbooks/aws-first-deploy.md`:

````markdown
# First deploy to AWS

There is a deliberate two-pass shape here: `PUBLIC_BASE_URL` must be the
CloudFront domain, and that domain does not exist until CloudFront is created.

## 1. Neon

Create a project in **us-east-1** (colocated with Lambda) and a database.
Copy the **pooled** connection string — the app disables prepared statements
specifically so the pooled endpoint works.

## 2. Seed the secrets

```bash
for name in OPENAI_API_KEY MEM0_API_KEY LANGSMITH_API_KEY \
            GOOGLE_CLIENT_ID GOOGLE_CLIENT_SECRET DATABASE_URL; do
  read -rsp "$name: " value; echo
  aws ssm put-parameter --name "/mem0-chatbot/prod/$name" \
    --value "$value" --type SecureString --overwrite
done

aws ssm put-parameter --name /mem0-chatbot/prod/SESSION_SECRET \
  --value "$(python3 -c 'import secrets;print(secrets.token_urlsafe(32))')" \
  --type SecureString --overwrite
```

**SESSION_SECRET has no rotation story.** `auth/session.py` builds a single
`URLSafeSerializer` with no fallback key, so changing it invalidates every
outstanding cookie and silently logs every user out. Treat it as break-glass.

## 3. First apply (pass one)

`PUBLIC_BASE_URL` is empty for now; login will not work yet, which is expected.

```bash
cd infra
terraform apply -var alert_email=you@example.com -var image_uri=<any-image>
terraform output public_url
```

For `image_uri` on the very first apply, push a placeholder or run the deploy
workflow once and let it fail at the function update, then apply.

## 4. Register the OAuth redirect URI

In the Google Cloud console, under the OAuth client's **Authorized redirect
URIs**, add exactly:

```
https://<distribution-domain>/auth/callback
```

Terraform cannot do this. It is the step that breaks every future environment,
so record the value here whenever it changes.

## 5. Second apply (pass two)

```bash
terraform apply -var alert_email=you@example.com \
                -var public_base_url="https://<distribution-domain>"
```

## 6. GitHub repository secrets

Set `AWS_DEPLOY_ROLE_ARN`, `SPA_BUCKET`, `CLOUDFRONT_DISTRIBUTION_ID`, and
`DATABASE_URL` from the Terraform outputs.

## 7. Deploy and smoke test

Push to `main`, then:

```bash
curl -s https://<domain>/health   # {"status":"ok"}
curl -s https://<domain>/ready    # {"status":"ready"}
curl -sI https://<domain>/        # 200, text/html
```

Then log in through the browser and send one message. Watch for:

- **First message is slow (8–20s).** Expected — that is the cold start.
- **Login loops back to the login page.** `PUBLIC_BASE_URL` is wrong, or the
  redirect URI is not registered.
- **The reply never arrives but no error shows.** Check the CloudFront
  behaviour for `/agent` uses `AllViewerExceptHostHeader` and `compress = false`.

## Rollback

```bash
gh workflow run deploy.yml -f image_digest=sha256:<previous>
```

## Never do this

- `terraform destroy` on the distribution. Its domain is the OAuth redirect
  host; `prevent_destroy` is set for this reason.
- Rotate `SESSION_SECRET` casually. See step 2.
````

- [ ] **Step 2: Commit**

```bash
git add docs/runbooks/aws-first-deploy.md
git commit -m "docs: first-deploy runbook for the AWS serverless stack"
```

---

## Done criteria

- [ ] `cd infra && terraform validate` passes and `terraform plan` is empty after a full apply.
- [ ] `https://<domain>/health` and `/ready` both answer; `/` serves the SPA.
- [ ] Google login completes and a chat turn streams a reply.
- [ ] A push to `main` deploys end to end without manual intervention.
- [ ] The budget alarm and all four CloudWatch alarms exist and the SNS subscription is confirmed.

## Known limitations carried by this design

Recorded so nobody rediscovers them as surprises. Full detail in the spec §9.

1. **8–20 s cold start** on the first message of a session. SnapStart cannot help — it does not support container images.
2. **The Function URL is publicly reachable.** Task 6 injects `X-Origin-Secret` at the CloudFront origin, but **neither plan implements the middleware that checks it** — so today the header is sent and ignored, and the `*.lambda-url.on.aws` host accepts traffic that never passed through CloudFront. Application auth still gates every route that matters, so this is a hardening gap rather than an open door, but close it before the app is public: a middleware comparing the header against an SSM-held value, rejecting mismatches with 403. Even then it is obscurity, not authentication.
3. **You are billed for the full stream**, including time waiting on OpenAI, and a client disconnect does not stop it.
4. **Neon free caps at 0.5 GB**, and LangGraph writes a full checkpoint per turn. Checkpoint pruning is needed before that ceiling arrives; it is not implemented by either plan.
5. **The AWS Free Plan is a 6-month trial.** The account converts or closes when the credits run out.
