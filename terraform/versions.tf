terraform {
  required_version = ">= 1.9, < 2.0"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.100.0"
    }
  }
  # Local state initially. Keep it private and backed up; do not commit it.
  # A shared deployment can configure an independently bootstrapped S3 backend.
}

provider "aws" {
  region = var.region
  default_tags {
    tags = { Project = "amigo3", Deployment = var.name, ManagedBy = "terraform" }
  }
}
