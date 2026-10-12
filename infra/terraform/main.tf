# Core resources shared by the briefing pipeline: SES inbound, raw email, and audio storage.
# The two-Lambda briefing flow lives in pipeline.tf; the dashboard in observability.tf.

locals {
  name_prefix           = "${var.project_name}-${var.environment}"
  receipt_rule_name     = "${local.name_prefix}-listen"
  receipt_rule_set_name = "${local.name_prefix}-receipt-rules"
  receipt_rule_arn      = "arn:${data.aws_partition.current.partition}:ses:${var.aws_region}:${data.aws_caller_identity.current.account_id}:receipt-rule-set/${local.receipt_rule_set_name}:receipt-rule/${local.receipt_rule_name}"
  raw_email_prefix      = "raw-email/default/"
  notification_from     = var.notification_from_email != "" ? var.notification_from_email : "no-reply@${var.domain_name}"

  common_tags = {
    Project     = var.project_name
    Environment = var.environment
    ManagedBy   = "terraform"
  }
}

data "aws_caller_identity" "current" {}

data "aws_partition" "current" {}

resource "aws_s3_bucket" "raw_email" {
  bucket_prefix = "${local.name_prefix}-raw-email-"
  tags          = local.common_tags
}

resource "aws_s3_bucket" "audio" {
  bucket_prefix = "${local.name_prefix}-audio-"
  tags          = local.common_tags
}

resource "aws_s3_bucket_policy" "allow_ses_raw_email_puts" {
  bucket = aws_s3_bucket.raw_email.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "AllowSESPuts"
        Effect = "Allow"
        Principal = {
          Service = "ses.amazonaws.com"
        }
        Action   = "s3:PutObject"
        Resource = "${aws_s3_bucket.raw_email.arn}/*"
        Condition = {
          StringEquals = {
            "AWS:SourceAccount" = data.aws_caller_identity.current.account_id
            "AWS:SourceArn"     = local.receipt_rule_arn
          }
        }
      }
    ]
  })
}

resource "aws_ses_receipt_rule_set" "main" {
  rule_set_name = local.receipt_rule_set_name
}

resource "aws_ses_active_receipt_rule_set" "main" {
  rule_set_name = aws_ses_receipt_rule_set.main.rule_set_name
}

# SES only stores the raw MIME. The S3 ObjectCreated notification (pipeline.tf) drives the rest.
resource "aws_ses_receipt_rule" "listen" {
  name          = local.receipt_rule_name
  rule_set_name = aws_ses_receipt_rule_set.main.rule_set_name
  recipients    = [var.inbound_recipient]
  enabled       = true
  scan_enabled  = true

  s3_action {
    bucket_name       = aws_s3_bucket.raw_email.bucket
    object_key_prefix = local.raw_email_prefix
    position          = 1
  }

  depends_on = [aws_s3_bucket_policy.allow_ses_raw_email_puts]
}
