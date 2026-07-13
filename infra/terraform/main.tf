locals {
  name_prefix           = "${var.project_name}-${var.environment}"
  receipt_rule_name     = "${local.name_prefix}-listen"
  receipt_rule_set_name = "${local.name_prefix}-receipt-rules"

  services = {
    email_parser = {
      function_name = "${local.name_prefix}-email-parser"
      artifact      = "${var.lambda_artifact_dir}/email-parser.zip"
      handler       = "handler.handler"
      timeout       = 30
      memory_size   = 256
    }
    newsletter_cleaner = {
      function_name = "${local.name_prefix}-newsletter-cleaner"
      artifact      = "${var.lambda_artifact_dir}/newsletter-cleaner.zip"
      handler       = "handler.handler"
      timeout       = 60
      memory_size   = 512
    }
    summarizer = {
      function_name = "${local.name_prefix}-summarizer"
      artifact      = "${var.lambda_artifact_dir}/summarizer.zip"
      handler       = "handler.handler"
      timeout       = 120
      memory_size   = 512
    }
    tts = {
      function_name = "${local.name_prefix}-tts"
      artifact      = "${var.lambda_artifact_dir}/tts.zip"
      handler       = "handler.handler"
      timeout       = 180
      memory_size   = 1024
    }
    rss_generator = {
      function_name = "${local.name_prefix}-rss-generator"
      artifact      = "${var.lambda_artifact_dir}/rss-generator.zip"
      handler       = "handler.handler"
      timeout       = 30
      memory_size   = 256
    }
  }

  queue_names = [
    "parsed-newsletter",
    "cleaned-newsletter",
    "briefing-script",
    "generated-episode",
  ]

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

resource "aws_s3_bucket" "artifacts" {
  bucket_prefix = "${local.name_prefix}-artifacts-"
  tags          = local.common_tags
}

resource "aws_s3_bucket" "audio" {
  bucket_prefix = "${local.name_prefix}-audio-"
  tags          = local.common_tags
}

resource "aws_s3_bucket" "feed" {
  bucket_prefix = "${local.name_prefix}-feed-"
  tags          = local.common_tags
}

resource "aws_s3_bucket_public_access_block" "private_buckets" {
  for_each = {
    raw_email = aws_s3_bucket.raw_email.id
    artifacts = aws_s3_bucket.artifacts.id
    audio     = aws_s3_bucket.audio.id
    feed      = aws_s3_bucket.feed.id
  }

  bucket                  = each.value
  block_public_acls       = !var.feed_public_read
  block_public_policy     = !var.feed_public_read
  ignore_public_acls      = !var.feed_public_read
  restrict_public_buckets = !var.feed_public_read
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
            "AWS:SourceArn"     = "arn:${data.aws_partition.current.partition}:ses:${var.aws_region}:${data.aws_caller_identity.current.account_id}:receipt-rule-set/${local.receipt_rule_set_name}:receipt-rule/${local.receipt_rule_name}"
          }
        }
      }
    ]
  })
}

resource "aws_sqs_queue" "dlq" {
  for_each = toset(local.queue_names)

  name                      = "${local.name_prefix}-${each.key}-dlq"
  message_retention_seconds = 1209600
  tags                      = local.common_tags
}

resource "aws_sqs_queue" "pipeline" {
  for_each = toset(local.queue_names)

  name                       = "${local.name_prefix}-${each.key}"
  visibility_timeout_seconds = 360
  message_retention_seconds  = 345600
  redrive_policy = jsonencode({
    deadLetterTargetArn = aws_sqs_queue.dlq[each.key].arn
    maxReceiveCount     = 4
  })
  tags = local.common_tags
}

resource "aws_dynamodb_table" "metadata" {
  name         = "${local.name_prefix}-metadata"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "pk"
  range_key    = "sk"

  attribute {
    name = "pk"
    type = "S"
  }

  attribute {
    name = "sk"
    type = "S"
  }

  ttl {
    attribute_name = "expires_at"
    enabled        = true
  }

  tags = local.common_tags
}

resource "aws_iam_role" "lambda_exec" {
  name = "${local.name_prefix}-lambda-exec"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Action = "sts:AssumeRole"
        Effect = "Allow"
        Principal = {
          Service = "lambda.amazonaws.com"
        }
      }
    ]
  })

  tags = local.common_tags
}

