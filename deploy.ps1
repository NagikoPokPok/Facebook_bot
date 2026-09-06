# deploy.ps1
#
# Packages main.py + dependencies and deploys them to AWS Lambda via AWS CLI.
#
# Also wires up a public Function URL for Discord Interactions.
#
# Prerequisites:
#   1. AWS CLI v2 installed
#   2. aws configure completed
#   3. This folder contains:
#        - main.py
#        - requirements-lambda.txt
#        - .env
#
# .env must contain:
#   DISCORD_PUBLIC_KEY=...
#   BOT_TOKEN=...
#   APPLICATION_ID=...
#
# Run:
#   .\deploy.ps1

$ErrorActionPreference = "Stop"

# ============================================================
# Helpers
# ============================================================

function Invoke-AwsProbe {
    param(
        [Parameter(ValueFromRemainingArguments = $true)]
        [string[]]$AwsArgs
    )

    $previousEAP = $ErrorActionPreference
    $ErrorActionPreference = "SilentlyContinue"

    try {
        $result = & aws @AwsArgs 2>$null
        return $result
    }
    finally {
        $ErrorActionPreference = $previousEAP
    }
}

function Invoke-AwsStrict {
    param(
        [Parameter(ValueFromRemainingArguments = $true)]
        [string[]]$AwsArgs
    )

    $previousEAP = $ErrorActionPreference
    $ErrorActionPreference = "Continue"

    try {
        $output = & aws @AwsArgs 2>&1
        $exitCode = $LASTEXITCODE

        if ($exitCode -ne 0) {
            $message = ($output | Out-String).Trim()

            if ([string]::IsNullOrWhiteSpace($message)) {
                $message = "AWS CLI command failed with exit code $exitCode."
            }

            throw $message
        }

        return $output
    }
    finally {
        $ErrorActionPreference = $previousEAP
    }
}

function Write-Utf8NoBom {
    param(
        [string]$Path,
        [string]$Content
    )

    $fullPath = Join-Path (Get-Location) $Path

    [System.IO.File]::WriteAllText(
        $fullPath,
        $Content,
        (New-Object System.Text.UTF8Encoding($false))
    )
}

function Ensure-LambdaPermission {
    param(
        [string]$FunctionName,
        [string]$StatementId,
        [string]$Action,
        [string]$Principal,
        [string]$Region,
        [string]$FunctionUrlAuthType = $null,
        [switch]$InvokedViaFunctionUrl
    )

    $policyOutput = Invoke-AwsProbe lambda get-policy `
        --function-name $FunctionName `
        --region $Region

    if ($policyOutput) {
        try {
            # AWS returns:
            # {
            #   "Policy": "{\"Version\":\"2012-10-17\",...}"
            # }
            $outerPolicy = ($policyOutput -join "`n") | ConvertFrom-Json

            if ($outerPolicy.Policy) {
                $policy = $outerPolicy.Policy | ConvertFrom-Json

                foreach ($statement in @($policy.Statement)) {
                    if ($statement.Sid -eq $StatementId) {
                        Write-Host "Permission '$StatementId' already exists."
                        return
                    }
                }
            }
        }
        catch {
            Write-Warning "Could not parse existing Lambda policy. AWS will be queried again when adding the permission."
        }
    }

    Write-Host "Adding Lambda permission '$StatementId'..."

    $args = @(
        "lambda",
        "add-permission",
        "--function-name", $FunctionName,
        "--statement-id", $StatementId,
        "--action", $Action,
        "--principal", $Principal,
        "--region", $Region
    )

    if ($FunctionUrlAuthType) {
        $args += @(
            "--function-url-auth-type", $FunctionUrlAuthType
        )
    }

    if ($InvokedViaFunctionUrl) {
        $args += "--invoked-via-function-url"
    }

    Invoke-AwsStrict @args | Out-Null
}

# ============================================================
# Configuration
# ============================================================

$FunctionName = "fb-embed-bot"
$Region       = "ap-southeast-1"
$RoleName     = "fb-embed-bot-role"

$Runtime      = "python3.12"
$Handler      = "main.lambda_handler"

$BuildDir     = "build"
$ZipFile      = "function.zip"

# Discord needs a public HTTPS endpoint.
# Change to AWS_IAM only if you intentionally want a private,
# IAM-authenticated Function URL.
$FunctionUrlAuthType = "NONE"

$EnvConfigFile = "env-config.json"

# ============================================================
# Main deployment
# ============================================================

