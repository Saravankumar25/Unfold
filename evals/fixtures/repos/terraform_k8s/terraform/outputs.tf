output "cluster_name" {
  value = module.gke.cluster_name
}

output "database_connection_name" {
  value = module.database.connection_name
}

output "registry" {
  value = google_artifact_registry_repository.images.name
}
