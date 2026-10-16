# Briefing pipeline:
#   SES -> S3 raw-email -> SQS raw-email -> briefing-builder -> S3 processed (scripts/**/script.json)
#   -> episode-producer -> S3 audio (MP3) + SES email from no-reply@<domain>

locals {
  briefing_config       = jsondecode(file("${path.module}/../../config/briefing.json"))
  briefing_source_count = length(local.briefing_config.sources)

  briefing_builder_name = "${local.name_prefix}-briefing-builder"
  episode_producer_name = "${local.name_prefix}-episode-producer"

  # 300s leaves room for the Claude script rewrite (client timeout 240s).
  briefing_builder_timeout = 300
  episode_producer_timeout = 300
}

# --- Storage -------------------------------------------------------------------------------

resource "aws_s3_bucket" "processed" {
  bucket_prefix = "${local.name_prefix}-processed-"
  tags          = local.common_tags
}

resource "aws_s3_bucket_lifecycle_configuration" "processed" {
  bucket = aws_s3_bucket.processed.id

  rule {
    id     = "expire-staged-newsletters"
    status = "Enabled"
    filter {
      prefix = "inbox/"
    }
    expiration {
      days = 30
    }
  }
}

resource "aws_s3_bucket_public_access_block" "pipeline" {
  for_each = {
    raw_email = aws_s3_bucket.raw_email.id
    audio     = aws_s3_bucket.audio.id
    processed = aws_s3_bucket.processed.id
  }

  bucket                  = each.value
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

# --- Queues --------------------------------------------------------------------------------

resource "aws_sqs_queue" "raw_email_dlq" {
  name                      = "${local.name_prefix}-raw-email-dlq"
  message_retention_seconds = 1209600
  tags                      = local.common_tags
}

resource "aws_sqs_queue" "raw_email" {
  name = "${local.name_prefix}-raw-email"
  # AWS guidance: >= 6x the function timeout plus the batching window.
  visibility_timeout_seconds = 6 * local.briefing_builder_timeout + var.raw_email_batch_window_seconds
  message_retention_seconds  = 345600
  redrive_policy = jsonencode({
    deadLetterTargetArn = aws_sqs_queue.raw_email_dlq.arn
    maxReceiveCount     = 5
  })
  tags = local.common_tags
}

resource "aws_sqs_queue_policy" "raw_email" {
  queue_url = aws_sqs_queue.raw_email.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid       = "AllowRawEmailBucketNotifications"
        Effect    = "Allow"
        Principal = { Service = "s3.amazonaws.com" }
        Action    = "sqs:SendMessage"
        Resource  = aws_sqs_queue.raw_email.arn
        Condition = {
          ArnEquals    = { "aws:SourceArn" = aws_s3_bucket.raw_email.arn }
          StringEquals = { "aws:SourceAccount" = data.aws_caller_identity.current.account_id }
        }
      }
    ]
  })
}

# Async invocations of episode-producer that still fail after retries land here.
resource "aws_sqs_queue" "episode_dlq" {
  name                      = "${local.name_prefix}-episode-dlq"
  message_retention_seconds = 1209600
  tags                      = local.common_tags
}

resource "aws_s3_bucket_notification" "raw_email" {
  bucket = aws_s3_bucket.raw_email.id

  queue {
    queue_arn     = aws_sqs_queue.raw_email.arn
    events        = ["s3:ObjectCreated:*"]
    filter_prefix = local.raw_email_prefix
  }

  depends_on = [aws_sqs_queue_policy.raw_email]
}

# --- IAM -----------------------------------------------------------------------------------

data "aws_iam_policy_document" "lambda_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["lambda.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "briefing_builder" {
  name               = local.briefing_builder_name
  assume_role_policy = data.aws_iam_policy_document.lambda_assume.json
  tags               = local.common_tags
}

