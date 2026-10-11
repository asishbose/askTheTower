variable "tables" {
  description = "Logical table name -> definition (the shape of tower_consent.tables.terraform_definitions())."
  type = map(object({
    name         = string
    billing_mode = string
    hash_key     = string
    range_key    = optional(string)
    attributes   = list(object({ name = string, type = string }))
    global_secondary_indexes = list(object({
      name            = string
      hash_key        = string
      range_key       = optional(string)
      projection_type = string
    }))
    ttl                    = object({ enabled = bool, attribute_name = string })
    point_in_time_recovery = bool
  }))
}

variable "prefix" {
  description = "Physical name prefix; services read the same value as TOWER_TABLE_PREFIX."
  type        = string
}

variable "kms_key_arn" {
  type = string
}
