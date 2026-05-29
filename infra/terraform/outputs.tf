output "raw_email_bucket" {
  value = aws_s3_bucket.raw_email.bucket
}

output "artifact_bucket" {
  value = aws_s3_bucket.artifacts.bucket
}

output "audio_bucket" {
  value = aws_s3_bucket.audio.bucket
}

output "feed_bucket" {
  value = aws_s3_bucket.feed.bucket
}

output "queue_urls" {
  value = { for name, queue in aws_sqs_queue.pipeline : name => queue.url }
}

output "metadata_table_name" {
  value = aws_dynamodb_table.metadata.name
}

output "ses_receipt_rule_set" {
  value = aws_ses_receipt_rule_set.main.rule_set_name
}

