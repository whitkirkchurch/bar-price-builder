variable "aws_region" {
  description = "AWS region for bootstrap resources and application deployment."
  type        = string
  default     = "eu-west-1"
}

variable "project_name" {
  description = "Prefix used for project resources and IAM role names."
  type        = string
  default     = "bartender"
}

variable "terraform_state_bucket_name" {
  description = "Globally unique S3 bucket name for Terraform remote state."
  type        = string
}

variable "github_org" {
  description = "GitHub organisation or user that owns the repository."
  type        = string
  default     = "whitkirkchurch"
}

variable "github_repo" {
  description = "GitHub repository name."
  type        = string
  default     = "bar-price-builder"
}

variable "github_allowed_branches" {
  description = "Branches allowed to assume the deploy role via OIDC."
  type        = list(string)
  default     = ["main"]
}

variable "allow_pull_request_plans" {
  description = "Allow pull request workflows to assume the deploy role for terraform plan."
  type        = bool
  default     = true
}
