# Terraform deployment (via GitHub Actions)

Infrastructure is deployed automatically by [`.github/workflows/deploy.yml`](../.github/workflows/deploy.yml) using **GitHub OIDC** — no long-lived AWS access keys are stored in GitHub.

## Overview

| Stage                     | What                                                                     | How                                                          | State                                |
| ------------------------- | ------------------------------------------------------------------------ | ------------------------------------------------------------ | ------------------------------------ |
| **Bootstrap** (once)      | State bucket, DynamoDB lock table, GitHub OIDC provider, deploy IAM role | Run locally — see [bootstrap/README.md](bootstrap/README.md) | Local file in `terraform/bootstrap/` |
| **Application** (ongoing) | SES, S3, Lambda, IAM for supplier email                                  | GitHub Actions on push to `main`                             | S3 remote state                      |

## Bootstrap (one-time)

Bootstrap must be applied **before** GitHub Actions can deploy. It runs locally in the target AWS account and creates remote state storage plus the GitHub OIDC deploy role.

**Full instructions, IAM permissions, AWS credential setup, and troubleshooting:** [bootstrap/README.md](bootstrap/README.md)

Quick start:

```bash
export AWS_PROFILE=your-member-account-profile
cp terraform/bootstrap/terraform.tfvars.example terraform/bootstrap/terraform.tfvars
# edit terraform/bootstrap/terraform.tfvars

terraform -chdir=terraform/bootstrap init
terraform -chdir=terraform/bootstrap apply
terraform -chdir=terraform/bootstrap output
```

## Configure GitHub repository settings

In the repository: **Settings → Secrets and variables → Actions**.

**Variables:**

| Name                               | Value (from bootstrap output)                             |
| ---------------------------------- | --------------------------------------------------------- |
| `AWS_REGION`                       | `eu-west-1`                                               |
| `AWS_ROLE_ARN`                     | `github_actions_role_arn` output                          |
| `TF_STATE_BUCKET`                  | `terraform_state_bucket` output                           |
| `TF_LOCK_TABLE`                    | `terraform_lock_table` output                             |
| `TF_VAR_DOMAIN_NAME`               | e.g. `whitkirk.com`                                       |
| `TF_VAR_INBOUND_EMAIL_ADDRESS`     | e.g. `supplier-updates@whitkirk.com`                      |
| `TF_VAR_NOTIFICATION_FROM_ADDRESS` | e.g. `supplier-updates@whitkirk.com`                      |
| `TF_VAR_PROJECT_NAME`              | Must match bootstrap `project_name` (default `bartender`) |

**Secrets:**

| Name                  | Notes              |
| --------------------- | ------------------ |
| `TF_VAR_LOYVERSE_PAT` | Loyverse API token |

No `AWS_ACCESS_KEY_ID` or `AWS_SECRET_ACCESS_KEY` secrets are required.

## Deploy the application

Push to `main` (with changes under `terraform/`, Lambda source, or `supplier_data.yaml`), or run the **Deploy** workflow manually from the Actions tab.

The workflow:

1. Builds `dist/lambda.zip`
2. Assumes the bootstrap deploy role via OIDC
3. Runs `terraform plan` (PRs) or `plan` + `apply` (pushes to `main`)

## Local application Terraform (optional)

For debugging the application stack locally, use credentials for the deploy role or an administrator in the target account:

```bash
cp terraform/backend.hcl.example terraform/backend.hcl
cp terraform/terraform.tfvars.example terraform/terraform.tfvars

./scripts/build_lambda.sh
terraform -chdir=terraform init -backend-config=backend.hcl
terraform -chdir=terraform apply
```

`terraform.tfvars`, `backend.hcl`, and `terraform/bootstrap/terraform.tfvars` are gitignored.

## DNS after apply

1. Add the SES domain verification TXT record from `terraform -chdir=terraform output ses_domain_verification_token`.
2. Add the inbound MX record: `10 inbound-smtp.eu-west-1.amazonaws.com`.
3. Verify the notification sender address in SES.
4. Request SES production access to reply to arbitrary forwarders.

## Updating bootstrap

See [bootstrap/README.md](bootstrap/README.md#state-file).
