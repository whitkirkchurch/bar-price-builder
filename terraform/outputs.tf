output "inbound_email_address" {
  description = "Address to forward supplier confirmation emails to."
  value       = var.inbound_email_address
}

output "inbound_email_bucket" {
  description = "S3 bucket storing inbound raw emails."
  value       = aws_s3_bucket.inbound_email.bucket
}

output "lambda_function_name" {
  description = "Name of the supplier email Lambda function."
  value       = aws_lambda_function.supplier_email.function_name
}

output "lambda_function_arn" {
  description = "ARN of the supplier email Lambda function."
  value       = aws_lambda_function.supplier_email.arn
}

output "ses_domain_verification_token" {
  description = "TXT record value for SES domain verification."
  value       = aws_ses_domain_identity.main.verification_token
}
