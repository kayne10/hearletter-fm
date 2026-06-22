# AWS Bootstrap Checklist

This is the practical path from "I have not signed into AWS in a while" to "Terraform can deploy Hearletter FM."

## 1. Re-Enter The Account

Checklist:

- Sign in to the AWS account.
- Confirm root MFA is enabled.
- Confirm billing access works.
- Create a low budget alert for the dev account.
- Note the AWS account ID.
- Choose a deployment region that supports SES inbound email receiving.

Local check:

```bash
aws --version
```

## 2. Prefer IAM Identity Center For Human Access

If the account is old, you may still have IAM users. That is workable for recovery, but the cleaner long-term path is:

- Enable IAM Identity Center if it is not already enabled.
- Create or confirm your human user.
- Assign yourself administrative access for bootstrap work.
- Configure AWS CLI SSO login.

Example local profile:

```text
[profile hearletter-admin]
sso_start_url = https://your-start-url.awsapps.com/start
sso_region = us-east-1
sso_account_id = 123456789012
sso_role_name = AdministratorAccess
region = us-east-1
output = json
```

Check it:

```bash
aws sso login --profile hearletter-admin
aws sts get-caller-identity --profile hearletter-admin
```

## 3. Create The Terraform Deploy Role

Role name:

```text
HearletterTerraformDeployRole
```

Trust policy shape:

- Trust your human admin principal or IAM Identity Center-generated role.
- Later, add GitHub Actions OIDC if CI/CD becomes useful.

Permissions for the first MVP:

- Use a broad policy for bootstrap, such as administrator-level access in a dev account.
- Tighten this after Milestone 7 when the Terraform resource set is stable.

Why this order:

- A narrow policy is better long term.
- A too-narrow policy on day one burns time while the resource list is still changing.

## 4. Configure A Local Terraform Profile

One simple option is an AWS CLI profile that uses SSO directly:

```text
[profile hearletter-dev]
sso_start_url = https://your-start-url.awsapps.com/start
sso_region = us-east-1
sso_account_id = 123456789012
sso_role_name = AdministratorAccess
region = us-east-1
output = json
```

Another option is an assume-role profile:

```text
[profile hearletter-admin]
sso_start_url = https://your-start-url.awsapps.com/start
sso_region = us-east-1
sso_account_id = 123456789012
sso_role_name = AdministratorAccess
region = us-east-1
output = json

[profile hearletter-dev]
role_arn = arn:aws:iam::123456789012:role/HearletterTerraformDeployRole
source_profile = hearletter-admin
region = us-east-1
output = json
```

Check it:

```bash
aws sts get-caller-identity --profile hearletter-dev
```

Terraform can then use:

```bash
export AWS_PROFILE=hearletter-dev
cd infra/terraform
terraform init
terraform plan \
  -var="domain_name=example.com" \
  -var="inbound_recipient=listen@example.com"
```

## 5. Keep These Values Local

Do not commit these values:

- AWS account ID if you prefer to keep it private.
- Role ARN.
- SSO start URL.
- Domain details before you are ready.
- API keys.

Reasonable local-only places:

- `~/.aws/config`
- `~/.aws/credentials`
- `.env`
- shell profile exports

The repo already ignores `.env`.

## 6. First Terraform Success Criteria

Before moving to real email, aim for:

```bash
terraform fmt -check -recursive .
terraform init
terraform validate
terraform plan \
  -var="domain_name=example.com" \
  -var="inbound_recipient=listen@example.com"
```

Done means:

- Auth works.
- Providers install.
- Terraform finds Lambda artifacts or tells us exactly which packaging step is missing.
- The plan fails only on expected missing inputs, not account access.

