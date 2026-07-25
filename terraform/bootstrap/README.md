# Bootstrap

One-time setup that prepares an AWS account for automated deployment via GitHub Actions. Run this **locally** before the application Terraform in [`../`](../) can be applied from CI.

Bootstrap creates:

| Resource          | Purpose                                                     |
| ----------------- | ----------------------------------------------------------- |
| S3 bucket         | Terraform remote state (and native `.tflock` state locking) |
| IAM OIDC provider | Lets GitHub Actions authenticate without access keys        |
| IAM deploy role   | Assumed by GitHub Actions to run `terraform plan` / `apply` |

Bootstrap uses **local Terraform state** (a `terraform.tfstate` file in this directory). It cannot store its own state in the S3 bucket it creates.

See also: [Application deployment](../README.md) (GitHub Actions, DNS, ongoing deploys).

## Prerequisites

- **Target AWS account** — the member account where SES, Lambda, and S3 will live. Verify with:

  ```bash
  export AWS_PROFILE=your-profile   # see “AWS credentials” below
  aws sts get-caller-identity
  ```

  The `Account` value must be the account you intend to deploy into.

- **Terraform** >= 1.15 ([install guide](https://developer.hashicorp.com/terraform/install))
- **AWS CLI** configured for that account
- **IAM permissions** for the principal running bootstrap — see [Required IAM permissions](#required-iam-permissions)

Use `eu-west-1` as the region (`aws_region` in `terraform.tfvars`). SES inbound mail is not available in `eu-west-2`.

## AWS credentials

Bootstrap must run with credentials for the **target member account**, not the Organizations management account (unless you are deploying there).

Common setups:

- **IAM Identity Center (SSO)** — recommended for Organizations member accounts. Configure with `aws configure sso`, select the member account, then `export AWS_PROFILE=...`.
- **IAM user access keys** in the member account — store in a named profile; no `role_arn` required.
- **Cross-account role** — only works if `source_profile` uses an **IAM user** (not root). Root cannot call `sts:AssumeRole`.

Do not use root access keys for routine CLI work.

## Required IAM permissions

The IAM principal applying bootstrap needs permission to **create and manage** the resources in [`main.tf`](main.tf). This is a one-time administrative action.

### Option A: Managed policy (simplest)

Attach one of the following to your user or role in the **target account**:

- `AdministratorAccess` — simplest for a dedicated workload account
- Or combine `PowerUserAccess` with IAM permissions to create OIDC providers and roles (IAM is not included in PowerUser)

### Option B: Scoped inline policy (minimum)

If your organisation requires least privilege, the applying principal needs at least these actions:

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "BootstrapS3StateBucket",
      "Effect": "Allow",
      "Action": [
        "s3:CreateBucket",
        "s3:DeleteBucket",
        "s3:GetBucketLocation",
        "s3:GetBucketVersioning",
        "s3:PutBucketVersioning",
        "s3:GetBucketPublicAccessBlock",
        "s3:PutBucketPublicAccessBlock",
        "s3:GetEncryptionConfiguration",
        "s3:PutEncryptionConfiguration",
        "s3:ListBucket",
        "s3:PutBucketTagging",
        "s3:GetBucketTagging"
      ],
      "Resource": "arn:aws:s3:::YOUR_STATE_BUCKET_NAME"
    },
    {
      "Sid": "BootstrapIAM",
      "Effect": "Allow",
      "Action": [
        "iam:CreateOpenIDConnectProvider",
        "iam:DeleteOpenIDConnectProvider",
        "iam:GetOpenIDConnectProvider",
        "iam:ListOpenIDConnectProviders",
        "iam:TagOpenIDConnectProvider",
        "iam:CreateRole",
        "iam:DeleteRole",
        "iam:GetRole",
        "iam:UpdateRole",
        "iam:TagRole",
        "iam:PutRolePolicy",
        "iam:DeleteRolePolicy",
        "iam:GetRolePolicy",
        "iam:ListRolePolicies"
      ],
      "Resource": "*"
    },
    {
      "Sid": "BootstrapReadAccount",
      "Effect": "Allow",
      "Action": "sts:GetCallerIdentity",
      "Resource": "*"
    }
  ]
}
```

Replace `YOUR_STATE_BUCKET_NAME` with the value you set in `terraform.tfvars`.

Terraform may require additional `s3:Get*` / `iam:Get*` actions during planning; the deploy role uses `s3:Get*` and `s3:List*` on project buckets for this. If `terraform plan` still reports `AccessDenied`, add the missing action or use Option A for bootstrap only.

### What the bootstrap deploy role receives

Bootstrap also creates `bartender-github-actions-deploy` with a **separate** policy (in `main.tf`) scoped to:

- Read/write Terraform state in the state bucket (including S3 native `.tflock` files)
- Manage application resources prefixed with `bartender-` (S3, Lambda, IAM, CloudWatch Logs)
- Full SES access (for inbound rules and sending replies)
- SNS create/update/delete on `${project_name}-*` topics (SES bounce/complaint alerts)

GitHub Actions uses this role via OIDC — it does **not** need the bootstrap permissions above.

If application `terraform apply` fails with `AccessDenied` on read APIs such as `lambda:GetFunctionCodeSigningConfig`, `iam:ListInstanceProfilesForRole`, or `logs:DescribeLogGroups`, re-run bootstrap apply locally to refresh the deploy role policy in `main.tf`.

## Steps

### 1. Configure variables

```bash
cp terraform.tfvars.example terraform.tfvars
```

Edit `terraform.tfvars`:

| Variable                      | Description                                                           |
| ----------------------------- | --------------------------------------------------------------------- |
| `terraform_state_bucket_name` | Globally unique S3 bucket name for remote state                       |
| `github_org` / `github_repo`  | Must match the GitHub repository (`whitkirkchurch/bar-price-builder`) |
| `aws_region`                  | `eu-west-1`                                                           |
| `project_name`                | Resource name prefix (default `bartender`)                            |

`terraform.tfvars` is gitignored.

### 2. Initialise and apply

```bash
export AWS_PROFILE=your-profile

