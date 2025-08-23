# Docker Compose CI Workflow

This repository includes a GitHub Actions CI pipeline that automatically builds and tests the Docker Compose setup when new tags are pushed.

## Workflow Overview

The CI pipeline (`/.github/workflows/docker-compose-ci.yml`) includes the following features:

### Trigger Conditions

- **Tag pushes**: Automatically triggers on any tag matching patterns:
  - `v*` (e.g., `v1.2.3`, `v2.0.0-beta`)  
  - `*.*.*` (e.g., `1.2.3`, `2.0.0`)
- **Manual trigger**: Can be triggered manually via GitHub Actions UI

### Pipeline Jobs

#### 1. Build and Test Job (`build-and-test`)

- **Environment validation**: Creates `.env` file from template
- **Docker Compose validation**: Validates the `docker-compose.yml` configuration
- **Image building**: Builds Docker image using a CI-friendly Dockerfile
- **Container testing**: Tests that the built container can start successfully
- **Tag extraction**: Extracts version information from Git tags
- **Version update**: Updates `docker-compose.yml` with the new tag version

#### 2. Security Scan Job (`security-scan`)

- **Vulnerability scanning**: Uses Trivy to scan the built image for security vulnerabilities
- **Security reporting**: Uploads scan results to GitHub Security tab
- **Only runs on tag pushes**: Skipped for manual workflow triggers

### Docker Image Publishing

When a tag is pushed (not manual trigger):

1. **Login to Docker Hub**: Uses `DOCKER_USERNAME` and `DOCKER_PASSWORD` secrets
2. **Build production image**: Attempts to build with the original Dockerfile
3. **Tag images**: Creates both version-specific and `latest` tags
4. **Push to registry**: Pushes to `trytodupe/ttd-bot` on Docker Hub

## Setup Requirements

### GitHub Secrets

For Docker image publishing, configure these secrets in your GitHub repository:

- `DOCKER_USERNAME`: Your Docker Hub username
- `DOCKER_PASSWORD`: Your Docker Hub password or access token

### Repository Configuration

1. Ensure `docker-compose.yml` exists in the repository root
2. Ensure `.env.example` exists as a template for environment variables
3. Ensure `Dockerfile` exists for building the production image

## Usage

### Automatic Trigger

1. Create and push a new tag:
   ```bash
   git tag v1.2.3
   git push origin v1.2.3
   ```

2. The CI pipeline will automatically:
   - Build and test the Docker image
   - Update `docker-compose.yml` with the new version
   - Push the image to Docker Hub (if secrets are configured)
   - Run security scans

### Manual Trigger

1. Go to the Actions tab in your GitHub repository
2. Select "Docker Compose CI" workflow  
3. Click "Run workflow" button
4. Choose the branch and click "Run workflow"

## CI Adaptations

The CI pipeline includes adaptations for different environments:

- **Network-resilient building**: Uses simplified Dockerfile for CI testing that doesn't depend on external mirrors
- **SSL certificate handling**: Gracefully handles certificate issues in CI environments
- **Flexible dependency management**: Falls back to basic validation when full dependency installation fails

## Troubleshooting

### Common Issues

1. **Build failures due to mirrors**: The original Dockerfile uses Chinese mirrors that may not be accessible in all CI environments
2. **Certificate verification errors**: SSL certificate issues can prevent package installations
3. **Git dependency access**: Private or restricted GitHub repositories may cause build failures

### Solutions

- The CI pipeline includes fallback mechanisms for these issues
- For production deployment, ensure the build environment can access required dependencies
- Consider using Docker Hub or other registries for dependency mirrors in restricted environments

## Files Created

- `/.github/workflows/docker-compose-ci.yml`: Main CI workflow
- `/README-CI.md`: This documentation file

## Monitoring

- Check the Actions tab for workflow run status
- Security scan results appear in the Security tab under "Code scanning alerts"
- Failed builds will show detailed logs for troubleshooting