variable "app_name" {
  description = "Application name"
  type        = string
  default     = "training-operator"
}

variable "channel" {
  description = "Charm channel"
  type        = string
  default     = "1.8/stable"
}

variable "config" {
  description = "Map of charm configuration options"
  type        = map(string)
  default     = {}
}

variable "model_name" {
  description = "Model name"
  type        = string
}

variable "resources" {
  description = "Map of resources"
  type        = map(string)
  default     = null
}

variable "revision" {
  description = "Charm revision"
  type        = number
  default     = null
}
