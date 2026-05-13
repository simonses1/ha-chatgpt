param(
    [Parameter(Mandatory = $true)]
    [string]$HaUrl,

    [Parameter(Mandatory = $true)]
    [string]$HaToken,

    [string]$EntryId,

    [string]$Name = "OpenAI Assist POC",

    [string]$CodexAuthJsonPath = "~/.codex/auth.json",

    [string]$Model = "gpt-5.3-codex-spark",

    [string]$Prompt = "You are a concise Home Assistant voice assistant. Answer directly.",

    [string]$Text = "Reply with the single word pong.",

    [switch]$Configure
)

$ErrorActionPreference = "Stop"
$HaUrl = $HaUrl.TrimEnd("/")
$headers = @{
    Authorization = "Bearer $HaToken"
}
$jsonHeaders = @{
    Authorization = "Bearer $HaToken"
    "Content-Type" = "application/json"
}

function Invoke-HaJson {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Method,

        [Parameter(Mandatory = $true)]
        [string]$Path,

        [object]$Body
    )

    $uri = "$HaUrl$Path"
    if ($null -eq $Body) {
        return Invoke-RestMethod -Method $Method -Uri $uri -Headers $headers
    }

    $bodyJson = $Body | ConvertTo-Json -Depth 20
    return Invoke-RestMethod -Method $Method -Uri $uri -Headers $jsonHeaders -Body $bodyJson
}

Write-Host "Checking Home Assistant API at $HaUrl"
$apiStatus = Invoke-HaJson -Method Get -Path "/api"
Write-Host "API status: $($apiStatus.message)"

if ($Configure) {
    Write-Host "Starting config flow for openai_oauth_assist"
    $flow = Invoke-HaJson -Method Post -Path "/api/config/config_entries/flow" -Body @{
        handler = "openai_oauth_assist"
        show_advanced_options = $false
    }

    if ($flow.type -ne "form") {
        throw "Expected a form flow result, got '$($flow.type)': $($flow | ConvertTo-Json -Depth 10)"
    }

    Write-Host "Submitting config flow $($flow.flow_id)"
    Write-Host "Home Assistant will read Codex auth JSON from: $CodexAuthJsonPath"
    $result = Invoke-HaJson -Method Post -Path "/api/config/config_entries/flow/$($flow.flow_id)" -Body @{
        name = $Name
        codex_auth_json_path = $CodexAuthJsonPath
        model = $Model
        system_prompt = $Prompt
    }

    if ($result.type -ne "create_entry") {
        throw "Config flow did not create an entry: $($result | ConvertTo-Json -Depth 10)"
    }

    $EntryId = $result.result.entry_id
    Write-Host "Created config entry: $EntryId"
}

if ([string]::IsNullOrWhiteSpace($EntryId)) {
    throw "EntryId is required unless -Configure creates one."
}

Write-Host "Calling conversation API with agent_id $EntryId"
$conversation = Invoke-HaJson -Method Post -Path "/api/conversation/process" -Body @{
    text = $Text
    language = "en"
    agent_id = $EntryId
}

$speech = $conversation.response.speech.plain.speech
Write-Host "Conversation ID: $($conversation.conversation_id)"
Write-Host "Speech: $speech"

if ([string]::IsNullOrWhiteSpace($speech)) {
    throw "No speech text returned from conversation API."
}