terraform init
terraform plan
terraform apply
```

### 3. Record outputs

```bash
terraform output
```

| Output                    | Use                             |
| ------------------------- | ------------------------------- |
| `github_actions_role_arn` | GitHub secret `AWS_ROLE_ARN`    |
| `terraform_state_bucket`  | GitHub secret `TF_STATE_BUCKET` |
| `aws_region`              | GitHub variable `AWS_REGION`    |

Configure the remaining GitHub secrets (and the `AWS_REGION` variable) as described in [../README.md#configure-github-repository-settings](../README.md#configure-github-repository-settings).

### 4. Deploy the application

After bootstrap, application deploys run from GitHub Actions (or locally with the deploy role). See [../README.md](../README.md).

## If the GitHub OIDC provider already exists

If `terraform apply` fails because `token.actions.githubusercontent.com` is already registered in this account:

```bash
terraform import \
  aws_iam_openid_connect_provider.github \
  arn:aws:iam::ACCOUNT_ID:oidc-provider/token.actions.githubusercontent.com
```

Replace `ACCOUNT_ID` with the target account ID from `aws sts get-caller-identity`, then run `terraform apply` again.

## State file

Bootstrap state is stored locally at `terraform.tfstate` in this directory (gitignored). Back it up if you need to modify bootstrap resources later.

To change OIDC trust (e.g. add a branch) or the state bucket configuration, edit `main.tf` / `variables.tf` and run `terraform apply` again with bootstrap IAM permissions.

## Troubleshooting

| Problem                                                 | Likely cause                                                                                  |
| ------------------------------------------------------- | --------------------------------------------------------------------------------------------- |
| `AccessDenied` on `terraform apply`                     | Bootstrap IAM permissions missing — see [Required IAM permissions](#required-iam-permissions) |
| `Roles may not be assumed by root accounts`             | `source_profile` uses root credentials — use SSO or an IAM user                               |
| `AccessDenied` assuming `OrganizationAccountAccessRole` | Role missing in member account, or trust policy does not allow your principal                 |
| S3 bucket name already taken                            | Choose a different `terraform_state_bucket_name`                                              |
| OIDC provider already exists                            | Import the provider — see above                                                               |
| Wrong account in `get-caller-identity`                  | `AWS_PROFILE` points at the management or another member account                              |
