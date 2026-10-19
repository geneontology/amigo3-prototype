mock_provider "aws" {
  mock_data "aws_caller_identity" {
    defaults = { account_id = "123456789012" }
  }
  mock_data "aws_partition" {
    defaults = { partition = "aws" }
  }
  mock_data "aws_availability_zones" {
    defaults = { names = ["us-east-1a"] }
  }
  mock_data "aws_ec2_instance_type" {
    defaults = { supported_architectures = ["x86_64"] }
  }
}

variables {
  hostname        = "amigo.example.org"
  github_user_ids = [1234]
  budget_email    = "owner@example.org"
}

run "private_standalone_deployment" {
  command = plan
  assert {
    condition     = aws_instance.this.metadata_options[0].http_tokens == "required" && aws_instance.this.metadata_options[0].http_put_response_hop_limit == 1
    error_message = "Require IMDSv2 and keep instance credentials out of ordinary container networking."
  }
  assert {
    condition     = aws_instance.this.root_block_device[0].encrypted && aws_instance.this.root_block_device[0].volume_size == 100
    error_message = "Use an encrypted 100 GiB serving volume."
  }
  assert {
    condition     = length(aws_vpc_security_group_ingress_rule.web) == 2 && aws_vpc_security_group_ingress_rule.web["443"].from_port == 443 && aws_vpc_security_group_ingress_rule.web["80"].from_port == 80
    error_message = "Expose only HTTPS and HTTP for redirects/certificate validation."
  }
  assert {
    condition     = aws_s3_bucket_public_access_block.releases.block_public_policy && !aws_s3_bucket.releases.force_destroy && !aws_ecr_repository.app.force_delete
    error_message = "Keep releases private and refuse silent artifact destruction."
  }
  assert {
    condition     = var.site_access == "private" && length(aws_budgets_budget.account) == 1
    error_message = "Start private and configure the requested budget alerts."
  }
}
