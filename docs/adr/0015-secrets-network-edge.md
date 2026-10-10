# ADR 0015: SSM Parameter Store secrets, NAT-free public-subnet Fargate, no CloudFront/WAF

Status: proposed (2026-10-10) · Phase 4 stage B

## Context
Secrets: 4 Gemini keys, 4 Groq keys, GitHub token, proxy HMAC secret; none may be in Terraform state or the repo.
Network: tasks must reach GitHub (no AAAA records for `github.com`, `api.github.com`, `codeload.github.com`,
checked 2026-10-10), Gemini, ECR, S3, DynamoDB, SQS, CloudWatch. Prices (Price List 2026-10-10): NAT gateway
$0.045/h (us-east-1) and $0.056/h (Mumbai) plus per GB; interface endpoint $0.01/AZ-hour ($0.013 Mumbai); gateway
endpoints free; public IPv4 $0.005/h; WAF ACL $5 + $1/rule + $0.60/M; Secrets Manager $0.40/secret/month; SSM
standard parameters free.

## Decision
- SSM Parameter Store SecureString (standard tier, AWS-managed key). Fargate via task-definition `secrets`; Lambda
  via a cold-start `GetParameters` loader. Terraform creates **no** parameters (a refresh reads a SecureString back
  decrypted, so the real value would enter the state and every plan job could read it); it derives names and ARNs
  only, values are put out of band with `aws ssm put-parameter`.
- No NAT, no interface endpoints. One VPC with two public subnets, no inbound, egress 443, S3 and DynamoDB gateway
  endpoints; tasks get a public IPv4 only while running ($0.00067 per 8-minute job). Lambda is outside any VPC.
- No CloudFront or WAF: edge rate rules would key on Vercel egress addresses. Revisit if browsers call the API
  origin directly.

## Alternatives considered
Secrets Manager (no rotation need), customer-managed KMS key ($1/month), IPv6-only Fargate (breaks GitHub), NAT
($32.85-40.88/month), five interface endpoints in two AZs ($73-95/month), WAF ($8/month floor).

## Consequences
Open egress from tasks is a residual risk (mitigations: no inbound, repository code never executed, minimal task
role). Terraform policy tests assert absence of NAT, load balancers, WAF and always-on ECS services.
