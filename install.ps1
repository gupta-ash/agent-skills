#requires -Version 5.1
[CmdletBinding()]
param(
    [Parameter(Mandatory=$true)][ValidatePattern('^v(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$')][string]$Version,
    [Parameter(Mandatory=$true)][string]$WorkRoot,
    [ValidateSet('Check','Install','Verify')][string]$Mode = 'Install',
    [string]$UserRoot,
    [string[]]$Harness,
    [string[]]$Skills,
    [string]$PythonExe,
    [string]$RuntimeConfigDir,
    [switch]$AdoptExisting
)
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version 2
$Component = 'agent-skills'
$Repository = 'gupta-ash/agent-skills'
$PrivateRelease = $false

function Assert-True([bool]$Condition, [string]$Message) {
    if (-not $Condition) { throw $Message }
}
function Get-Absolute([string]$Path) {
    Assert-True (-not [string]::IsNullOrWhiteSpace($Path) -and [IO.Path]::IsPathRooted($Path) -and $Path -notmatch '^[\\/]{2}' -and $Path -notmatch '(^|[\\/])\.\.([\\/]|$)') 'Path must be absolute, local, and traversal-free.'
    return [IO.Path]::GetFullPath($Path).TrimEnd('\','/')
}
function Test-Under([string]$Path,[string]$Root) {
    return $Path.Equals($Root,[StringComparison]::OrdinalIgnoreCase) -or $Path.StartsWith($Root+[IO.Path]::DirectorySeparatorChar,[StringComparison]::OrdinalIgnoreCase)
}
function Assert-Unlinked([string]$Path) {
    $Cursor=$Path
    while ($Cursor) {
        if (Test-Path -LiteralPath $Cursor) {
            $Item=Get-Item -LiteralPath $Cursor -Force
            Assert-True (($Item.Attributes -band [IO.FileAttributes]::ReparsePoint) -eq 0) ('Reparse point refused: '+$Cursor)
        }
        $Parent=[IO.Path]::GetDirectoryName($Cursor)
        if ($Parent -eq $Cursor) { break }; $Cursor=$Parent
    }
}
function Assert-Owned([string]$Path) {
    $Acl=Get-Acl -LiteralPath $Path
    $Owner=$Acl.Owner
    if ($Owner -notmatch '^S-1-') { $Owner=([Security.Principal.NTAccount]$Owner).Translate([Security.Principal.SecurityIdentifier]).Value }
    Assert-True ($Owner -eq [Security.Principal.WindowsIdentity]::GetCurrent().User.Value) ('Directory is not owned by current account: '+$Path)
}
function Get-Sha([string]$Path) { return (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash.ToLowerInvariant() }
function Get-BytesSha([byte[]]$Bytes) {
    $Hash=[Security.Cryptography.SHA256]::Create()
    try { return ([BitConverter]::ToString($Hash.ComputeHash($Bytes))).Replace('-','').ToLowerInvariant() } finally { $Hash.Dispose() }
}
function Get-GitBlobSha([byte[]]$Bytes) {
    $Hash=[Security.Cryptography.SHA1]::Create();$Stream=New-Object IO.MemoryStream
    try {
        $Header=[Text.Encoding]::ASCII.GetBytes(('blob '+$Bytes.Length+"`0"))
        $Stream.Write($Header,0,$Header.Length);$Stream.Write($Bytes,0,$Bytes.Length);$Stream.Position=0
        return ([BitConverter]::ToString($Hash.ComputeHash($Stream))).Replace('-','').ToLowerInvariant()
    } finally { $Hash.Dispose();$Stream.Dispose() }
}
function Assert-Fields($Object,[string[]]$Fields,[string]$Description) {
    $Actual=@($Object.PSObject.Properties.Name | Sort-Object)
    Assert-True (($Actual -join '|') -ceq (($Fields | Sort-Object) -join '|')) ('Invalid '+$Description+' fields.')
}
function Assert-Member([string]$Name) {
    Assert-True ($Name -and $Name -notmatch '[\\:]|^/|/$|//|(^|/)\.{1,2}(/|$)' ) 'Unsafe ZIP member.'
    foreach($Part in $Name.Split('/')) {
        Assert-True ($Part -notmatch '[. ]$|^(?i:CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\.|$)') 'Reserved ZIP member.'
    }
}
function Get-Api([string]$Endpoint) {
    if ($PrivateRelease) {
        Assert-True ($null -ne (Get-Command gh -ErrorAction SilentlyContinue)) 'Private releases require an already-authenticated GitHub CLI.'
        $Text=& gh api $Endpoint
        Assert-True ($LASTEXITCODE -eq 0) 'Authenticated GitHub metadata request failed.'
        return (($Text -join "`n") | ConvertFrom-Json)
    }
    [Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12
    return Invoke-RestMethod -Uri ('https://api.github.com/'+$Endpoint) -Headers @{'User-Agent'=$Component+'-installer';'Accept'='application/vnd.github+json'}
}
function Download-Asset($Release,[string]$Name,[string]$Directory) {
    $Assets=@($Release.assets | Where-Object { $_.name -ceq $Name })
    Assert-True ($Assets.Count -eq 1) ('Exact release asset missing/ambiguous: '+$Name)
    if ($PrivateRelease) {
        & gh release download $Version --repo $Repository --pattern $Name --dir $Directory
        Assert-True ($LASTEXITCODE -eq 0) ('Authenticated release download failed: '+$Name)
    } else {
        Assert-True ($Assets[0].browser_download_url.StartsWith('https://github.com/'+$Repository+'/releases/download/',[StringComparison]::Ordinal)) 'Unexpected release asset URL.'
        Invoke-WebRequest -UseBasicParsing -Uri $Assets[0].browser_download_url -OutFile (Join-Path $Directory $Name) -Headers @{'User-Agent'=$Component+'-installer'}
    }
}

try {
    Assert-True ($env:OS -eq 'Windows_NT') 'Scripted installation currently supports Windows PowerShell 5.1+ only.'
    $ActualProfile=[Environment]::GetFolderPath([Environment+SpecialFolder]::UserProfile)
    Assert-True ($ActualProfile -and (Get-Absolute $env:USERPROFILE).Equals((Get-Absolute $ActualProfile),[StringComparison]::OrdinalIgnoreCase)) 'Actual account profile disagrees with USERPROFILE.'
    if (-not $UserRoot) { $UserRoot=$ActualProfile }
    $UserRoot=Get-Absolute $UserRoot
    Assert-True ($UserRoot.Equals((Get-Absolute $ActualProfile),[StringComparison]::OrdinalIgnoreCase)) 'UserRoot must be the actual invoking account profile.'
    Assert-Unlinked $UserRoot; Assert-Owned $UserRoot
    $WorkRoot=Get-Absolute $WorkRoot;Assert-Unlinked $WorkRoot
    foreach($Root in @((Join-Path $UserRoot '.agents'),(Join-Path $UserRoot '.claude'),(Join-Path $UserRoot '.omp'))) {
        Assert-True (-not ((Test-Under $WorkRoot $Root) -or (Test-Under $Root $WorkRoot))) 'WorkRoot overlaps installation/state roots.'
    }
    if ($Mode -eq 'Verify') {
        if (Test-Path -LiteralPath $WorkRoot) { Assert-Owned $WorkRoot } else { Assert-Owned ([IO.Path]::GetDirectoryName($WorkRoot)) }
    }
    $Install=Join-Path $UserRoot ('.agents/installations/'+$Component)
    Assert-Unlinked $Install
    $ReceiptPath=Join-Path $Install 'installed.json'
    $Installed=$null
    if (Test-Path -LiteralPath $ReceiptPath -PathType Leaf) {
        $Installed=Get-Content -LiteralPath $ReceiptPath -Raw | ConvertFrom-Json
        Assert-Fields $Installed @('schema_version','component','repository','version','source_commit','archive_sha256','parameters','files','configuration_paths','held_paths','last_code_change_utc') 'installed receipt'
        Assert-True ($Installed.schema_version -eq 1 -and $Installed.component -ceq $Component -and $Installed.repository -ceq $Repository) 'Installed identity mismatch.'
        Assert-True ((Get-Absolute $Installed.parameters.UserRoot).Equals($UserRoot,[StringComparison]::OrdinalIgnoreCase)) 'Retained account/root mismatch.'
        if (-not $PythonExe) { $PythonExe=$Installed.parameters.PythonExe }
    }
    if (-not $PythonExe) {
        $Command=Get-Command python -CommandType Application -ErrorAction SilentlyContinue
        Assert-True ($null -ne $Command) 'PythonExe prerequisite: select an existing base Python 3.11+ interpreter.'
        $PythonExe=$Command.Source
    }
    $PythonExe=Get-Absolute $PythonExe; Assert-Unlinked $PythonExe
    Assert-True (Test-Path -LiteralPath $PythonExe -PathType Leaf) 'PythonExe does not exist.'
    & $PythonExe -I -B -c "import sys; sys.exit(0 if sys.version_info >= (3, 11) and sys.prefix == sys.base_prefix else 1)"
    Assert-True ($LASTEXITCODE -eq 0) 'PythonExe prerequisite: existing base Python 3.11+ is required; no runtime is provisioned.'
    $Arguments=@('-I','-B')
    $Backend=$null
    if ($Mode -eq 'Verify') {
        Assert-True ($null -ne $Installed) 'No retained installation exists to verify.'
        Assert-True ($Installed.version -ceq $Version) 'Expected version mismatch.'
        $Backend=Join-Path $Install 'install.py'
        $VerifierRows=@($Installed.files | Where-Object { $_.path -and (Get-Absolute $_.path).Equals($Backend,[StringComparison]::OrdinalIgnoreCase) })
        Assert-True ($VerifierRows.Count -eq 1) 'Retained verifier is not receipt-bound.'
        Assert-Unlinked $Backend
        Assert-True ((Get-Sha $Backend) -ceq $VerifierRows[0].sha256) 'Retained verifier drift; refusing to execute.'
    } else {
        $WorkRoot=Get-Absolute $WorkRoot; Assert-Unlinked $WorkRoot
        foreach ($Root in @((Join-Path $UserRoot '.agents'),(Join-Path $UserRoot '.claude'),(Join-Path $UserRoot '.omp'))) {
            Assert-True (-not ((Test-Under $WorkRoot $Root) -or (Test-Under $Root $WorkRoot))) 'WorkRoot overlaps installation/state roots.'
        }
        if (Test-Path -LiteralPath (Join-Path $PSScriptRoot '.git')) {
            Assert-True (-not ((Test-Under $WorkRoot $PSScriptRoot) -or (Test-Under $PSScriptRoot $WorkRoot))) 'WorkRoot overlaps source repository.'
        }
        if (-not (Test-Path -LiteralPath $WorkRoot)) {
            $Parent=[IO.Path]::GetDirectoryName($WorkRoot)
            Assert-Unlinked $Parent;Assert-Owned $Parent
            [IO.Directory]::CreateDirectory($WorkRoot) | Out-Null
        }
        Assert-Owned $WorkRoot
        $Acl=Get-Acl -LiteralPath $WorkRoot;$Acl.SetAccessRuleProtection($true,$false)
        foreach($ExistingRule in @($Acl.Access)) { [void]$Acl.RemoveAccessRuleSpecific($ExistingRule) }
        foreach($Sid in @([Security.Principal.WindowsIdentity]::GetCurrent().User.Value,'S-1-5-18','S-1-5-32-544')) {
            $Identity=New-Object Security.Principal.SecurityIdentifier($Sid)
            $Rule=New-Object Security.AccessControl.FileSystemAccessRule($Identity,'FullControl','ContainerInherit,ObjectInherit','None','Allow');$Acl.AddAccessRule($Rule)
        }
        Set-Acl -LiteralPath $WorkRoot -AclObject $Acl
        foreach($Name in @('TEMP','TMP','TMPDIR','CLAUDE_CODE_TMPDIR')) { [Environment]::SetEnvironmentVariable($Name,$WorkRoot,'Process') }
        $env:PYTHONDONTWRITEBYTECODE='1'
        $Operation=Join-Path $WorkRoot ('operation-'+[Guid]::NewGuid().ToString('N'))
        [IO.Directory]::CreateDirectory($Operation) | Out-Null
        $Reference=Get-Api ('repos/'+$Repository+'/git/ref/tags/'+[Uri]::EscapeDataString($Version))
        $Object=$Reference.object
        for($Depth=0;$Object.type -ceq 'tag' -and $Depth -lt 8;$Depth++) { $Object=(Get-Api ('repos/'+$Repository+'/git/tags/'+$Object.sha)).object }
        Assert-True ($Object.type -ceq 'commit' -and $Object.sha -cmatch '^[0-9a-f]{40}$') 'Tag did not dereference to a full commit.'
        $SourceCommit=$Object.sha
        $Commit=Get-Api ('repos/'+$Repository+'/git/commits/'+$SourceCommit)
        Assert-True ($Commit.sha -ceq $SourceCommit -and $Commit.tree.sha -cmatch '^[0-9a-f]{40}$') 'Committed tree identity mismatch.'
        $Tree=Get-Api ('repos/'+$Repository+'/git/trees/'+$Commit.tree.sha+'?recursive=1')
        Assert-True (-not $Tree.truncated) 'Committed tree response is incomplete.'
        $Committed=@{}
        foreach($Entry in $Tree.tree) { $Committed[$Entry.path]=$Entry }
        $Release=Get-Api ('repos/'+$Repository+'/releases/tags/'+[Uri]::EscapeDataString($Version))
        Assert-True ($Release.tag_name -ceq $Version -and -not $Release.draft -and -not $Release.prerelease) 'Exact stable release required.'
        $ArchiveName=$Component+'-'+$Version+'.zip'
        foreach($Asset in @($ArchiveName,$ArchiveName+'.json',$ArchiveName+'.sha256','install.ps1')) { Download-Asset $Release $Asset $Operation }
        $Archive=Join-Path $Operation $ArchiveName
        $ReleaseReceipt=Get-Content -LiteralPath ($Archive+'.json') -Raw | ConvertFrom-Json
        Assert-Fields $ReleaseReceipt @('schema_version','component','repository','version','source_commit','archive_sha256','members') 'release receipt'
        Assert-True ($ReleaseReceipt.schema_version -eq 1 -and $ReleaseReceipt.component -ceq $Component -and $ReleaseReceipt.repository -ceq $Repository -and $ReleaseReceipt.version -ceq $Version -and $ReleaseReceipt.source_commit -ceq $SourceCommit -and $ReleaseReceipt.archive_sha256 -cmatch '^[0-9a-f]{64}$') 'Release trust binding mismatch.'
        $ArchiveSha=Get-Sha $Archive
        Assert-True ($ArchiveSha -ceq $ReleaseReceipt.archive_sha256) 'Release archive checksum mismatch.'
        $Checksum=(Get-Content -LiteralPath ($Archive+'.sha256') -Raw).Trim()
        Assert-True ($Checksum -ceq ($ArchiveSha+'  '+$ArchiveName)) 'Published checksum does not match exact archive.'
        Assert-True ((Get-Sha (Join-Path $Operation 'install.ps1')) -ceq (Get-Sha $PSCommandPath)) 'Executing frontdoor differs from selected release. Download its install.ps1; no self-replacement occurs.'
        Add-Type -AssemblyName System.IO.Compression.FileSystem
        $Zip=[IO.Compression.ZipFile]::OpenRead($Archive)
        try {
            $Names=New-Object 'Collections.Generic.HashSet[string]' ([StringComparer]::OrdinalIgnoreCase)
            $Contents=@{}
            foreach($Entry in $Zip.Entries) {
                Assert-Member $Entry.FullName
                Assert-True ($Names.Add($Entry.FullName)) 'Duplicate/case-colliding ZIP member.'
                $Kind=($Entry.ExternalAttributes -shr 16) -band 61440
                Assert-True ($Kind -eq 0 -or $Kind -eq 32768) 'ZIP links/special entries are refused.'
                $Stream=$Entry.Open();$Memory=New-Object IO.MemoryStream
                try { $Stream.CopyTo($Memory);$Contents[$Entry.FullName]=$Memory.ToArray() } finally { $Stream.Dispose();$Memory.Dispose() }
            }
            Assert-True ($Contents.ContainsKey('manifest.json')) 'Package manifest missing.'
            $Manifest=([Text.Encoding]::UTF8.GetString($Contents['manifest.json'])) | ConvertFrom-Json
            Assert-Fields $Manifest @('schema_version','component','repository','version','source_commit','files') 'manifest'
            Assert-True ($Manifest.schema_version -eq 1 -and $Manifest.component -ceq $Component -and $Manifest.repository -ceq $Repository -and $Manifest.version -ceq $Version -and $Manifest.source_commit -ceq $SourceCommit) 'Manifest release/OID mismatch.'
            $Expected=New-Object 'Collections.Generic.HashSet[string]' ([StringComparer]::OrdinalIgnoreCase)
            [void]$Expected.Add('manifest.json')
            foreach($Row in $Manifest.files) {
                Assert-Fields $Row @('path','sha256','mode') 'manifest file'
                Assert-Member $Row.path
                Assert-True ($Expected.Add($Row.path) -and $Row.sha256 -cmatch '^[0-9a-f]{64}$' -and $Row.mode -cin @('100644','100755') -and $Contents.ContainsKey($Row.path)) 'Invalid/incomplete manifest member.'
                Assert-True ((Get-BytesSha $Contents[$Row.path]) -ceq $Row.sha256) ('Package file digest mismatch: '+$Row.path)
                $ArchiveEntry=@($Zip.Entries | Where-Object { $_.FullName -ceq $Row.path })
                Assert-True ($ArchiveEntry.Count -eq 1 -and (((($ArchiveEntry[0].ExternalAttributes -shr 16) -band 73) -ne 0) -eq ($Row.mode -ceq '100755'))) 'Archive source mode mismatch.'
            }
            Assert-True ($Expected.SetEquals($Names)) 'Extra or missing archive members.'
            $Bound=New-Object 'Collections.Generic.HashSet[string]' ([StringComparer]::Ordinal)
            foreach($Member in $ReleaseReceipt.members) { Assert-Member $Member;Assert-True ($Bound.Add($Member)) 'Duplicate release member binding.' }
            Assert-True ($Bound.SetEquals([string[]]@($Zip.Entries | ForEach-Object {$_.FullName}))) 'Exact normalized ZIP membership is not release-bound.'
            Assert-True ((Get-BytesSha $Contents['install.ps1']) -ceq (Get-Sha $PSCommandPath)) 'Committed archive frontdoor mismatch.'
            foreach($Required in @('deploy/install.py','deploy/exports.json','deploy/retired.json','install.ps1')) { Assert-True ($Contents.ContainsKey($Required)) 'Incomplete backend closure.' }
            $Projection=@{}
            foreach($Required in @('deploy/install.py','deploy/exports.json','deploy/retired.json','install.ps1')) { $Projection[$Required]=$Required }
            foreach($Legal in @('LICENSE','NOTICE')) { if ($Committed.ContainsKey($Legal)) { $Projection[$Legal]=$Legal } }
            $Exports=([Text.Encoding]::UTF8.GetString($Contents['deploy/exports.json'])) | ConvertFrom-Json
            Assert-Fields $Exports @('schema_version','component','entries') 'exports'
            Assert-True ($Exports.schema_version -eq 1) 'Unsupported export declaration.'
            foreach($Export in $Exports.entries) {
                Assert-Fields $Export @('source','destination') 'export entry'
                Assert-Member $Export.source;Assert-Member $Export.destination
                Assert-True ($Export.destination.StartsWith('harness/.',[StringComparison]::Ordinal)) 'Invalid export destination.'
                $Member='payload/'+$Export.destination.Substring(8)
                Assert-True (-not $Projection.ContainsKey($Member)) 'Duplicate export destination.'
                $Projection[$Member]=$Export.source
            }
            $ExactProjection=New-Object 'Collections.Generic.HashSet[string]' ([StringComparer]::Ordinal)
            [void]$ExactProjection.Add('manifest.json')
            foreach($Member in $Projection.Keys) {
                [void]$ExactProjection.Add($Member)
                $Source=$Projection[$Member]
                Assert-True ($Committed.ContainsKey($Source) -and $Contents.ContainsKey($Member)) 'Committed export source/member is missing.'
                $SourceRow=$Committed[$Source]
                Assert-True ($SourceRow.type -ceq 'blob' -and $SourceRow.mode -cin @('100644','100755')) 'Committed export source is not a regular file.'
                Assert-True ((Get-GitBlobSha $Contents[$Member]) -ceq $SourceRow.sha) ('Archive bytes differ from selected committed source: '+$Member)
                $ManifestRow=@($Manifest.files | Where-Object { $_.path -ceq $Member })
                Assert-True ($ManifestRow.Count -eq 1 -and $ManifestRow[0].mode -ceq $SourceRow.mode) 'Source/manifest mode mismatch.'
            }
            Assert-True ($ExactProjection.SetEquals([string[]]@($Zip.Entries | ForEach-Object {$_.FullName}))) 'Archive contains extra/incomplete executable or runtime members beyond the explicit committed exports.'
            $Payload=Join-Path $Operation 'payload'
            [IO.Directory]::CreateDirectory($Payload) | Out-Null
            foreach($Name in $Contents.Keys) {
                $Destination=Join-Path $Payload $Name
                [IO.Directory]::CreateDirectory([IO.Path]::GetDirectoryName($Destination)) | Out-Null
                [IO.File]::WriteAllBytes($Destination,$Contents[$Name])
            }
        } finally { $Zip.Dispose() }
        $Backend=Join-Path $Payload 'deploy/install.py'
        $Arguments+=@($Backend,'--payload-root',$Payload,'--archive',$Archive,'--archive-sha256',$ArchiveSha,'--source-commit',$SourceCommit,'--frontdoor',$PSCommandPath)
    }
    if ($Mode -eq 'Verify') { $Arguments+=@($Backend) }
    $Arguments+=@('--mode',$Mode,'--version',$Version,'--work-root',$WorkRoot,'--user-root',$UserRoot,'--python-exe',$PythonExe)
    if ($RuntimeConfigDir) { $Arguments+=@('--runtime-config-dir',$RuntimeConfigDir) }
    foreach($Item in $Harness) { $Arguments+=@('--harness',$Item) }
    foreach($Item in $Skills) { $Arguments+=@('--skills',$Item) }
    if ($AdoptExisting) { $Arguments+='--adopt-existing' }
    & $PythonExe @Arguments
    if ($LASTEXITCODE -ne 0) { throw ('Local '+$Component+' '+$Mode+' failed; retained WorkRoot diagnostics/backups were not purged.') }
} catch {
    Write-Error $_
    exit 1
}