resource "aws_iam_role_policy_attachment" "briefing_builder_logs" {
  role       = aws_iam_role.briefing_builder.name
  policy_arn = "arn:${data.aws_partition.current.partition}:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

data "aws_iam_policy_document" "briefing_builder" {
  statement {
    sid       = "ReadRawEmail"
    actions   = ["s3:GetObject"]
    resources = ["${aws_s3_bucket.raw_email.arn}/*"]
  }

  statement {
    sid       = "ReadWriteProcessed"
    actions   = ["s3:GetObject", "s3:PutObject"]
    resources = ["${aws_s3_bucket.processed.arn}/*"]
  }

  # ListBucket lets HeadObject return 404 (not 403) and lets the builder list staged newsletters.
  statement {
    sid       = "ListProcessed"
    actions   = ["s3:ListBucket"]
    resources = [aws_s3_bucket.processed.arn]
  }

  statement {
    sid       = "ConsumeRawEmailQueue"
    actions   = ["sqs:ReceiveMessage", "sqs:DeleteMessage", "sqs:GetQueueAttributes"]
    resources = [aws_sqs_queue.raw_email.arn]
  }

  dynamic "statement" {
    for_each = var.anthropic_api_key_secret_arn != "" ? [var.anthropic_api_key_secret_arn] : []
    content {
      sid       = "ReadAnthropicKey"
      actions   = ["secretsmanager:GetSecretValue"]
      resources = [statement.value]
    }
  }
}

resource "aws_iam_role_policy" "briefing_builder" {
  name   = local.briefing_builder_name
  role   = aws_iam_role.briefing_builder.id
  policy = data.aws_iam_policy_document.briefing_builder.json
}

resource "aws_iam_role" "episode_producer" {
  name               = local.episode_producer_name
  assume_role_policy = data.aws_iam_policy_document.lambda_assume.json
  tags               = local.common_tags
}

resource "aws_iam_role_policy_attachment" "episode_producer_logs" {
  role       = aws_iam_role.episode_producer.name
  policy_arn = "arn:${data.aws_partition.current.partition}:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

data "aws_iam_policy_document" "episode_producer" {
  statement {
    sid       = "ReadScripts"
    actions   = ["s3:GetObject"]
    resources = ["${aws_s3_bucket.processed.arn}/*"]
  }

  statement {
    sid       = "ReadWriteAudio"
    actions   = ["s3:GetObject", "s3:PutObject"]
    resources = ["${aws_s3_bucket.audio.arn}/*"]
  }

  statement {
    sid       = "ListAudio"
    actions   = ["s3:ListBucket"]
    resources = [aws_s3_bucket.audio.arn]
  }

  dynamic "statement" {
    for_each = var.openai_api_key_secret_arn != "" ? [var.openai_api_key_secret_arn] : []
    content {
      sid       = "ReadOpenAIKey"
      actions   = ["secretsmanager:GetSecretValue"]
      resources = [statement.value]
    }
  }

  statement {
    sid       = "Speech"
    actions   = ["polly:SynthesizeSpeech"]
    resources = ["*"]
  }

  statement {
    sid       = "SendEpisodeEmail"
    actions   = ["ses:SendEmail", "ses:SendRawEmail"]
    resources = ["*"]
    condition {
      test     = "StringEquals"
      variable = "ses:FromAddress"
      values   = [local.notification_from]
    }
  }

  statement {
    sid       = "OnFailureDestination"
    actions   = ["sqs:SendMessage"]
    resources = [aws_sqs_queue.episode_dlq.arn]
  }
}

resource "aws_iam_role_policy" "episode_producer" {
  name   = local.episode_producer_name
  role   = aws_iam_role.episode_producer.id
  policy = data.aws_iam_policy_document.episode_producer.json
}

# --- Lambdas -------------------------------------------------------------------------------

resource "aws_cloudwatch_log_group" "briefing_builder" {
  name              = "/aws/lambda/${local.briefing_builder_name}"
  retention_in_days = var.log_retention_days
  tags              = local.common_tags
}

resource "aws_cloudwatch_log_group" "episode_producer" {
  name              = "/aws/lambda/${local.episode_producer_name}"
  retention_in_days = var.log_retention_days
  tags              = local.common_tags
}

resource "aws_lambda_function" "briefing_builder" {
  function_name    = local.briefing_builder_name
  filename         = "${var.lambda_artifact_dir}/briefing-builder.zip"
  source_code_hash = filebase64sha256("${var.lambda_artifact_dir}/briefing-builder.zip")
  role             = aws_iam_role.briefing_builder.arn
  handler          = "handler.handler"
  runtime          = "python3.11"
  timeout          = local.briefing_builder_timeout
  memory_size      = 512

  environment {
    variables = {
      PROCESSED_BUCKET = aws_s3_bucket.processed.bucket
      # Claude rewrites the script when a key secret is set; otherwise the template is used.
      ANTHROPIC_API_KEY_SECRET_ARN = var.anthropic_api_key_secret_arn
      SCRIPT_WRITER                = var.script_writer
      SCRIPT_MODEL                 = var.script_model
      SCRIPT_EFFORT                = var.script_effort
    }
  }

  depends_on = [aws_cloudwatch_log_group.briefing_builder]
  tags       = local.common_tags
}

resource "aws_lambda_event_source_mapping" "briefing_builder_from_raw_email" {
  event_source_arn                   = aws_sqs_queue.raw_email.arn
  function_name                      = aws_lambda_function.briefing_builder.arn
  batch_size                         = local.briefing_source_count
  maximum_batching_window_in_seconds = var.raw_email_batch_window_seconds
  function_response_types            = ["ReportBatchItemFailures"]

  # Fewer concurrent pollers means fewer split batches. 2 is the minimum AWS allows.
  scaling_config {
    maximum_concurrency = 2
  }
}

resource "aws_lambda_function" "episode_producer" {
  function_name    = local.episode_producer_name
  filename         = "${var.lambda_artifact_dir}/episode-producer.zip"
  source_code_hash = filebase64sha256("${var.lambda_artifact_dir}/episode-producer.zip")
  role             = aws_iam_role.episode_producer.arn
  handler          = "handler.handler"
  runtime          = "python3.11"
  timeout          = local.episode_producer_timeout
  memory_size      = 512

  environment {
    variables = {
      AUDIO_BUCKET              = aws_s3_bucket.audio.bucket
      NOTIFICATION_FROM_EMAIL   = local.notification_from
      AUDIO_URL_TTL_SECONDS     = tostring(var.audio_url_ttl_seconds)
      TTS_PROVIDER              = var.tts_provider
      OPENAI_API_KEY_SECRET_ARN = var.openai_api_key_secret_arn
      OPENAI_TTS_MODEL          = var.openai_tts_model
      OPENAI_TTS_VOICE          = var.openai_tts_voice
      OPENAI_TTS_INSTRUCTIONS   = var.openai_tts_instructions
      # Polly is called cross-region: the generative engine isn't offered in every region.
      POLLY_REGION   = var.polly_region
      POLLY_ENGINE   = var.polly_engine
      POLLY_VOICE_ID = var.polly_voice_id
    }
  }

  depends_on = [aws_cloudwatch_log_group.episode_producer]
  tags       = local.common_tags
}

resource "aws_lambda_function_event_invoke_config" "episode_producer" {
  function_name                = aws_lambda_function.episode_producer.function_name
  maximum_retry_attempts       = 2
  maximum_event_age_in_seconds = 3600

  destination_config {
    on_failure {
      destination = aws_sqs_queue.episode_dlq.arn
    }
  }
}

resource "aws_lambda_permission" "allow_processed_bucket" {
  statement_id   = "AllowExecutionFromProcessedBucket"
  action         = "lambda:InvokeFunction"
  function_name  = aws_lambda_function.episode_producer.function_name
  principal      = "s3.amazonaws.com"
  source_arn     = aws_s3_bucket.processed.arn
  source_account = data.aws_caller_identity.current.account_id
}

resource "aws_s3_bucket_notification" "processed" {
  bucket = aws_s3_bucket.processed.id

  lambda_function {
    lambda_function_arn = aws_lambda_function.episode_producer.arn
    events              = ["s3:ObjectCreated:*"]
    filter_prefix       = "scripts/"
    filter_suffix       = "script.json"
  }

  depends_on = [aws_lambda_permission.allow_processed_bucket]
}

# --- Optional daily flush ------------------------------------------------------------------
# Only created when config/briefing.json sets flush_at (local HH:MM). It builds today's briefing
# from whatever has been staged. Off by default; run the flush manually instead (see docs).

locals {
  briefing_flush_at = try(local.briefing_config.flush_at, null)
  # Defaulted so indexing is valid even when the schedule is disabled (count = 0).
  briefing_flush_hm = split(":", coalesce(local.briefing_flush_at, "00:00"))
}

data "aws_iam_policy_document" "scheduler_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["scheduler.amazonaws.com"]
    }
    condition {
      test     = "StringEquals"
      variable = "aws:SourceAccount"
      values   = [data.aws_caller_identity.current.account_id]
    }
  }
}