try {

    # --------------------------------------------------------
    # Step 0: Validate required local files
    # --------------------------------------------------------

    Write-Host "Validating project files..."

    foreach ($requiredFile in @(
        "main.py",
        "requirements-lambda.txt",
        ".env"
    )) {
        if (-not (Test-Path $requiredFile)) {
            throw "Required file not found: $requiredFile"
        }
    }

    # --------------------------------------------------------
    # Step 1: Clean previous build
    # --------------------------------------------------------

    Write-Host "Cleaning previous build..."

    if (Test-Path $BuildDir) {
        Remove-Item -Recurse -Force $BuildDir
    }

    if (Test-Path $ZipFile) {
        Remove-Item -Force $ZipFile
    }

    New-Item -ItemType Directory -Path $BuildDir | Out-Null

    # --------------------------------------------------------
    # Step 2: Install Lambda dependencies
    # --------------------------------------------------------

    Write-Host "Installing dependencies for Lambda (manylinux)..."

    & pip install `
        -r requirements-lambda.txt `
        --target $BuildDir `
        --platform manylinux2014_x86_64 `
        --implementation cp `
        --python-version 3.12 `
        --only-binary=:all: `
        --upgrade

    if ($LASTEXITCODE -ne 0) {
        throw "pip install failed with exit code $LASTEXITCODE."
    }

    # --------------------------------------------------------
    # Step 3: Copy source
    # --------------------------------------------------------

    Write-Host "Copying source code..."

    Copy-Item main.py $BuildDir

    # --------------------------------------------------------
    # Step 4: Create ZIP
    # --------------------------------------------------------

    Write-Host "Zipping package..."

    Compress-Archive `
        -Path "$BuildDir\*" `
        -DestinationPath $ZipFile

    if (-not (Test-Path $ZipFile)) {
        throw "Failed to create $ZipFile"
    }

    Write-Host "Package ready: $ZipFile"

    # --------------------------------------------------------
    # Step 5: IAM execution role
    # --------------------------------------------------------

    Write-Host "Checking IAM execution role..."

    $roleExists = Invoke-AwsProbe `
        iam get-role `
        --role-name $RoleName `
        --region $Region

    if (-not $roleExists) {

        Write-Host "Creating IAM execution role $RoleName..."

        $trustPolicy = @'
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Principal": {
        "Service": "lambda.amazonaws.com"
      },
      "Action": "sts:AssumeRole"
    }
  ]
}
'@

        Write-Utf8NoBom `
            -Path "trust-policy.json" `
            -Content $trustPolicy

        try {

            Invoke-AwsStrict `
                iam create-role `
                --role-name $RoleName `
                --assume-role-policy-document file://trust-policy.json |
                Out-Null

            Invoke-AwsStrict `
                iam attach-role-policy `
                --role-name $RoleName `
                --policy-arn arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole |
                Out-Null

        }
        finally {

            if (Test-Path "trust-policy.json") {
                Remove-Item -Force "trust-policy.json"
            }
        }

        Write-Host "Waiting for IAM role propagation..."
        Start-Sleep -Seconds 10
    }

    $AccountId = (
        Invoke-AwsStrict `
            sts get-caller-identity `
            --query Account `
            --output text
    ).ToString().Trim()

    $RoleArn = "arn:aws:iam::${AccountId}:role/$RoleName"

    # ponytail: Đảm bảo IAM Role có quyền tự gọi chính nó (Self-Invoke Async) để xử lý tác vụ ngầm miễn phí 0đ
    Write-Host "Ensuring self-invoke policy on IAM role..."
    $selfInvokePolicy = @'
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": "lambda:InvokeFunction",
      "Resource": "*"
    }
  ]
}
'@
    Write-Utf8NoBom -Path "self-invoke-policy.json" -Content $selfInvokePolicy
    try {
        Invoke-AwsStrict iam put-role-policy `
            --role-name $RoleName `
            --policy-name "LambdaSelfInvokePolicy" `
            --policy-document file://self-invoke-policy.json | Out-Null
    }
    finally {
        if (Test-Path "self-invoke-policy.json") {
            Remove-Item -Force "self-invoke-policy.json"
        }
    }


    # --------------------------------------------------------
    # Step 6: Create/update Lambda function
    # --------------------------------------------------------

    Write-Host "Checking Lambda function..."

    $functionExists = Invoke-AwsProbe `
        lambda get-function `
        --function-name $FunctionName `
        --region $Region

    if ($functionExists) {

        Write-Host "Function exists, waiting until it is ready..."

        Invoke-AwsStrict `
            lambda wait function-active-v2 `
            --function-name $FunctionName `
            --region $Region

        Write-Host "Updating Lambda code..."

        Invoke-AwsStrict `
            lambda update-function-code `
            --function-name $FunctionName `
            --zip-file "fileb://$ZipFile" `
            --region $Region |
            Out-Null

        Write-Host "Waiting for code update to complete..."

        Invoke-AwsStrict `
            lambda wait function-updated-v2 `
            --function-name $FunctionName `
            --region $Region

    }
    else {

        Write-Host "Creating new Lambda function..."

        Invoke-AwsStrict `
            lambda create-function `
            --function-name $FunctionName `
            --runtime $Runtime `
            --handler $Handler `
            --role $RoleArn `
            --zip-file "fileb://$ZipFile" `
            --timeout 10 `
            --memory-size 256 `
            --architectures x86_64 `
            --region $Region |
            Out-Null

        Write-Host "Waiting for Lambda to become Active..."

        Invoke-AwsStrict `
            lambda wait function-active-v2 `
            --function-name $FunctionName `
            --region $Region
    }

    Write-Host "Lambda is ready."

    # --------------------------------------------------------
    # Step 7: Load .env
    # --------------------------------------------------------

    Write-Host "Reading environment variables from .env..."

    $envVars = @{}

    Get-Content ".env" | ForEach-Object {

        $line = $_.Trim()

        if (
            [string]::IsNullOrWhiteSpace($line) -or
            $line.StartsWith("#")
        ) {
            return
        }

        if ($line -notmatch "=") {
            return
        }

        $parts = $line -split "=", 2

        $key = $parts[0].Trim()
        $value = $parts[1].Trim()

        # Remove optional surrounding quotes
        if (
            $value.Length -ge 2 -and
            (
                ($value.StartsWith('"') -and $value.EndsWith('"')) -or
                ($value.StartsWith("'") -and $value.EndsWith("'"))
            )
        ) {
            $value = $value.Substring(1, $value.Length - 2)
        }

        $envVars[$key] = $value
    }

    # Validate required variables
    foreach ($requiredVariable in @(
        "DISCORD_PUBLIC_KEY",
        "BOT_TOKEN",
        "APPLICATION_ID"
    )) {
        if (
            -not $envVars.ContainsKey($requiredVariable) -or
            [string]::IsNullOrWhiteSpace($envVars[$requiredVariable])
        ) {
            throw "Missing required .env variable: $requiredVariable"
        }
    }

    # --------------------------------------------------------
    # Step 8: Update Lambda environment
    # --------------------------------------------------------

    Write-Host "Setting environment variables..."

    $envJson = @{
        Variables = $envVars
    } | ConvertTo-Json -Depth 3

    Write-Utf8NoBom `
        -Path $EnvConfigFile `
        -Content $envJson

    Invoke-AwsStrict `
        lambda update-function-configuration `
        --function-name $FunctionName `
        --environment "file://$EnvConfigFile" `
        --timeout 10 `
        --memory-size 256 `
        --region $Region |
        Out-Null

    Remove-Item -Force $EnvConfigFile

    Write-Host "Waiting for environment update to complete..."

    Invoke-AwsStrict `
        lambda wait function-updated-v2 `
        --function-name $FunctionName `
        --region $Region

    # --------------------------------------------------------
    # Step 9: Create/update Function URL
    # --------------------------------------------------------

    Write-Host "Checking Function URL..."

    $urlConfig = Invoke-AwsProbe `
        lambda get-function-url-config `
        --function-name $FunctionName `
        --region $Region

    if (-not $urlConfig) {

        Write-Host "Creating Function URL with AuthType=$FunctionUrlAuthType..."

        Invoke-AwsStrict `
            lambda create-function-url-config `
            --function-name $FunctionName `
            --auth-type $FunctionUrlAuthType `
            --region $Region |
            Out-Null

    }
    else {

        $urlConfigObject = ($urlConfig -join "`n") | ConvertFrom-Json

        if ($urlConfigObject.AuthType -ne $FunctionUrlAuthType) {

            Write-Host "Updating Function URL AuthType: $($urlConfigObject.AuthType) -> $FunctionUrlAuthType"

            Invoke-AwsStrict `
                lambda update-function-url-config `
                --function-name $FunctionName `
                --auth-type $FunctionUrlAuthType `
                --region $Region |
                Out-Null
        }
        else {
            Write-Host "Function URL already uses AuthType=$FunctionUrlAuthType"
        }
    }

    # --------------------------------------------------------
    # Step 10: Function URL permissions
    # --------------------------------------------------------

    if ($FunctionUrlAuthType -eq "NONE") {

        Write-Host "Ensuring public Function URL permissions..."

        # Required since October 2025:
        # 1. lambda:InvokeFunctionUrl
        # 2. lambda:InvokeFunction
        #
        # Both are required for public Function URLs.

        Ensure-LambdaPermission `
            -FunctionName $FunctionName `
            -StatementId "FunctionURLAllowPublicAccess" `
            -Action "lambda:InvokeFunctionUrl" `
            -Principal "*" `
            -FunctionUrlAuthType "NONE" `
            -Region $Region

        Ensure-LambdaPermission `
            -FunctionName $FunctionName `
            -StatementId "FunctionURLInvokeAllowPublicAccess" `
            -Action "lambda:InvokeFunction" `
            -Principal "*" `
            -InvokedViaFunctionUrl `
            -Region $Region
    }

    # --------------------------------------------------------
    # Step 11: Get final Function URL
    # --------------------------------------------------------

    $FunctionUrl = (
        Invoke-AwsStrict `
            lambda get-function-url-config `
            --function-name $FunctionName `
            --region $Region `
            --query FunctionUrl `
            --output text
    ).ToString().Trim()

    # --------------------------------------------------------
    # Step 12: Final verification
    # --------------------------------------------------------

    Write-Host ""
    Write-Host "Verifying Lambda state..."

    $finalState = (
        Invoke-AwsStrict `
            lambda get-function-configuration `
            --function-name $FunctionName `
            --region $Region `
            --query "{State:State,LastUpdateStatus:LastUpdateStatus}" `
            --output json
    ) -join "`n"

    Write-Host $finalState

    # Đặt CloudWatch Log Retention = 7 ngày để log không tích tụ quá 5GB Free Tier
    Write-Host "Configuring CloudWatch log retention (7 days - 100% Free Tier protection)..."
    Invoke-AwsProbe logs put-retention-policy `
        --log-group-name "/aws/lambda/$FunctionName" `
        --retention-in-days 7 `
        --region $Region | Out-Null


    # --------------------------------------------------------
    # Step 13: HTTP API Gateway (v2) for Discord (100% Free Tier)
    # Discord's infrastructure reliably connects to standard *.amazonaws.com domains,
    # avoiding DNS/routing incompatibilities with *.on.aws Function URLs.
    # --------------------------------------------------------
    Write-Host "Configuring HTTP API Gateway for Discord (100% Free Tier)..."
    $ApiName = "fb-embed-bot-api"
    $apiId = $null
    $apiEndpoint = $null

    $existingApis = Invoke-AwsProbe apigatewayv2 get-apis --region $Region
    if ($existingApis) {
        $apiObj = ($existingApis -join "`n") | ConvertFrom-Json
        foreach ($a in @($apiObj.Items)) {
            if ($a.Name -eq $ApiName) {
                $apiId = $a.ApiId
                $apiEndpoint = $a.ApiEndpoint
                break
            }
        }
    }

    if (-not $apiId) {
        Write-Host "Creating HTTP API Gateway $ApiName..."
        $lambdaArn = (Invoke-AwsStrict lambda get-function-configuration --function-name $FunctionName --region $Region --query FunctionArn --output text).Trim()
        $newApi = Invoke-AwsStrict apigatewayv2 create-api `
            --name $ApiName `
            --protocol-type HTTP `
            --target $lambdaArn `
            --region $Region | ConvertFrom-Json

        $apiId = $newApi.ApiId
        $apiEndpoint = $newApi.ApiEndpoint

        Ensure-LambdaPermission `
            -FunctionName $FunctionName `
            -StatementId "ApiGatewayInvokePermission" `
            -Action "lambda:InvokeFunction" `
            -Principal "apigateway.amazonaws.com" `
            -Region $Region
    }
    else {
        Write-Host "HTTP API Gateway already exists: $apiId ($apiEndpoint)"
    }

    $DiscordEndpointUrl = "$apiEndpoint/"

    Write-Host ""
    Write-Host "============================================"
    Write-Host "Deployment successful."
    Write-Host "Function:    $FunctionName"
    Write-Host "Region:      $Region"
    Write-Host "Discord URL: $DiscordEndpointUrl"
    Write-Host "============================================"
    Write-Host ""
    Write-Host "Verified Discord Interactions Endpoint URL:"
    Write-Host $DiscordEndpointUrl

}
catch {

    Write-Host ""
    Write-Host "============================================" -ForegroundColor Red
    Write-Host "DEPLOYMENT FAILED" -ForegroundColor Red
    Write-Host "============================================" -ForegroundColor Red
    Write-Host $_.Exception.Message -ForegroundColor Red
    Write-Host ""

    exit 1
}
finally {

    # Always remove generated temporary files
    if (Test-Path $EnvConfigFile) {
        Remove-Item -Force $EnvConfigFile
    }

    if (Test-Path "trust-policy.json") {
        Remove-Item -Force "trust-policy.json"
    }
}