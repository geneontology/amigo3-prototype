variable "region" {
  type    = string
  default = "us-east-1"
}

variable "name" {
  description = "Unique deployment name within this AWS account and region."
  type        = string
  default     = "amigo3-preview"
  validation {
    condition     = can(regex("^[a-z][a-z0-9-]{2,30}$", var.name))
    error_message = "Use 3–31 lowercase letters, digits or hyphens, starting with a letter."
  }
}

variable "hostname" {
  description = "DNS name you control. Point its A record to the output public_ip."
  type        = string
  validation {
    condition     = length(var.hostname) < 254 && can(regex("^[a-z0-9]([a-z0-9-]*[a-z0-9])?(\\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)+$", var.hostname))
    error_message = "Supply a lowercase DNS hostname, without scheme, path, port or trailing dot."
  }
}

variable "github_user_ids" {
  description = "Approved numeric GitHub account IDs (not handles). Empty means deny everyone."
  type        = set(number)
  validation {
    condition     = alltrue([for id in var.github_user_ids : id > 0 && floor(id) == id])
    error_message = "GitHub IDs must be positive integers."
  }
}

variable "site_access" {
  description = "private requires login everywhere; public allows anonymous browsing. Chat is disabled in both."
  type        = string
  default     = "private"
  validation {
    condition     = contains(["private", "public"], var.site_access)
    error_message = "Choose private or public."
  }
}

variable "instance_type" {
  description = "x86_64 instance; images are built for linux/amd64. Start with 8 GiB RAM."
  type        = string
  default     = "t3.large"
}

variable "disk_gb" {
  type    = number
  default = 100
  validation {
    condition     = var.disk_gb >= 40 && floor(var.disk_gb) == var.disk_gb
    error_message = "Provision at least 40 GiB for OS, images and staged releases."
  }
}

variable "budget_email" {
  description = "Optional monthly budget notifications. Alerts do not cap spending."
  type        = string
  default     = ""
}

variable "monthly_budget_usd" {
  type    = number
  default = 100
  validation {
    condition     = var.monthly_budget_usd > 0
    error_message = "Budget must be positive."
  }
}
