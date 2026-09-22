resource "google_sql_database_instance" "main" {
  name             = "${var.name}-pg"
  region           = var.region
  database_version = "POSTGRES_16"

  settings {
    tier = var.tier
    ip_configuration {
      ipv4_enabled    = false
      private_network = var.network_id
    }
    backup_configuration {
      enabled = true
    }
  }

  deletion_protection = true
}

resource "google_sql_database" "app" {
  name     = "shortlink"
  instance = google_sql_database_instance.main.name
}

resource "google_sql_user" "app" {
  name     = "shortlink"
  instance = google_sql_database_instance.main.name
  password = var.db_password
}

output "connection_name" {
  value = google_sql_database_instance.main.connection_name
}
