variable "aws_region" {
  description = "AWS region for SES, Lambda, and S3 resources. SES inbound mail requires eu-west-1, us-east-1, or us-west-2."
  type        = string
  default     = "eu-west-1"
}

variable "project_name" {
  description = "Prefix used for resource names. Must match project_name in bootstrap/terraform.tfvars."
  type        = string
  default     = "bartender"
}

variable "domain_name" {
  description = "Domain used for SES inbound and outbound email."
  type        = string
}

variable "inbound_email_address" {
  description = "Email address that receives forwarded supplier confirmations."
  type        = string

  validation {
    condition     = endswith(var.inbound_email_address, "@${var.domain_name}")
    error_message = "inbound_email_address must be an address on domain_name."
  }
}

variable "notification_from_address" {
  description = "Sender address for Lambda reply emails. Must be on domain_name; sending is allowed once the SES domain identity is verified."
  type        = string

  validation {
    condition     = endswith(var.notification_from_address, "@${var.domain_name}")
    error_message = "notification_from_address must be an address on domain_name."
  }
}

variable "loyverse_pat" {
  description = "Loyverse personal access token for API updates."
  type        = string
  sensitive   = true
}

variable "airtable_pat" {
  description = "Airtable personal access token for supplier mapping reads and writes."
  type        = string
  sensitive   = true
}

variable "airtable_base_id" {
  description = "Airtable base ID for the Products supplier mapping table."
  type        = string
}

variable "lambda_python_runtime" {
  description = "Python runtime for the supplier email Lambda."
  type        = string
  default     = "python3.13"
}
