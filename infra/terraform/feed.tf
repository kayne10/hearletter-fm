# Private podcast feed (personal use).
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
    sid       = "CloudFrontReadsFeedsAndEpisodes"
    actions   = ["s3:GetObject"]
    resources = ["${aws_s3_bucket.audio.arn}/feeds/*", "${aws_s3_bucket.audio.arn}/episodes/*"]

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
