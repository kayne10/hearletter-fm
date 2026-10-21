.PHONY: test test-feed-function compile local-briefing episode feed package-lambdas package-lambdas-no-deps terraform-fmt terraform-fmt-check

AWS_REGION ?= us-east-2
BRIEFING_BUILDER ?= hearletter-fm-dev-briefing-builder
EPISODE_PRODUCER ?= hearletter-fm-dev-episode-producer

test:
	python3 -m pytest

# CloudFront feed-token function (JavaScript); needs Node.
test-feed-function:
	node tests/cloudfront/feed_token.test.js

compile:
	python3 -m compileall packages services tests

local-briefing:
	python3 scripts/run_local_briefing.py tests/fixtures/raw_email --min-sources 1

package-lambdas:
	python3 scripts/package_lambdas.py

package-lambdas-no-deps:
	python3 scripts/package_lambdas.py --skip-deps

terraform-fmt:
	terraform fmt -recursive infra/terraform

terraform-fmt-check:
	terraform fmt -check -recursive infra/terraform

# Build today's episode from whatever newsletters have arrived (manual flush), then the
# producer emails the MP3. Safe to repeat: unchanged content builds nothing new.
episode:
	@out=$$(mktemp); \
	err=$$(aws lambda invoke --function-name $(BRIEFING_BUILDER) --region $(AWS_REGION) \
		--cli-binary-format raw-in-base64-out --payload '{"action":"flush"}' \
		--query FunctionError --output text "$$out") || { rm -f "$$out"; exit 1; }; \
	if [ "$$err" != "None" ]; then cat "$$out"; echo; rm -f "$$out"; exit 1; fi; \
	python3 scripts/describe_flush.py "$$out"; status=$$?; rm -f "$$out"; exit $$status

# Rebuild every private podcast feed from existing episodes and print the subscribe URLs.
# The URLs contain the secret feed token: paste into your podcast app, don't share.
feed:
	@out=$$(mktemp); \
	err=$$(aws lambda invoke --function-name $(EPISODE_PRODUCER) --region $(AWS_REGION) \
		--cli-binary-format raw-in-base64-out --payload '{"action":"rebuild_feeds"}' \
		--query FunctionError --output text "$$out") || { rm -f "$$out"; exit 1; }; \
	if [ "$$err" != "None" ]; then cat "$$out"; echo; rm -f "$$out"; exit 1; fi; \
	python3 -c 'import json,sys; [print(u) for u in json.load(open(sys.argv[1]))["feeds"]]' "$$out"; \
	rm -f "$$out"
