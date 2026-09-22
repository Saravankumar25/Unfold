# shortlink-platform

Infrastructure and deployment for **shortlink**, a tiny URL-shortener written in Go.

- `terraform/` provisions a VPC, a private GKE cluster and a Cloud SQL (PostgreSQL) instance on Google Cloud.
- `k8s/` contains the Kubernetes manifests (Deployment, Service, Ingress, HPA, ConfigMap) for the app.
- `app/` is the Go HTTP service; it stores links in PostgreSQL.
- `.github/workflows/deploy.yml` builds the image, pushes it to Artifact Registry and applies the manifests.

## Provisioning

```bash
cd terraform
terraform init
terraform apply -var project_id=my-project
```

## Deploying

Pushing to `main` runs the deploy workflow. Manual deploys use `kubectl apply -f k8s/`.
