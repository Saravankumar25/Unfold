# Public subnets only: no NAT gateway, no load balancer, no interface endpoints (decision 4.7). Fargate tasks get a
# public IP, have no inbound rule, and may reach 443 only. S3 and DynamoDB use free gateway endpoints.

locals {
  zones = length(var.availability_zones) > 0 ? var.availability_zones : ["${var.region}a", "${var.region}b"]
}

resource "aws_vpc" "this" {
  cidr_block           = var.vpc_cidr
  enable_dns_support   = true
  enable_dns_hostnames = true

  tags = { Name = var.name }
}

resource "aws_internet_gateway" "this" {
  vpc_id = aws_vpc.this.id

  tags = { Name = var.name }
}

resource "aws_subnet" "public" {
  count = length(local.zones)

  vpc_id                  = aws_vpc.this.id
  availability_zone       = local.zones[count.index]
  cidr_block              = cidrsubnet(var.vpc_cidr, 8, count.index)
  map_public_ip_on_launch = true

  tags = { Name = "${var.name}-public-${count.index}" }
}

resource "aws_route_table" "public" {
  vpc_id = aws_vpc.this.id

  tags = { Name = "${var.name}-public" }
}

resource "aws_route" "internet" {
  route_table_id         = aws_route_table.public.id
  destination_cidr_block = "0.0.0.0/0"
  gateway_id             = aws_internet_gateway.this.id
}

resource "aws_route_table_association" "public" {
  count = length(aws_subnet.public)

  subnet_id      = aws_subnet.public[count.index].id
  route_table_id = aws_route_table.public.id
}

# Tasks reach GitHub, the LLM providers and AWS APIs over 443. No ingress rule exists. DNS is answered by the VPC
# resolver, which security groups do not filter.
resource "aws_security_group" "worker" {
  name_prefix = "${var.name}-worker-"
  description = "Fargate analysis tasks: no inbound, egress 443 only"
  vpc_id      = aws_vpc.this.id

  tags = { Name = "${var.name}-worker" }

  lifecycle {
    create_before_destroy = true
  }
}

resource "aws_vpc_security_group_egress_rule" "https" {
  security_group_id = aws_security_group.worker.id
  description       = "HTTPS to anywhere"
  ip_protocol       = "tcp"
  from_port         = 443
  to_port           = 443
  cidr_ipv4         = "0.0.0.0/0"
}

# The default security group is emptied so nothing can attach to it by accident.
resource "aws_default_security_group" "default" {
  vpc_id = aws_vpc.this.id

  tags = { Name = "${var.name}-default-closed" }
}

resource "aws_vpc_endpoint" "s3" {
  vpc_id            = aws_vpc.this.id
  service_name      = "com.amazonaws.${var.region}.s3"
  vpc_endpoint_type = "Gateway"
  route_table_ids   = [aws_route_table.public.id]

  tags = { Name = "${var.name}-s3" }
}

resource "aws_vpc_endpoint" "dynamodb" {
  vpc_id            = aws_vpc.this.id
  service_name      = "com.amazonaws.${var.region}.dynamodb"
  vpc_endpoint_type = "Gateway"
  route_table_ids   = [aws_route_table.public.id]

  tags = { Name = "${var.name}-dynamodb" }
}
