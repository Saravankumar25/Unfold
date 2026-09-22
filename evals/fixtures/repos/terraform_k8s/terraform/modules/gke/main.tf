resource "google_container_cluster" "primary" {
  name                     = "${var.name}-gke"
  location                 = var.region
  network                  = var.network_id
  subnetwork               = var.subnetwork
  remove_default_node_pool = true
  initial_node_count       = 1

  private_cluster_config {
    enable_private_nodes    = true
    enable_private_endpoint = false
    master_ipv4_cidr_block  = "172.16.0.0/28"
  }

  workload_identity_config {
    workload_pool = "${data.google_client_config.current.project}.svc.id.goog"
  }
}

resource "google_container_node_pool" "default" {
  name       = "default"
  cluster    = google_container_cluster.primary.name
  location   = var.region
  node_count = var.node_count

  node_config {
    machine_type = var.machine_type
    oauth_scopes = ["https://www.googleapis.com/auth/cloud-platform"]
  }
}

data "google_client_config" "current" {}

output "cluster_name" {
  value = google_container_cluster.primary.name
}
