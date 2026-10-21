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

variable "notification_from_email" {
  description = "Verified SES email address used as the sender for MP3-ready notifications. Defaults to no-reply@domain_name."
  type        = string
  default     = ""
}

variable "audio_url_ttl_seconds" {
  description = "Expiration time for presigned MP3 links emailed to users."
  type        = number
  default     = 604800
}

variable "tts_provider" {
  description = "TTS provider used by episode-producer: openai or polly."
  type        = string
  default     = "openai"

  validation {
    condition     = contains(["polly", "openai"], var.tts_provider)
    error_message = "tts_provider must be either polly or openai."
  }
}

variable "openai_api_key_secret_arn" {
  description = "Secrets Manager secret ARN containing the OpenAI API key as plain text or JSON with api_key."
  type        = string
  default     = ""
}

variable "openai_tts_model" {
  description = "OpenAI speech model."
  type        = string
  default     = "gpt-4o-mini-tts"
}

variable "openai_tts_voice" {
  description = "Optional explicit OpenAI speech voice. Leave empty to derive from show_style.voice."
  type        = string
  default     = ""
}

variable "openai_tts_instructions" {
  description = "Style instructions sent to OpenAI TTS."
  type        = string
  default     = "Sound like a warm, natural morning podcast host. Keep the delivery conversational, clear, lightly energetic, and never salesy."
}

variable "raw_email_batch_window_seconds" {
  description = "Max seconds SQS waits to fill a briefing-builder batch (batch size = number of sources)."
  type        = number
  default     = 60

  validation {
    condition     = var.raw_email_batch_window_seconds >= 0 && var.raw_email_batch_window_seconds <= 300
    error_message = "raw_email_batch_window_seconds must be between 0 and 300."
  }
}

variable "log_retention_days" {
  description = "CloudWatch log retention for the briefing Lambdas."
  type        = number
  default     = 30
}

variable "enable_s3_request_metrics" {
  description = "Enable paid S3 request metrics (PUT/GET counts) for the pipeline buckets and show them on the dashboard."
  type        = bool
  default     = false
}

variable "anthropic_api_key_secret_arn" {
  description = "Secrets Manager ARN holding the Anthropic API key (raw or JSON with api_key). Empty disables the Claude script writer."
  type        = string
  default     = ""
}

variable "script_writer" {
  description = "claude: Claude rewrites the briefing script (needs anthropic_api_key_secret_arn). template: deterministic script only."
  type        = string
  default     = "claude"

  validation {
    condition     = contains(["claude", "template"], var.script_writer)
    error_message = "script_writer must be claude or template."
  }
}

variable "script_model" {
  description = "Claude model that writes the briefing script."
  type        = string
  default     = "claude-opus-5-5"
}

variable "script_effort" {
  description = "Claude effort level for the script rewrite."
  type        = string
  default     = "medium"

  validation {
    condition     = contains(["low", "medium", "high", "xhigh", "max"], var.script_effort)
    error_message = "script_effort must be one of low, medium, high, xhigh, max."
  }
}

variable "polly_region" {
  description = "Region for Polly calls. Independent of aws_region because the generative engine is only offered in some regions (us-east-2 has none)."
  type        = string
  default     = "us-east-1"
}

variable "polly_engine" {
  description = "Polly engine: long-form (built for long narration), generative, neural, or standard. Must be offered in polly_region for polly_voice_id."
  type        = string
  default     = "long-form"

  validation {
    condition     = contains(["generative", "long-form", "neural", "standard"], var.polly_engine)
    error_message = "polly_engine must be generative, long-form, neural, or standard."
  }
}

variable "polly_voice_id" {
  description = "Polly voice id; must support polly_engine. Long-form en-US voices in us-east-1: Danielle, Ruth, Gregory, Patrick (Joanna is generative only)."
  type        = string
  default     = "Danielle"
}
