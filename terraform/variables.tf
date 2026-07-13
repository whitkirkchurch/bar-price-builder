variable "aws_region" {
  description = "AWS region for SES, Lambda, and S3 resources. SES inbound mail requires eu-west-1, us-east-1, or us-west-2."
  type        = string
  default     = "eu-west-1"
}

variable "project_name" {
  description = "Prefix used for resource names."
  type        = string
  default     = "bar-price-builder"
}

variable "domain_name" {
  description = "Domain used for SES inbound and outbound email."
  type        = string
}

variable "inbound_email_address" {
  description = "Email address that receives forwarded supplier confirmations."
  type        = string
}

variable "notification_from_address" {
  description = "Verified SES sender address used for result reply emails."
  type        = string
}

variable "loyverse_pat" {
  description = "Loyverse personal access token for API updates."
  type        = string
  sensitive   = true
}

variable "lambda_python_runtime" {
  description = "Python runtime for the supplier email Lambda."
  type        = string
  default     = "python3.13"
}
