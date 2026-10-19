# AWS deployment

This is a single-instance preview deployment for an ordinary AWS account. Terraform
creates its own VPC/subnet, EC2 instance, Elastic IP, private S3 release bucket,
ECR application repository, GitHub secret container and optional budget alerts.
It does not depend on GO infrastructure. Solr and the application have no public
ports; Caddy exposes HTTPS. Administration uses AWS Systems Manager (SSM), not SSH.

The default is **private**: every page, API and download requires an approved
GitHub identity. Hosted chat is disabled; no Claude credentials or agent runtime
are installed. A public mode allows browsing while retaining the login allowlist.

The host only downloads an application image and restores a centrally built
DuckDB/Parquet/Solr release. It does not run `amigo3 build` or `amigo3 publish`.

## Prerequisites

- Terraform >= 1.9, AWS CLI v2, Docker with Buildx, Python >= 3.11.
- An AWS CLI profile allowed to create the resources above and use SSM. Deployment
  operators also need S3 upload, ECR push and Secrets Manager PutSecretValue.
  The instance role has only release/image read, the one GitHub secret, and SSM.
- A DNS hostname you control, e.g. `amigo.example.org`.
- A completed, published AmiGO build and access to the matching Solr core directory.
  Run the packager on the build machine. With Docker, use the host-side mounted
  core directory; an anonymous Docker volume must first be made accessible there.
- A GitHub App: homepage `https://amigo.example.org`, user authorization callback
  `https://amigo.example.org/auth/github/callback`. No repository/organization/account
  permissions or installation are needed. See [login setup](login.md).

## 1. Review and create infrastructure

From the repository root:

```bash
cp terraform/terraform.tfvars.example terraform/terraform.tfvars
# Edit hostname, deployment name and approved numeric GitHub IDs.
# Resolve each ID: gh api users/YOUR_HANDLE --jq .id
export AWS_PROFILE=your-personal-profile
terraform -chdir=terraform init
terraform -chdir=terraform plan -out=preview.tfplan
# Review costs, account, region and the planned resources before applying.
terraform -chdir=terraform apply preview.tfplan
terraform -chdir=terraform output
```

The plan/apply is an operator step; creating the configuration does not provision
anything. `terraform.tfvars`, state and saved plans are ignored by Git. Preserve
state securely. The checked-in provider lockfile pins the tested provider version.
An independent S3 state backend can be added for shared operation later.

Defaults are `us-east-1`, `t3.large`, encrypted 100 GiB gp3 storage. Plan for roughly
$80/month before model usage or heavy traffic; allow headroom for ECR images,
the GitHub secret and retained releases. The optional $100 budget is account-wide
and sends actual/forecast alerts; it does not cap spending. Completed S3 releases
and ECR images are deliberately not automatically deleted. Review retention.

Point an A record for your hostname to `public_ip`. DNS is intentionally external
to this Terraform module, so either Cloudflare, Route53 or another provider works.
Start Cloudflare DNS-only while Caddy obtains its certificate, then optionally
enable proxying with **Full (strict)** TLS. Bypass Cloudflare caching for a private
deployment; do not add a Cache Everything rule. AmiGO also marks private responses
`Cache-Control: no-store`. Cloudflare Access is not needed for the app's GitHub gate.

Cloud-init installs Docker/Compose and the release installer. Check completion via
the `admin_command` output and `sudo cloud-init status --wait` before deploying.
SSM requires the AWS Session Manager plugin on your computer, or use the EC2 console's
Session Manager connection. No SSH key or inbound port 22 is needed.

## 2. Set the GitHub credential outside Terraform

Create `deploy/github-secret.json` locally, with file permissions restricted to you:

```json
{"github_client_id": "YOUR_CLIENT_ID", "github_client_secret": "YOUR_CLIENT_SECRET"}
```

