output "vpc_id" {
  value = aws_vpc.this.id
}

output "public_subnet_ids" {
  value = aws_subnet.public[*].id
}

output "private_subnet_ids" {
  value = aws_subnet.private[*].id
}

output "agentcore_egress_security_group_id" {
  value = aws_security_group.agentcore_egress.id
}

output "vpc_link_id" {
  value = aws_apigatewayv2_vpc_link.this.id
}
