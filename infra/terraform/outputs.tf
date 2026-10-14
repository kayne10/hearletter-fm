output "raw_email_bucket" {
  value = aws_s3_bucket.raw_email.bucket
}

output "processed_bucket" {
  value = aws_s3_bucket.processed.bucket
}

output "audio_bucket" {
  value = aws_s3_bucket.audio.bucket
}

output "raw_email_queue_url" {
  value = aws_sqs_queue.raw_email.url
}

output "dead_letter_queue_urls" {
  value = {
    raw_email = aws_sqs_queue.raw_email_dlq.url
    episode   = aws_sqs_queue.episode_dlq.url
  }
}

output "lambda_functions" {
  value = {
    briefing_builder = aws_lambda_function.briefing_builder.function_name
    episode_producer = aws_lambda_function.episode_producer.function_name
  }
}

output "briefing_batch_size" {
  description = "SQS batch size, derived from the number of sources in config/briefing.json."
  value       = local.briefing_source_count
}

output "ses_receipt_rule_set" {
  value = aws_ses_receipt_rule_set.main.rule_set_name
}

output "dashboard_url" {
  value = "https://${var.aws_region}.console.aws.amazon.com/cloudwatch/home?region=${var.aws_region}#dashboards/dashboard/${aws_cloudwatch_dashboard.briefing_pipeline.dashboard_name}"
}

output "briefing_flush_schedule" {
  description = "Daily deadline that builds the briefing from whatever has arrived."
  value       = local.briefing_flush_at == null ? "disabled" : "${local.briefing_flush_at} ${local.briefing_config.timezone}"
}
