.PHONY: test compile local-pipeline package-lambdas package-lambdas-no-deps terraform-fmt terraform-fmt-check

test:
	python3 -m pytest

compile:
	python3 -m compileall packages services tests

local-pipeline:
	python3 scripts/run_local_pipeline.py --input data --output-dir artifacts/local/latest --clean-output

package-lambdas:
	python3 scripts/package_lambdas.py

package-lambdas-no-deps:
	python3 scripts/package_lambdas.py --skip-deps

terraform-fmt:
	terraform fmt -recursive infra/terraform

terraform-fmt-check:
	terraform fmt -check -recursive infra/terraform