```bash
chmod 600 deploy/github-secret.json
aws --region us-east-1 secretsmanager put-secret-value \
  --secret-id "$(terraform -chdir=terraform output -raw github_secret_arn)" \
  --secret-string file://deploy/github-secret.json
```

This file is gitignored. Keep the credential in your password manager and remove
the local JSON once stored. Terraform manages only the secret's metadata; the
value never enters Terraform state, plans or cloud-init. During deployment, the
instance fetches it into `/etc/amigo3/github.env`, readable only by root. The app
receives it as environment variables. Do not paste it into SSM command text.

## 3. Build the Linux image centrally

Build from a committed checkout corresponding to the release. Even on Apple
Silicon, use `linux/amd64` for this x86 EC2 configuration. Native Python/Rust
dependencies are built inside the image builder, not on EC2.

```bash
AMIGO_REPO=$(terraform -chdir=terraform output -raw image_repository)
AMIGO_REV=$(git rev-parse HEAD)
aws --region us-east-1 ecr get-login-password |
  docker login --username AWS --password-stdin "${AMIGO_REPO%%/*}"
docker buildx build --platform linux/amd64 -f deploy/Dockerfile \
  -t "$AMIGO_REPO:$AMIGO_REV" --push .
AMIGO_DIGEST=$(aws --region us-east-1 ecr describe-images \
  --repository-name "${AMIGO_REPO#*/}" --image-ids "imageTag=$AMIGO_REV" \
  --query 'imageDetails[0].imageDigest' --output text)
```

ECR tags are immutable. Reuse the existing digest if that commit is already
published. Do not build changed, uncommitted code under an existing commit tag.
Docker's build context is restricted to application source and packaging inputs;
local data, credentials, Terraform state and development environments are excluded.

## 4. Package and upload a complete release

Keep the source build and its Solr core unchanged during packaging. The script
checks Parquet checksums, Solr version and collection counts, requests a hard
commit and a replication-handler backup, then packages that snapshot with the
core configuration. It never copies an actively written Lucene index directory.
The packager finds per-core configuration or a sibling `configsets/NAME/conf`
referenced by `core.properties`; use `--config-dir` for another config-set location.
Solr is pinned to **9.9.0** on both ends. No indexing runs during deployment.

```bash
python3 deploy/release.py \
  --data /path/to/amigo3/data \
  --core-dir /path/to/solr/server/solr/amigo3_BUILD_ID \
  --solr http://127.0.0.1:8987/solr \
  --image "$AMIGO_REPO@$AMIGO_DIGEST" --revision "$AMIGO_REV" \
  --output dist/amigo3-release.tar.gz
AMIGO_BUCKET=$(terraform -chdir=terraform output -raw release_bucket)
AMIGO_SHA=$(cut -d ' ' -f 1 dist/amigo3-release.tar.gz.sha256)
aws --region us-east-1 s3 cp dist/amigo3-release.tar.gz \
  "s3://$AMIGO_BUCKET/releases/$AMIGO_SHA.tar.gz"
```

`BUILD_ID` is from `data/published.json`, which may differ from `current.json`.
The packager prints the retained source Solr backup path; remove that backup when
no longer needed. The archive includes per-file checksums, build ID, image digest,
source revision, Solr version and counts. Its outer SHA256 identifies the S3 key.
The deployment account's IAM permissions establish trust; checksums detect damage,
not a malicious publisher with permission to upload and deploy another digest.

## 5. Activate on EC2

Open an SSM session using the Terraform `admin_command` output. On the instance:

```bash
sudo cloud-init status --wait
sudo amigo3-deploy releases/ARCHIVE_SHA256.tar.gz ARCHIVE_SHA256
```

Replace both placeholders with the packager's actual SHA256. The installer:

1. Downloads and verifies the archive, rejects unsafe tar entries, and checks every
   file against the manifest.
2. Fetches credentials and pulls the digest-pinned image from this deployment's ECR.
3. Stops the previous stack, starts the restored release, waits for readiness, and
   checks every Solr collection count against the manifest.
