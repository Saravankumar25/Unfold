# Remote state in the bootstrap bucket, locked with the bootstrap DynamoDB table. The bucket, region and lock table
# are not hard-coded: pass them with `terraform init -backend-config=backend.hcl` (see backend.hcl.example).
# Only the state key is fixed here so dev and prod can never share a state file.
terraform {
  backend "s3" {
    key = "envs/prod/terraform.tfstate"
  }
}
