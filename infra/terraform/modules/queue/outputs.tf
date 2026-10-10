output "queue_url" {
  value = aws_sqs_queue.jobs.id
}

output "queue_arn" {
  value = aws_sqs_queue.jobs.arn
}

output "queue_name" {
  value = aws_sqs_queue.jobs.name
}

output "dlq_arn" {
  value = aws_sqs_queue.dlq.arn
}

output "dlq_name" {
  value = aws_sqs_queue.dlq.name
}