4. Records the successful release, or restarts the old release on cutover failure.

This is a single-server deployment with a short outage during activation. App
sessions reset on restart. A failure before cutover leaves the running stack alone.
The readiness check is internal; it does not expose private data anonymously.

Visit the HTTPS hostname. Verify both an approved and an unapproved GitHub login,
anonymous API denial, and search/enrichment after login. Certificate issuance and
real OAuth callback registration must be smoke-tested on the deployed hostname.

## Operations

Rollback the last successful activation:

```bash
sudo amigo3-deploy --rollback
```

Inspect services on the host:

```bash
sudo docker compose --env-file /var/lib/amigo3/current.env \
  -f /opt/amigo3/compose.yaml ps
sudo docker compose --env-file /var/lib/amigo3/current.env \
  -f /opt/amigo3/compose.yaml logs --tail 100
```

Logs rotate at 3 × 10 MB per service. Release directories are under
`/var/lib/amigo3/releases`. Retain the directories referenced by `current.env`
and `previous.env`; remove older unused directories and image digests deliberately
as storage fills. Failed activation directories may remain for diagnosis. S3
versioning also retains overwritten/deleted object versions, which continue to
cost storage. Use unique digest keys and an explicit retention policy.

The allowlist is `/etc/amigo3/access/allowlist.json`, initially populated from
Terraform's `github_user_ids`. It has the shape `{"github_ids": [1234, 5678]}`.
Edit via SSM using root permissions; preferably write a temporary file then rename
it over the original. The app denies removed users on their next request and ends
their existing sessions within five seconds, including local chat sessions if used
outside this deployment. A missing, invalid or empty list fails closed. Mirror
edits into tfvars so a future replacement preserves them.

Changes to bootstrap configuration (including Terraform's initial access policy)
replace the disposable EC2 host. Review the plan, expect an outage, then redeploy
the selected artifact using SSM. The EIP and artifact stores survive an instance
replacement. AMI publication alone does not trigger replacement; plan patching
explicitly. Host/root-disk data is disposable; durable release artifacts live in
S3 and ECR. Secrets Manager retains deleted secrets for seven days. Terraform
refuses to destroy nonempty S3/ECR stores without an explicit cleanup decision.

## Local validation

```bash
terraform -chdir=terraform init -backend=false
terraform -chdir=terraform fmt -check -recursive
terraform -chdir=terraform validate
terraform -chdir=terraform test
uv run --extra chat pytest tests/test_auth.py tests/test_private_site.py tests/test_release.py tests/test_deploy.py
uv run ruff check .
uv run ruff format --check .
```

Terraform tests use a mocked AWS provider and do not create cloud resources.
The release tests check archive traversal/link rejection, corruption and pinned
build metadata. Authentication tests cover private pages/APIs/assets, denied
identities, live revocation and malformed/missing allowlists.

For a real backup/restore test, start a separate Solr 9.9.0 instance with its own
home and default config set, then run:

```bash
AMIGO3_RELEASE_TEST_SOLR_HOME=/absolute/path/to/isolated/solr-home \
AMIGO3_RELEASE_TEST_SOLR_URL=http://127.0.0.1:18987/solr \
  uv run pytest tests/test_release_solr.py
```

This test publishes a fixture core, packages its backup, opens the restored index
as another core, and compares record payloads and counts. Do not point it at a
production Solr instance. The remaining backend tests can run separately with
`uv run --extra chat pytest --ignore=tests/browser`; the existing browser test
directory has duplicate module names and should be collected separately.

References: [Solr single-node backup/restore](https://solr.apache.org/guide/solr/9_9/deployment-guide/backup-restore.html),
[AWS provider](https://registry.terraform.io/providers/hashicorp/aws/latest/docs),
[Caddy automatic HTTPS](https://caddyserver.com/docs/automatic-https).
