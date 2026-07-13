locals {
  name_prefix = var.project_name
}

data "aws_caller_identity" "current" {}

resource "aws_s3_bucket" "inbound_email" {
  bucket = "${local.name_prefix}-inbound-email-${data.aws_caller_identity.current.account_id}"
}

resource "aws_s3_bucket_public_access_block" "inbound_email" {
  bucket = aws_s3_bucket.inbound_email.id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_server_side_encryption_configuration" "inbound_email" {
  bucket = aws_s3_bucket.inbound_email.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

data "aws_iam_policy_document" "inbound_email_ses" {
  statement {
    sid    = "AllowSESPuts"
    effect = "Allow"

    principals {
      type        = "Service"
      identifiers = ["ses.amazonaws.com"]
    }

    actions   = ["s3:PutObject"]
    resources = ["${aws_s3_bucket.inbound_email.arn}/*"]

    condition {
      test     = "StringEquals"
      variable = "AWS:SourceAccount"
      values   = [data.aws_caller_identity.current.account_id]
    }

    condition {
      test     = "ArnLike"
      variable = "AWS:SourceArn"
      values   = ["arn:aws:ses:${var.aws_region}:${data.aws_caller_identity.current.account_id}:receipt-rule-set/*"]
    }
  }
}

resource "aws_s3_bucket_policy" "inbound_email" {
  bucket = aws_s3_bucket.inbound_email.id
  policy = data.aws_iam_policy_document.inbound_email_ses.json
}

resource "aws_ses_domain_identity" "main" {
  domain = var.domain_name
}

resource "aws_sns_topic" "ses_feedback" {
  name = "${local.name_prefix}-ses-feedback"
}

data "aws_iam_policy_document" "ses_feedback_sns" {
  statement {
    sid    = "AllowSESPublish"
    effect = "Allow"

    principals {
      type        = "Service"
      identifiers = ["ses.amazonaws.com"]
    }

    actions   = ["sns:Publish"]
    resources = [aws_sns_topic.ses_feedback.arn]

    condition {
      test     = "StringEquals"
      variable = "AWS:SourceAccount"
      values   = [data.aws_caller_identity.current.account_id]
    }

    condition {
      test     = "ArnLike"
      variable = "AWS:SourceArn"
      values   = [aws_ses_domain_identity.main.arn]
    }
  }
}

resource "aws_sns_topic_policy" "ses_feedback" {
  arn    = aws_sns_topic.ses_feedback.arn
  policy = data.aws_iam_policy_document.ses_feedback_sns.json
}

resource "aws_sns_topic_subscription" "ses_feedback_email" {
  topic_arn = aws_sns_topic.ses_feedback.arn
  protocol  = "email"
  endpoint  = var.ses_feedback_email
}

resource "aws_ses_identity_notification_topic" "bounce" {
  topic_arn                = aws_sns_topic.ses_feedback.arn
  notification_type        = "Bounce"
  identity                 = aws_ses_domain_identity.main.domain
  include_original_headers = true

  depends_on = [aws_sns_topic_policy.ses_feedback]
}

resource "aws_ses_identity_notification_topic" "complaint" {
  topic_arn                = aws_sns_topic.ses_feedback.arn
  notification_type        = "Complaint"
  identity                 = aws_ses_domain_identity.main.domain
  include_original_headers = true

  depends_on = [aws_sns_topic_policy.ses_feedback]
}

resource "aws_ses_receipt_rule_set" "main" {
  rule_set_name = "${local.name_prefix}-inbound"
}

resource "aws_ses_active_receipt_rule_set" "main" {
  rule_set_name = aws_ses_receipt_rule_set.main.rule_set_name
}

resource "aws_cloudwatch_log_group" "lambda" {
  name              = "/aws/lambda/${local.name_prefix}-supplier-email"
  retention_in_days = 14
}

data "aws_iam_policy_document" "lambda_assume_role" {
  statement {
    actions = ["sts:AssumeRole"]

    principals {
      type        = "Service"
      identifiers = ["lambda.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "lambda" {
  name               = "${local.name_prefix}-supplier-email"
  assume_role_policy = data.aws_iam_policy_document.lambda_assume_role.json
}

data "aws_iam_policy_document" "lambda_permissions" {
  statement {
    actions = [
      "logs:CreateLogGroup",
      "logs:CreateLogStream",
      "logs:PutLogEvents",
    ]
    resources = ["${aws_cloudwatch_log_group.lambda.arn}:*"]
  }

  statement {
    actions = [
      "s3:GetObject",
    ]
    resources = ["${aws_s3_bucket.inbound_email.arn}/*"]
  }

  statement {
    actions = [
      "ses:SendEmail",
      "ses:SendRawEmail",
    ]
    resources = ["*"]
  }
}

resource "aws_iam_role_policy" "lambda" {
  name   = "${local.name_prefix}-supplier-email"
  role   = aws_iam_role.lambda.id
  policy = data.aws_iam_policy_document.lambda_permissions.json
}

resource "aws_lambda_function" "supplier_email" {
  function_name = "${local.name_prefix}-supplier-email"
  role          = aws_iam_role.lambda.arn
  handler       = "lambda_handler.handler"
  runtime       = var.lambda_python_runtime
  timeout       = 120
  memory_size   = 256

  filename         = local.lambda_package_path
  source_code_hash = fileexists(local.lambda_package_path) ? filebase64sha256(local.lambda_package_path) : null

  environment {
    variables = {
      LOYVERSE_PAT                       = var.loyverse_pat
      AIRTABLE_PAT                       = var.airtable_pat
      AIRTABLE_BASE_ID                   = var.airtable_base_id
      AIRTABLE_SUPPLIER_MAPPING_TABLE_ID = var.airtable_supplier_mapping_table_id
      NOTIFICATION_FROM                  = var.notification_from_address
      INBOUND_EMAIL_BUCKET               = aws_s3_bucket.inbound_email.bucket
    }
  }

  depends_on = [
    aws_cloudwatch_log_group.lambda,
    aws_iam_role_policy.lambda,
  ]
}

resource "aws_lambda_permission" "allow_ses" {
  statement_id   = "AllowExecutionFromSES"
  action         = "lambda:InvokeFunction"
  function_name  = aws_lambda_function.supplier_email.function_name
  principal      = "ses.amazonaws.com"
  source_account = data.aws_caller_identity.current.account_id
}

resource "aws_ses_receipt_rule" "supplier_email" {
  name          = "${local.name_prefix}-supplier-email"
  rule_set_name = aws_ses_receipt_rule_set.main.rule_set_name
  recipients    = [var.inbound_email_address]
  enabled       = true
  scan_enabled  = true

  s3_action {
    bucket_name = aws_s3_bucket.inbound_email.bucket
    position    = 1
  }

  lambda_action {
    function_arn    = aws_lambda_function.supplier_email.arn
    invocation_type = "Event"
    position        = 2
  }

  depends_on = [
    aws_lambda_permission.allow_ses,
  ]
}
