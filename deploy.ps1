# deploy.ps1
# Packages main.py + dependencies and deploys them to AWS Lambda via the AWS CLI.
# Also wires up a public Function URL, which becomes the Discord Interactions
# Endpoint URL (replaces the ngrok URL used for local testing).
#
# PREREQUISITES (do these once, before running this script):
#   1. Install AWS CLI v2: https://aws.amazon.com/cli/
#   2. Run `aws configure` and set your Access Key, Secret Key, and default region.
#      The IAM user/role you use needs permission to manage Lambda, IAM roles,
#      and Function URLs (AdministratorAccess is fine for a personal project).
#   3. Make sure this folder has: main.py, requirements-lambda.txt, and a .env
#      file containing DISCORD_PUBLIC_KEY, BOT_TOKEN, APPLICATION_ID.
#
# Run it with:  .\deploy.ps1

$ErrorActionPreference = "Stop"

# ---- Config: adjust these to your setup ----
$FunctionName = "fb-embed-bot"
$Region       = "ap-southeast-1"     # change to your preferred AWS region
$RoleName     = "fb-embed-bot-role"
$Runtime      = "python3.12"
$Handler      = "main.lambda_handler"
$BuildDir     = "build"
$ZipFile      = "function.zip"

# ---- Step 1: clean up any previous build ----
Write-Host "Cleaning previous build..."
if (Test-Path $BuildDir) { Remove-Item -Recurse -Force $BuildDir }
if (Test-Path $ZipFile)  { Remove-Item -Force $ZipFile }
New-Item -ItemType Directory -Path $BuildDir | Out-Null

# ---- Step 2: install dependencies targeting Lambda's Linux runtime ----
# IMPORTANT: PyNaCl ships a compiled C extension. Installing it normally on
# Windows produces a Windows wheel that will NOT run on Lambda (Amazon Linux).
# These flags force pip to download manylinux wheels instead, matching Lambda.
Write-Host "Installing dependencies for Lambda (manylinux)..."
pip install -r requirements-lambda.txt `
    --target $BuildDir `
    --platform manylinux2014_x86_64 `
    --implementation cp `
    --python-version 3.12 `
    --only-binary=:all: `
    --upgrade

# ---- Step 3: copy the source code into the build folder ----
Copy-Item main.py $BuildDir

# ---- Step 4: zip everything up ----
Write-Host "Zipping package..."
Compress-Archive -Path "$BuildDir\*" -DestinationPath $ZipFile
Write-Host "Package ready: $ZipFile"

# ---- Step 5: create the IAM execution role (only runs once, skips if it exists) ----
$roleExists = aws iam get-role --role-name $RoleName 2>$null
if (-not $roleExists) {
    Write-Host "Creating IAM execution role $RoleName..."
    $trustPolicy = @'
{
  "Version": "2012-10-17",
  "Statement": [{
    "Effect": "Allow",
    "Principal": {"Service": "lambda.amazonaws.com"},
    "Action": "sts:AssumeRole"
  }]
}
'@
    $trustPolicy | Out-File -Encoding utf8 trust-policy.json
    aws iam create-role --role-name $RoleName --assume-role-policy-document file://trust-policy.json | Out-Null
    aws iam attach-role-policy --role-name $RoleName `
        --policy-arn arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole
    Remove-Item trust-policy.json
    Write-Host "Waiting for the role to propagate..."
    Start-Sleep -Seconds 10
}

$AccountId = (aws sts get-caller-identity --query Account --output text)
$RoleArn   = "arn:aws:iam::${AccountId}:role/$RoleName"

# ---- Step 6: create the function if it doesn't exist yet, otherwise update its code ----
$functionExists = aws lambda get-function --function-name $FunctionName --region $Region 2>$null
if ($functionExists) {
    Write-Host "Function exists, updating code..."
    aws lambda update-function-code `
        --function-name $FunctionName `
        --zip-file "fileb://$ZipFile" `
        --region $Region | Out-Null
} else {
    Write-Host "Creating new function..."
    aws lambda create-function `
        --function-name $FunctionName `
        --runtime $Runtime `
        --handler $Handler `
        --role $RoleArn `
        --zip-file "fileb://$ZipFile" `
        --timeout 10 `
        --memory-size 256 `
        --region $Region | Out-Null
}

# ---- Step 7: push secrets from .env into Lambda's environment variables ----
# Uses a JSON file (not an inline string) so values with special characters
# don't get mangled by shell quoting.
Write-Host "Setting environment variables from .env..."
$envVars = @{}
Get-Content .env | Where-Object { $_ -match "=" -and $_ -notmatch "^\s*#" } | ForEach-Object {
    $parts = $_ -split "=", 2
    $envVars[$parts[0].Trim()] = $parts[1].Trim()
}
@{ Variables = $envVars } | ConvertTo-Json -Depth 3 | Out-File -Encoding utf8 env-config.json

aws lambda update-function-configuration `
    --function-name $FunctionName `
    --environment file://env-config.json `
    --region $Region | Out-Null

Remove-Item env-config.json

# Lambda needs a moment to finish applying the update before the next call
Start-Sleep -Seconds 5

# ---- Step 8: expose a public Function URL (this is what Discord will call) ----
$urlConfigExists = aws lambda get-function-url-config --function-name $FunctionName --region $Region 2>$null
if (-not $urlConfigExists) {
    Write-Host "Creating public Function URL..."
    aws lambda create-function-url-config `
        --function-name $FunctionName `
        --auth-type NONE `
        --region $Region | Out-Null

    aws lambda add-permission `
        --function-name $FunctionName `
        --statement-id FunctionURLAllowPublicAccess `
        --action lambda:InvokeFunctionUrl `
        --principal "*" `
        --function-url-auth-type NONE `
        --region $Region | Out-Null
}

$FunctionUrl = aws lambda get-function-url-config `
    --function-name $FunctionName --region $Region --query FunctionUrl --output text

Write-Host ""
Write-Host "Deploy done."
Write-Host "Set this as your Discord Interactions Endpoint URL (paste exactly, no trailing changes):"
Write-Host $FunctionUrl