resource "aws_iam_role" "briefing_flush_scheduler" {
  count              = local.briefing_flush_at == null ? 0 : 1
  name               = "${local.name_prefix}-briefing-flush-scheduler"
  assume_role_policy = data.aws_iam_policy_document.scheduler_assume.json
  tags               = local.common_tags
}

resource "aws_iam_role_policy" "briefing_flush_scheduler" {
  count = local.briefing_flush_at == null ? 0 : 1
  name  = "${local.name_prefix}-briefing-flush-scheduler"
  role  = aws_iam_role.briefing_flush_scheduler[0].id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect   = "Allow"
        Action   = "lambda:InvokeFunction"
        Resource = aws_lambda_function.briefing_builder.arn
      }
    ]
  })
}

resource "aws_scheduler_schedule" "briefing_flush" {
  count = local.briefing_flush_at == null ? 0 : 1
  name  = "${local.name_prefix}-briefing-flush"

  schedule_expression          = "cron(${tonumber(local.briefing_flush_hm[1])} ${tonumber(local.briefing_flush_hm[0])} * * ? *)"
  schedule_expression_timezone = local.briefing_config.timezone

  flexible_time_window {
    mode = "OFF"
  }

  target {
    arn      = aws_lambda_function.briefing_builder.arn
    role_arn = aws_iam_role.briefing_flush_scheduler[0].arn
    input    = jsonencode({ action = "flush" })

    retry_policy {
      maximum_retry_attempts       = 3
      maximum_event_age_in_seconds = 3600
    }
  }
}
