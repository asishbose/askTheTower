output "key_arn" {
  value = aws_kms_key.main.arn
}

output "key_id" {
  value = aws_kms_key.main.key_id
}

output "hmac_key_arn" {
  value = aws_kms_key.hmac.arn
}

output "aliases" {
  value = {
    main = aws_kms_alias.main.name
    hmac = aws_kms_alias.hmac.name
  }
}
