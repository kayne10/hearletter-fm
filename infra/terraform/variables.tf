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
  description = "TTS provider used by the tts Lambda. Code defaults to polly; this stack currently deploys openai."
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

variable "ffmpeg_path" {
  description = "Path to the FFmpeg executable available to the audio-mixer Lambda."
  type        = string
  default     = "ffmpeg"
}

variable "mixer_asset_root" {
  description = "Local asset root in the Lambda runtime or layer."
  type        = string
  default     = "/opt/assets"
}

variable "mixer_normalize" {
  description = "Whether the audio mixer applies loudness normalization."
  type        = bool
  default     = true
}

variable "mixer_allow_passthrough_on_error" {
  description = "Temporarily allow audio-mixer to publish narration as the final MP3 when FFmpeg is unavailable."
  type        = bool
  default     = true
}

variable "mixer_intro_music" {
  description = "Intro music path relative to mixer_asset_root. Missing files are skipped."
  type        = string
  default     = "intro/morning_theme.mp3"
}

variable "mixer_outro_music" {
  description = "Outro music path relative to mixer_asset_root. Missing files are skipped."
  type        = string
  default     = "outro/closing_theme.mp3"
}

variable "mixer_transition_sound" {
  description = "Transition sound path relative to mixer_asset_root. Leave empty to derive from show_style.transition."
  type        = string
  default     = ""
}

variable "show_style" {
  description = "Default semantic show style carried through script, TTS, and audio mixer stages."
  type = object({
    voice      = string
    pace       = string
    music      = string
    transition = string
  })
  default = {
    voice      = "calm"
    pace       = "slow"
    music      = "soft"
    transition = "light_chime"
  }
}
