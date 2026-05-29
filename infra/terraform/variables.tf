variable "project_name" {
  description = "Name prefix for AWS resources."
  type        = string
  default     = "hearletter-fm"
}

variable "aws_region" {
  description = "AWS region. Must support SES inbound receiving."
  type        = string
  default     = "us-east-1"
}

variable "environment" {
  description = "Deployment environment name."
  type        = string
  default     = "dev"
}

variable "domain_name" {
  description = "Verified SES domain that receives forwarded newsletters."
  type        = string
}

variable "inbound_recipient" {
  description = "Email address handled by SES, for example listen@example.com."
  type        = string
}

variable "lambda_artifact_dir" {
  description = "Directory containing pre-built Lambda zip artifacts."
  type        = string
  default     = "../../artifacts/lambda"
}

variable "feed_public_read" {
  description = "Whether the RSS/audio buckets are publicly readable by direct S3 URL. Prefer CloudFront later."
  type        = bool
  default     = false
}

