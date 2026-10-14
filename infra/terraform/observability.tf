# CloudWatch dashboard for the briefing pipeline: S3 -> SQS -> Lambda -> SES.
#
# SES publishes CloudWatch metrics for sending only; inbound mail shows up as raw-email S3
# objects and raw-email SQS messages. S3 request metrics are paid, so they are opt-in.

locals {
  dashboard_name = "${local.name_prefix}-briefing-pipeline"
  region         = var.aws_region

  pipeline_buckets = {
    raw_email = aws_s3_bucket.raw_email.bucket
    processed = aws_s3_bucket.processed.bucket
    audio     = aws_s3_bucket.audio.bucket
  }

  pipeline_lambdas = [
    aws_lambda_function.briefing_builder.function_name,
    aws_lambda_function.episode_producer.function_name,
  ]

  s3_request_metrics_filter = "EntireBucket"

  dashboard_widgets_header = [
    {
      type   = "text"
      x      = 0
      y      = 0
      width  = 24
      height = 2
      properties = {
        markdown = join("\n", [
          "## Hearletter briefing pipeline",
          "SES (S3 action) -> **${aws_s3_bucket.raw_email.bucket}** -> SQS **${aws_sqs_queue.raw_email.name}** -> **${local.briefing_builder_name}** -> **${aws_s3_bucket.processed.bucket}** -> **${local.episode_producer_name}** -> **${aws_s3_bucket.audio.bucket}** + SES email. Anything in a DLQ needs attention.",
        ])
      }
    },
  ]

  dashboard_widgets_sqs = [
    {
      type   = "metric"
      x      = 0
      y      = 2
      width  = 8
      height = 6
      properties = {
        title  = "SQS raw-email: inbound emails"
        region = local.region
        view   = "timeSeries"
        stat   = "Sum"
        period = 300
        metrics = [
          ["AWS/SQS", "NumberOfMessagesSent", "QueueName", aws_sqs_queue.raw_email.name, { label = "Sent (raw emails stored)" }],
          [".", "NumberOfMessagesReceived", ".", ".", { label = "Received by Lambda" }],
          [".", "NumberOfMessagesDeleted", ".", ".", { label = "Deleted (processed)" }],
        ]
      }
    },
    {
      type   = "metric"
      x      = 8
      y      = 2
      width  = 8
      height = 6
      properties = {
        title  = "SQS raw-email: backlog"
        region = local.region
        view   = "timeSeries"
        period = 60
        metrics = [
          ["AWS/SQS", "ApproximateNumberOfMessagesVisible", "QueueName", aws_sqs_queue.raw_email.name, { stat = "Maximum", label = "Visible" }],
          [".", "ApproximateNumberOfMessagesNotVisible", ".", ".", { stat = "Maximum", label = "In flight" }],
          [".", "ApproximateAgeOfOldestMessage", ".", ".", { stat = "Maximum", label = "Oldest message age (s)", yAxis = "right" }],
        ]
      }
    },
    {
      type   = "metric"
      x      = 16
      y      = 2
      width  = 8
      height = 6
      properties = {
        title  = "Dead-letter queues (should be 0)"
        region = local.region
        view   = "singleValue"
        stat   = "Maximum"
        period = 300
        metrics = [
          ["AWS/SQS", "ApproximateNumberOfMessagesVisible", "QueueName", aws_sqs_queue.raw_email_dlq.name, { label = "raw-email DLQ" }],
          [".", ".", ".", aws_sqs_queue.episode_dlq.name, { label = "episode DLQ" }],
        ]
      }
    },
  ]

  dashboard_widgets_lambda = [
    {
      type   = "metric"
      x      = 0
      y      = 8
      width  = 8
      height = 6
      properties = {
        title  = "Lambda invocations / errors / throttles"
        region = local.region
        view   = "timeSeries"
        stat   = "Sum"
        period = 300
        # concat (not flatten): each metric row must stay a list.
        metrics = concat(
          [for fn in local.pipeline_lambdas : ["AWS/Lambda", "Invocations", "FunctionName", fn, { label = "${fn} invocations" }]],
          [for fn in local.pipeline_lambdas : ["AWS/Lambda", "Errors", "FunctionName", fn, { label = "${fn} errors" }]],
          [for fn in local.pipeline_lambdas : ["AWS/Lambda", "Throttles", "FunctionName", fn, { label = "${fn} throttles" }]],
        )
      }
    },
    {
      type   = "metric"
      x      = 8
      y      = 8
      width  = 8
      height = 6
      properties = {
        title  = "Lambda duration (ms)"
        region = local.region
        view   = "timeSeries"
        period = 300
        annotations = {
          horizontal = [
            { label = "builder timeout", value = local.briefing_builder_timeout * 1000 },
            { label = "producer timeout", value = local.episode_producer_timeout * 1000 },
          ]
        }
        metrics = concat(
          [for fn in local.pipeline_lambdas : ["AWS/Lambda", "Duration", "FunctionName", fn, { stat = "Average", label = "${fn} avg" }]],
          [for fn in local.pipeline_lambdas : ["AWS/Lambda", "Duration", "FunctionName", fn, { stat = "Maximum", label = "${fn} max" }]],
        )
      }
    },
    {
      type   = "metric"
      x      = 16
      y      = 8
      width  = 8
      height = 6
      properties = {
        title  = "episode-producer async delivery"
        region = local.region
        view   = "timeSeries"
        stat   = "Sum"
        period = 300
        metrics = [
          ["AWS/Lambda", "AsyncEventsReceived", "FunctionName", local.episode_producer_name, { label = "Script events received" }],
          [".", "AsyncEventsDropped", ".", ".", { label = "Dropped" }],
          [".", "DestinationDeliveryFailures", ".", ".", { label = "DLQ delivery failures" }],
          [".", "AsyncEventAge", ".", ".", { stat = "Maximum", label = "Event age (ms)", yAxis = "right" }],
        ]
      }
    },
  ]

  dashboard_widgets_ses_s3 = [
    {
      type   = "metric"
      x      = 0
      y      = 14
      width  = 8
      height = 6
      properties = {
        title  = "SES outbound (account-wide, ${local.region})"
        region = local.region
        view   = "timeSeries"
        stat   = "Sum"
        period = 3600
        metrics = [
          ["AWS/SES", "Send", { label = "Sent" }],
          [".", "Delivery", { label = "Delivered" }],
          [".", "Bounce", { label = "Bounced" }],
          [".", "Complaint", { label = "Complaints" }],
          [".", "Reject", { label = "Rejected" }],
        ]
      }
    },
    {
      type   = "metric"
      x      = 8
      y      = 14
      width  = 8
      height = 6
      properties = {
        title  = "S3 object count (daily)"
        region = local.region
        view   = "timeSeries"
        stat   = "Average"
        period = 86400
        metrics = [
          for name, bucket in local.pipeline_buckets :
          ["AWS/S3", "NumberOfObjects", "BucketName", bucket, "StorageType", "AllStorageTypes", { label = name }]
        ]
      }
    },
    {
      type   = "metric"
      x      = 16
      y      = 14
      width  = 8
      height = 6
      properties = {
        title  = "S3 bucket size bytes (daily)"
        region = local.region
        view   = "timeSeries"
        stat   = "Average"
        period = 86400
        metrics = [
          for name, bucket in local.pipeline_buckets :
          ["AWS/S3", "BucketSizeBytes", "BucketName", bucket, "StorageType", "StandardStorage", { label = name }]
        ]
      }
    },
  ]

  dashboard_widgets_s3_requests = var.enable_s3_request_metrics ? [
    {
      type   = "metric"
      x      = 0
      y      = 20
      width  = 24
      height = 6
      properties = {
        title  = "S3 PUT / GET requests"
        region = local.region
        view   = "timeSeries"
        stat   = "Sum"
        period = 300
        metrics = concat(
          [for name, bucket in local.pipeline_buckets : ["AWS/S3", "PutRequests", "BucketName", bucket, "FilterId", local.s3_request_metrics_filter, { label = "${name} PUT" }]],
          [for name, bucket in local.pipeline_buckets : ["AWS/S3", "GetRequests", "BucketName", bucket, "FilterId", local.s3_request_metrics_filter, { label = "${name} GET" }]],
        )
      }
    },
  ] : []

  dashboard_widgets_logs = [
    {
      type   = "log"
      x      = 0
      y      = 26
      width  = 24
      height = 8
      properties = {
        title  = "Pipeline events (staged, ignored, briefings, episodes, failures)"
        region = local.region
        view   = "table"
        query = join(" ", [
          "SOURCE '${aws_cloudwatch_log_group.briefing_builder.name}'",
          "| SOURCE '${aws_cloudwatch_log_group.episode_producer.name}'",
          "| fields @timestamp, @log, message, @message",
          "| filter message in ['briefing_batch_processed', 'briefing_flush_processed', 'script_writer_failed', 'episodes_produced', 'raw_email_failed', 'briefing_failed']",
          "| sort @timestamp desc",
          "| limit 50",
        ])
      }
    },
    {
      type   = "log"
      x      = 0
      y      = 34
      width  = 24
      height = 6
      properties = {
        title  = "Lambda errors"
        region = local.region
        view   = "table"
        query = join(" ", [
          "SOURCE '${aws_cloudwatch_log_group.briefing_builder.name}'",
          "| SOURCE '${aws_cloudwatch_log_group.episode_producer.name}'",
          "| fields @timestamp, @log, @message",
          "| filter @message like /(?i)(error|exception|traceback|task timed out)/",
          "| sort @timestamp desc",
          "| limit 50",
        ])
      }
    },
  ]
}

resource "aws_s3_bucket_metric" "pipeline_requests" {
  for_each = var.enable_s3_request_metrics ? local.pipeline_buckets : {}

  bucket = each.value
  name   = local.s3_request_metrics_filter
}

resource "aws_cloudwatch_dashboard" "briefing_pipeline" {
  dashboard_name = local.dashboard_name

  dashboard_body = jsonencode({
    widgets = concat(
      local.dashboard_widgets_header,
      local.dashboard_widgets_sqs,
      local.dashboard_widgets_lambda,
      local.dashboard_widgets_ses_s3,
      local.dashboard_widgets_s3_requests,
      local.dashboard_widgets_logs,
    )
  })
}