resource "aws_iam_role_policy_attachment" "lambda_basic" {
  role       = aws_iam_role.lambda_exec.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

resource "aws_iam_role_policy" "lambda_pipeline" {
  name = "${local.name_prefix}-lambda-pipeline"
  role = aws_iam_role.lambda_exec.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = [
          "s3:GetObject",
          "s3:PutObject",
          "s3:DeleteObject",
        ]
        Resource = [
          "${aws_s3_bucket.raw_email.arn}/*",
          "${aws_s3_bucket.artifacts.arn}/*",
          "${aws_s3_bucket.audio.arn}/*",
          "${aws_s3_bucket.feed.arn}/*",
        ]
      },
      {
        Effect = "Allow"
        Action = [
          "sqs:SendMessage",
          "sqs:ReceiveMessage",
          "sqs:DeleteMessage",
          "sqs:GetQueueAttributes",
        ]
        Resource = concat(
          [for queue in aws_sqs_queue.pipeline : queue.arn],
          [for queue in aws_sqs_queue.dlq : queue.arn],
        )
      },
      {
        Effect = "Allow"
        Action = [
          "dynamodb:GetItem",
          "dynamodb:PutItem",
          "dynamodb:UpdateItem",
          "dynamodb:Query",
        ]
        Resource = aws_dynamodb_table.metadata.arn
      },
      {
        Effect = "Allow"
        Action = [
          "secretsmanager:GetSecretValue",
        ]
        Resource = "*"
      },
    ]
  })
}

resource "aws_lambda_function" "service" {
  for_each = local.services

  function_name    = each.value.function_name
  filename         = each.value.artifact
  source_code_hash = filebase64sha256(each.value.artifact)
  role             = aws_iam_role.lambda_exec.arn
  handler          = each.value.handler
  runtime          = "python3.11"
  timeout          = each.value.timeout
  memory_size      = each.value.memory_size

  environment {
    variables = {
      ARTIFACT_BUCKET      = aws_s3_bucket.artifacts.bucket
      AUDIO_BUCKET         = aws_s3_bucket.audio.bucket
      FEED_BUCKET          = aws_s3_bucket.feed.bucket
      RAW_EMAIL_BUCKET     = aws_s3_bucket.raw_email.bucket
      RAW_EMAIL_PREFIX     = "raw-email/default/"
      METADATA_TABLE       = aws_dynamodb_table.metadata.name
      PARSED_QUEUE_URL     = aws_sqs_queue.pipeline["parsed-newsletter"].url
      CLEANED_QUEUE_URL    = aws_sqs_queue.pipeline["cleaned-newsletter"].url
      SCRIPT_QUEUE_URL     = aws_sqs_queue.pipeline["briefing-script"].url
      EPISODE_QUEUE_URL    = aws_sqs_queue.pipeline["generated-episode"].url
      TTS_PROVIDER         = "openai"
      POWERTOOLS_SERVICE   = each.key
      POWERTOOLS_LOG_LEVEL = "INFO"
    }
  }

  tags = local.common_tags
}

resource "aws_lambda_event_source_mapping" "cleaner_from_parsed" {
  event_source_arn = aws_sqs_queue.pipeline["parsed-newsletter"].arn
  function_name    = aws_lambda_function.service["newsletter_cleaner"].arn
  batch_size       = 5
}

resource "aws_lambda_event_source_mapping" "summarizer_from_cleaned" {
  event_source_arn = aws_sqs_queue.pipeline["cleaned-newsletter"].arn
  function_name    = aws_lambda_function.service["summarizer"].arn
  batch_size       = 3
}

resource "aws_lambda_event_source_mapping" "tts_from_script" {
  event_source_arn = aws_sqs_queue.pipeline["briefing-script"].arn
  function_name    = aws_lambda_function.service["tts"].arn
  batch_size       = 1
}

resource "aws_lambda_event_source_mapping" "rss_from_episode" {
  event_source_arn = aws_sqs_queue.pipeline["generated-episode"].arn
  function_name    = aws_lambda_function.service["rss_generator"].arn
  batch_size       = 5
}

resource "aws_lambda_permission" "allow_ses_parser" {
  statement_id   = "AllowExecutionFromSES"
  action         = "lambda:InvokeFunction"
  function_name  = aws_lambda_function.service["email_parser"].function_name
  principal      = "ses.amazonaws.com"
  source_account = data.aws_caller_identity.current.account_id
  source_arn     = "arn:${data.aws_partition.current.partition}:ses:${var.aws_region}:${data.aws_caller_identity.current.account_id}:receipt-rule-set/${local.receipt_rule_set_name}:receipt-rule/${local.receipt_rule_name}"
}

resource "aws_ses_receipt_rule_set" "main" {
  rule_set_name = local.receipt_rule_set_name
}

resource "aws_ses_active_receipt_rule_set" "main" {
  rule_set_name = aws_ses_receipt_rule_set.main.rule_set_name
}

resource "aws_ses_receipt_rule" "listen" {
  name          = local.receipt_rule_name
  rule_set_name = aws_ses_receipt_rule_set.main.rule_set_name
  recipients    = [var.inbound_recipient]
  enabled       = true
  scan_enabled  = true

  s3_action {
    bucket_name       = aws_s3_bucket.raw_email.bucket
    object_key_prefix = "raw-email/default/"
    position          = 1
  }

  lambda_action {
    function_arn    = aws_lambda_function.service["email_parser"].arn
    invocation_type = "Event"
    position        = 2
  }

  depends_on = [
    aws_lambda_permission.allow_ses_parser,
    aws_s3_bucket_policy.allow_ses_raw_email_puts,
  ]
}
