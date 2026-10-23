# Private podcast feed and web player (personal use).
#
# CloudFront serves the audio bucket through origin access control, so the bucket stays private.
# A secret token in the URL path gates access: https://<distribution>/<token>/feeds/<you>/feed.xml
# Anyone holding that URL can listen, so treat it like a password. Rotate it with:
#   terraform apply -replace=random_password.feed_token   (then re-subscribe)
# Per-user tokens are the step for multiple listeners.

resource "random_password" "feed_token" {
  length  = 32
  special = false
}

locals {
  feed_base_url = "https://${aws_cloudfront_distribution.feed.domain_name}/${random_password.feed_token.result}"
}

resource "aws_cloudfront_origin_access_control" "audio" {
  name                              = "${local.name_prefix}-audio"
  description                       = "CloudFront access to the private audio bucket"
  origin_access_control_origin_type = "s3"
  signing_behavior                  = "always"
  signing_protocol                  = "sigv4"
}

resource "aws_cloudfront_function" "feed_token" {
  name    = "${local.name_prefix}-feed-token"
  runtime = "cloudfront-js-2.0"
  comment = "Require the secret path token for the private podcast feed"
  publish = true
  code    = templatefile("${path.module}/feed_token.js.tftpl", { token = random_password.feed_token.result })
}

data "aws_cloudfront_cache_policy" "caching_optimized" {
  name = "Managed-CachingOptimized"
}

resource "aws_cloudfront_distribution" "feed" {
  enabled     = true
  comment     = "${local.name_prefix} private podcast feed"
  price_class = "PriceClass_100"

  origin {
    origin_id                = "audio"
    domain_name              = aws_s3_bucket.audio.bucket_regional_domain_name
    origin_access_control_id = aws_cloudfront_origin_access_control.audio.id
  }

  default_cache_behavior {
    target_origin_id       = "audio"
    viewer_protocol_policy = "redirect-to-https"
    allowed_methods        = ["GET", "HEAD"]
    cached_methods         = ["GET", "HEAD"]
    cache_policy_id        = data.aws_cloudfront_cache_policy.caching_optimized.id
    compress               = true

    function_association {
      event_type   = "viewer-request"
      function_arn = aws_cloudfront_function.feed_token.arn
    }
  }

  restrictions {
    geo_restriction {
      restriction_type = "none"
    }
  }

  viewer_certificate {
    cloudfront_default_certificate = true
  }

  tags = local.common_tags
}

data "aws_iam_policy_document" "audio_bucket" {
  statement {
    sid     = "CloudFrontReadsFeedsAndEpisodes"
    actions = ["s3:GetObject"]
    resources = [
      "${aws_s3_bucket.audio.arn}/feeds/*",
      "${aws_s3_bucket.audio.arn}/episodes/*",
      "${aws_s3_bucket.audio.arn}/player/*",
    ]

    principals {
      type        = "Service"
      identifiers = ["cloudfront.amazonaws.com"]
    }

    condition {
      test     = "StringEquals"
      variable = "AWS:SourceArn"
      values   = [aws_cloudfront_distribution.feed.arn]
    }
  }
}

resource "aws_s3_bucket_policy" "audio" {
  bucket     = aws_s3_bucket.audio.id
  policy     = data.aws_iam_policy_document.audio_bucket.json
  depends_on = [aws_s3_bucket_public_access_block.pipeline]
}

# --- Web player ------------------------------------------------------------------------------
# Static files from web/player/, served at <feed base>/player/. The page reads
# feeds/<forwarder>/episodes.json, which the producer writes next to feed.xml. Editing a file
# and applying re-uploads it (etag); CloudFront and browsers pick it up within 5 minutes.

locals {
  player_source_dir = "${path.module}/../../web/player"
  player_content_types = {
    html = "text/html; charset=utf-8"
    js   = "text/javascript; charset=utf-8"
    css  = "text/css; charset=utf-8"
  }
}

resource "aws_s3_object" "player" {
  for_each = fileset(local.player_source_dir, "*.{html,js,css}")

  bucket        = aws_s3_bucket.audio.id
  key           = "player/${each.value}"
  source        = "${local.player_source_dir}/${each.value}"
  etag          = filemd5("${local.player_source_dir}/${each.value}")
  content_type  = local.player_content_types[regex("[a-z]+$", each.value)]
  cache_control = "max-age=300"
  tags          = local.common_tags
}
