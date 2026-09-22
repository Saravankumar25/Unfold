provider "google" {
  project = var.project_id
  region  = var.region
}

module "network" {
  source      = "./modules/network"
  name        = "shortlink"
  region      = var.region
  subnet_cidr = var.subnet_cidr
}

module "gke" {
  source       = "./modules/gke"
  name         = "shortlink"
  region       = var.region
  network_id   = module.network.network_id
  subnetwork   = module.network.subnet_id
  node_count   = var.node_count
  machine_type = var.machine_type
}

module "database" {
  source     = "./modules/database"
  name       = "shortlink"
  region     = var.region
  network_id = module.network.network_id
  tier       = var.db_tier
}

resource "google_artifact_registry_repository" "images" {
  location      = var.region
  repository_id = "shortlink"
  format        = "DOCKER"
}
