.PHONY: test compile terraform-fmt terraform-fmt-check

test:
	python3 -m pytest

compile:
	python3 -m compileall packages services tests

terraform-fmt:
	terraform fmt -recursive infra/terraform

terraform-fmt-check:
	terraform fmt -check -recursive infra/terraform

