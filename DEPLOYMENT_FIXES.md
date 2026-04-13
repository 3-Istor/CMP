# Deployment Issues & Fixes

## Issue 1: Deployments Not Executing

The deployment is created but Terraform isn't running. This could be due to:

### Check 1: Terraform Installation

```bash
terraform --version
```

If not installed, install from https://terraform.io/downloads

### Check 2: OpenStack Credentials

Check `backend/.env` has:

```env
OS_AUTH_URL=http://192.168.1.210:5000/v3
OS_USERNAME=your_username
OS_PASSWORD=your_password
OS_PROJECT_NAME=your_project
OS_USER_DOMAIN_NAME=Default
OS_PROJECT_DOMAIN_NAME=Default
```

### Check 3: Backend Logs

Look for errors in the backend terminal. The background task should show:

```
INFO: [pending] Queued...
INFO: [initializing] 🔧 Initializing Terraform...
INFO: [planning] 📋 Planning deployment...
INFO: [deploying] 🚀 Deploying resources...
```

If you don't see these logs, the background task isn't running.

### Fix: Check Background Task

The issue might be that the database session isn't being passed correctly to the background task. Let me check the code.

## Issue 2: S3 Backend with Separate AWS Credentials

You want to use different AWS credentials for Terraform S3 backend state storage.

### Solution: Environment Variables

Add to `backend/.env`:

```env
# Main AWS account (for deployments)
AWS_ACCESS_KEY_ID=your_main_access_key
AWS_SECRET_ACCESS_KEY=your_main_secret_key
AWS_DEFAULT_REGION=eu-west-3

# S3 Backend AWS account (for Terraform state)
TF_BACKEND_AWS_ACCESS_KEY_ID=your_s3_access_key
TF_BACKEND_AWS_SECRET_ACCESS_KEY=your_s3_secret_key
TF_BACKEND_AWS_REGION=eu-west-3
TF_BACKEND_S3_BUCKET=your-terraform-state-bucket
TF_BACKEND_S3_KEY_PREFIX=deployments/
```

Then update the Terraform executor to use these credentials for backend configuration.

## Issue 3: Stepper Width & Auto-Centering

The deployment stepper is too wide and doesn't show all steps.

### Solution: Horizontal Scrolling with Auto-Center

Update the stepper to:

1. Use horizontal scroll
2. Auto-center on current step
3. Allow manual scrolling
4. Return to current step after 5s of inactivity

This requires updating `DeploymentStepper.tsx` with scroll behavior.
