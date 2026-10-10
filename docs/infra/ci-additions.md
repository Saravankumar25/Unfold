# CI additions for phase 4 (apply in PART II)

`.github/workflows/ci.yml` is not edited in this change. These are the exact additions, in the order they should be
reviewed. Nothing below holds an AWS key: plan and apply assume the OIDC roles created by `infra/terraform/bootstrap`.

## 1. Terraform job (no AWS access, runs on every pull request)

```yaml
  terraform:
    runs-on: ubuntu-latest
    env:
      UNFOLD_REQUIRE_TERRAFORM: "1"          # a missing binary fails the policy tests instead of skipping them
      TF_PLUGIN_CACHE_DIR: ${{ github.workspace }}/.tf-plugin-cache
    steps:
      - uses: actions/checkout@v4
      - uses: hashicorp/setup-terraform@v3
        with:
          terraform_version: 1.9.8             # pinned like required_version; bump with use_lockfile (see bootstrap)
          terraform_wrapper: false
      - uses: actions/cache@v4
        with:
          path: .tf-plugin-cache
          key: tf-providers-${{ hashFiles('infra/terraform/**/versions.tf') }}
      - run: mkdir -p "$TF_PLUGIN_CACHE_DIR"
      - run: terraform fmt -check -recursive infra/terraform
      - name: init + validate every root and module
        run: |
          set -euo pipefail
          for d in infra/terraform/bootstrap infra/terraform/envs/* infra/terraform/modules/*; do
            (cd "$d" && terraform init -backend=false -input=false >/dev/null && terraform validate)
          done
      - uses: astral-sh/setup-uv@v5
      - name: policy tests (offline plans; positive and negative cases)
        run: uv run --directory backend pytest ../infra/policy
```

The policy tests plan `bootstrap`, `envs/dev` and `envs/prod` offline (dummy credentials, `skip_*` provider flags, a
local backend injected through an override file in a temp copy) and also plan the violating fixtures in
`infra/policy/fixtures`. First run downloads the AWS provider (about 100 MB); the cache step above keeps it.

## 2. Plan on pull requests, apply from a protected environment

```yaml
  plan:
    if: github.event_name == 'pull_request'
    needs: terraform
    runs-on: ubuntu-latest
    permissions: { id-token: write, contents: read }
    strategy:
      matrix: { env: [dev, prod] }
    steps:
      - uses: actions/checkout@v4
      - uses: hashicorp/setup-terraform@v3
        with: { terraform_version: 1.9.8 }
      - uses: aws-actions/configure-aws-credentials@v4
        with:
          role-to-assume: ${{ vars.AWS_PLAN_ROLE_ARN }}        # bootstrap output plan_role_arn
          aws-region: ${{ vars.AWS_REGION }}
      - working-directory: infra/terraform/envs/${{ matrix.env }}
        run: |
          terraform init -backend-config="bucket=${{ vars.TF_STATE_BUCKET }}" \
            -backend-config="region=${{ vars.AWS_REGION }}" \
            -backend-config="dynamodb_table=${{ vars.TF_LOCK_TABLE }}" -backend-config="encrypt=true"
          terraform plan -input=false -lock-timeout=60s
        env:   # variables without a default, from repository variables (nothing secret)
          TF_VAR_account_id: ${{ vars.AWS_ACCOUNT_ID }}
          TF_VAR_web_origin: ${{ vars.WEB_ORIGIN }}
          TF_VAR_alert_emails: ${{ vars.ALERT_EMAILS_JSON }}          # e.g. ["me@example.com"]
          TF_VAR_api_image_uri: ${{ vars.ECR_REGISTRY }}/unfold-${{ matrix.env }}-api:${{ github.sha }}
          TF_VAR_worker_image_uri: ${{ vars.ECR_REGISTRY }}/unfold-${{ matrix.env }}-worker:${{ github.sha }}
          TF_VAR_exporter_image_uri: ${{ vars.ECR_REGISTRY }}/unfold-${{ matrix.env }}-exporter:${{ github.sha }}

  apply:
    if: github.ref == 'refs/heads/main' && github.event_name == 'push'
    needs: [terraform]
    runs-on: ubuntu-latest
    environment: ${{ inputs.env || 'dev' }}        # GitHub Environment with required reviewers (prod)
    permissions: { id-token: write, contents: read }
    steps:
      # same steps as plan, role-to-assume: ${{ vars.AWS_APPLY_ROLE_ARN }}, then `terraform apply -input=false`
      - run: echo "wire after the owner approves the first apply"
```

The trust policies in `bootstrap/main.tf` already restrict the apply role to `repo:<owner>/<repo>:environment:dev|prod`
and the image role to `refs/heads/main`.

## 3. Images (api-lambda, worker, exporter) from one Dockerfile

```yaml
  images:
    if: github.ref == 'refs/heads/main' && github.event_name == 'push'
    runs-on: ubuntu-latest
    permissions: { id-token: write, contents: read }
    strategy:
      matrix:
        include:
          - { target: api-lambda, repository: api }     # the API Lambda image (Lambda Web Adapter)
          - { target: worker, repository: worker }      # Fargate analysis task and the three control Lambdas
          - { target: exporter, repository: exporter }
    steps:
      - uses: actions/checkout@v4
      - uses: aws-actions/configure-aws-credentials@v4
        with: { role-to-assume: "${{ vars.AWS_IMAGES_ROLE_ARN }}", aws-region: "${{ vars.AWS_REGION }}" }
      - uses: aws-actions/amazon-ecr-login@v2
      - run: |
          docker build -f docker/backend.Dockerfile --target ${{ matrix.target }} \
            -t "$REGISTRY/unfold-dev-${{ matrix.target }}:${{ github.sha }}" .
          docker push "$REGISTRY/unfold-dev-${{ matrix.target }}:${{ github.sha }}"
        env: { REGISTRY: "${{ vars.ECR_REGISTRY }}" }
```

Tags are immutable (repositories are `IMMUTABLE`), so the git sha is the tag and `terraform apply -var
api_image_uri=...:<sha>` is the deploy.

## 4. Integration job

The existing `integration` job already runs `uv run pytest -m integration`, which now includes
`tests/integration/test_retrieval_backend_parity.py` (dense artifact vs Qdrant, in-memory graph vs Neo4j). It needs
the fastembed and reranker models; cache them to keep the job under a few minutes:

```yaml
      - uses: actions/cache@v4
        with:
          path: backend/.model-cache
          key: models-${{ hashFiles('backend/src/unfold/core/config.py') }}
```

## 5. One-time operator steps (not CI)

1. `terraform -chdir=infra/terraform/bootstrap apply -var account_id=<id>` with a human principal; note the three
   role ARNs and the state bucket and lock table names; set them as GitHub repository variables.
2. Create the GitHub Environments `dev` and `prod` (prod with required reviewers).
3. Create the secrets (Terraform never holds them; a refresh would copy the decrypted value into state):
   `aws ssm put-parameter --name /unfold/<env>/gemini_api_key_1 --type SecureString --value ... --overwrite`
   and `.../proxy_hmac_secret` (the same value goes into the Vercel project). A parameter must exist before its name
   is listed in `worker_secret_names`.
4. Push the first images (step 3 above), then apply `envs/<env>` with the image URIs.
5. Confirm the SNS e-mail subscription for alarms (a click in the mailbox).
