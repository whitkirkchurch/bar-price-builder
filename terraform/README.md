# Terraform deployment (via GitHub Actions)

Infrastructure is deployed automatically by [`.github/workflows/deploy.yml`](../.github/workflows/deploy.yml) using **GitHub OIDC** — no long-lived AWS access keys are stored in GitHub.

## Overview

| Stage                     | What                                                | How                                                          | State                                |
| ------------------------- | --------------------------------------------------- | ------------------------------------------------------------ | ------------------------------------ |
| **Bootstrap** (once)      | State bucket, GitHub OIDC provider, deploy IAM role | Run locally — see [bootstrap/README.md](bootstrap/README.md) | Local file in `terraform/bootstrap/` |
| **Application** (ongoing) | SES, S3, Lambda, IAM for supplier email             | GitHub Actions on push to `main`                             | S3 remote state                      |

Inbound supplier emails are stored in S3 for Lambda processing and expire after 30 days.

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

| Name         | Value (from bootstrap output) |
| ------------ | ----------------------------- |
| `AWS_REGION` | `eu-west-1`                   |

**Secrets:**

Infrastructure identifiers live in secrets (not variables) so they stay private if the repository is public.

| Name                                        | Notes                                                                      |
| ------------------------------------------- | -------------------------------------------------------------------------- |
| `AWS_ROLE_ARN`                              | `github_actions_role_arn` bootstrap output                                 |
| `TF_STATE_BUCKET`                           | `terraform_state_bucket` bootstrap output                                  |
| `TF_VAR_DOMAIN_NAME`                        | e.g. `bartender.example.org.uk`                                            |
| `TF_VAR_INBOUND_EMAIL_ADDRESS`              | e.g. `supplier-updates@bartender.example.org.uk`                           |
| `TF_VAR_NOTIFICATION_FROM_ADDRESS`          | e.g. `supplier-updates@bartender.example.org.uk`                           |
| `TF_VAR_SES_FEEDBACK_EMAIL`                 | Bounce/complaint alerts destination                                        |
| `TF_VAR_APPROVED_SENDER_DOMAINS`            | Optional; default `whitkirkchurch.org.uk` (comma-separated)                |
| `TF_VAR_AIRTABLE_BASE_ID`                   | Airtable Products base ID (e.g. `appXXXXXXXXXXXXXX`)                       |
| `TF_VAR_AIRTABLE_SUPPLIER_MAPPING_TABLE_ID` | Supplier mapping table ID (e.g. `tblXXXXXXXXXXXXXX`)                       |
| `TF_VAR_LOYVERSE_PAT`                       | Loyverse API token                                                         |
| `TF_VAR_AIRTABLE_PAT`                       | Airtable personal access token with read/write access to the Products base |

No `AWS_ACCESS_KEY_ID` or `AWS_SECRET_ACCESS_KEY` secrets are required.

## Deploy the application

Push to `main` (with changes under `terraform/`, Lambda source, or supplier mapping code), or run the **Deploy** workflow manually from the Actions tab.

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
3. Request SES production access to reply to arbitrary forwarders.

Once the domain is verified, SES allows sending from any address on that domain (including `TF_VAR_NOTIFICATION_FROM_ADDRESS`). No separate per-address verification is required.

## SES bounce and complaint alerts

Terraform creates an SNS topic (`bartender-ses-feedback`) and emails bounce/complaint events for the SES domain identity to `TF_VAR_SES_FEEDBACK_EMAIL`. Email feedback forwarding on the domain identity is disabled so those events are not also sent to the outbound From/Return-Path address.

After the first apply, confirm the SNS subscription from the inbox of that address (AWS sends a one-time confirmation email). Until confirmed, bounce/complaint alerts will not be delivered.

## Updating bootstrap

See [bootstrap/README.md](bootstrap/README.md#state-file).
