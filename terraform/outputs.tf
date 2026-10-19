output "public_ip" { value = aws_eip.this.public_ip }
output "url" { value = "https://${var.hostname}" }
output "instance_id" { value = aws_instance.this.id }
output "release_bucket" { value = aws_s3_bucket.releases.id }
output "image_repository" { value = aws_ecr_repository.app.repository_url }
output "github_secret_arn" { value = aws_secretsmanager_secret.github.arn }
output "github_callback" { value = "https://${var.hostname}/auth/github/callback" }
output "admin_command" { value = "aws --region ${var.region} ssm start-session --target ${aws_instance.this.id}" }
