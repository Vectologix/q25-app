param(
    [Parameter(Mandatory=$true)][string]$SessionId,
    [Parameter(Mandatory=$true)][string]$TempDirectory
)
$ErrorActionPreference = 'Stop'
$WarningPreference = 'SilentlyContinue'
$ProgressPreference = 'SilentlyContinue'
Set-StrictMode -Version Latest
[Console]::InputEncoding = New-Object System.Text.UTF8Encoding($false)
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)
$owned = @{}
$nativeReady = $false
$exitCode = 0

function Send-Reply([hashtable]$Reply) {
    [Console]::WriteLine((ConvertTo-Json -InputObject $Reply -Compress))
    [Console]::Out.Flush()
}
function Read-BoundedLine {
    $line = New-Object System.Text.StringBuilder
    while ($true) {
        $value = [Console]::Read()
        if ($value -eq -1) { return $null }
        if ($value -eq 10) { return $line.ToString() }
        if ($line.Length -ge 1024) { throw 'protocol' }
        if ($value -ne 13) { [void]$line.Append([char]$value) }
    }
}
function Target([string]$Id) { return ('Q25-TEST-2B2/' + $SessionId + '/' + $Id) }
function Clean-Owned {
    $failures = 0
    foreach ($id in @($owned.Keys)) {
        try {
            [Q25NativeCredential]::Delete((Target $id))
            $owned.Remove($id)
        } catch { $failures++ }
    }
    return ($failures -eq 0)
}

try {
    if ($SessionId -cnotmatch '^[0-9a-f]{32}$' -or
        -not [System.IO.Directory]::Exists($TempDirectory)) { throw 'configuration' }
    # Refuse enforced PowerShell recording rather than expose key IPC to it.
    foreach ($hive in @('HKLM:', 'HKCU:')) {
        foreach ($policy in @(
            @('Transcription','EnableTranscripting'),
            @('ModuleLogging','EnableModuleLogging'),
            @('ScriptBlockLogging','EnableScriptBlockLogging'),
            @('ScriptBlockLogging','EnableScriptBlockInvocationLogging')
        )) {
            $location = $hive + '\Software\Policies\Microsoft\Windows\PowerShell\' + $policy[0]
            if (Test-Path -LiteralPath $location) {
                $value = Get-ItemProperty -LiteralPath $location -ErrorAction Stop
                if ($null -ne $value -and $value.PSObject.Properties[$policy[1]] -and
                    $value.PSObject.Properties[$policy[1]].Value -eq 1) { throw 'recording' }
            }
        }
    }
    # Compilation scratch stays in the owned synthetic workspace, not Windows TEMP.
    $env:TEMP = $TempDirectory
    $env:TMP = $TempDirectory
    Set-Location -LiteralPath $TempDirectory
    Add-Type -TypeDefinition @'
using System;
using System.Runtime.InteropServices;
using System.Security.Cryptography;

[assembly: DefaultDllImportSearchPaths(DllImportSearchPath.System32)]

public sealed class Q25VaultException : Exception {
    public readonly string Code;
    public Q25VaultException(string code) : base(code) { Code = code; }
}
public static class Q25NativeCredential {
    [StructLayout(LayoutKind.Sequential, CharSet = CharSet.Unicode)]
    private struct Credential {
        public uint Flags;
        public uint Type;
        [MarshalAs(UnmanagedType.LPWStr)] public string TargetName;
        [MarshalAs(UnmanagedType.LPWStr)] public string Comment;
        public System.Runtime.InteropServices.ComTypes.FILETIME LastWritten;
        public uint CredentialBlobSize;
        public IntPtr CredentialBlob;
        public uint Persist;
        public uint AttributeCount;
        public IntPtr Attributes;
        [MarshalAs(UnmanagedType.LPWStr)] public string TargetAlias;
        [MarshalAs(UnmanagedType.LPWStr)] public string UserName;
    }
    [DllImport("Advapi32.dll", EntryPoint="CredReadW", CharSet=CharSet.Unicode,
               ExactSpelling=true, SetLastError=true)]
    [return: MarshalAs(UnmanagedType.Bool)]
    private static extern bool CredRead(string target, uint type, uint flags, out IntPtr result);
    [DllImport("Advapi32.dll", EntryPoint="CredWriteW", CharSet=CharSet.Unicode,
               ExactSpelling=true, SetLastError=true)]
    [return: MarshalAs(UnmanagedType.Bool)]
    private static extern bool CredWrite(ref Credential credential, uint flags);
    [DllImport("Advapi32.dll", EntryPoint="CredDeleteW", CharSet=CharSet.Unicode,
               ExactSpelling=true, SetLastError=true)]
    [return: MarshalAs(UnmanagedType.Bool)]
    private static extern bool CredDelete(string target, uint type, uint flags);
    [DllImport("Advapi32.dll", ExactSpelling=true)]
    private static extern void CredFree(IntPtr buffer);

    private static void Clear(IntPtr buffer) {
        for (int i = 0; i < 32; i++) Marshal.WriteByte(buffer, i, 0);
    }
    public static bool Exists(string target) {
        IntPtr pointer;
        if (CredRead(target, 1, 0, out pointer)) {
            CredFree(pointer); // Presence only; do not copy an existing blob.
            return true;
        }
        if (Marshal.GetLastWin32Error() == 1168) return false;
        throw new Q25VaultException("unavailable");
    }
    public static void Create(string target) {
        if (Exists(target)) throw new Q25VaultException("exists");
        byte[] key = new byte[32];
        IntPtr blob = IntPtr.Zero;
        try {
            using (RandomNumberGenerator rng = RandomNumberGenerator.Create()) { rng.GetBytes(key); }
            blob = Marshal.AllocHGlobal(32);
            Marshal.Copy(key, 0, blob, 32);
            Credential credential = new Credential();
            credential.Type = 1; // Generic application credential.
            credential.TargetName = target;
            credential.Comment = "Q25 synthetic prototype 2B2";
            credential.UserName = "Q25-2B2-test";
            credential.CredentialBlobSize = 32;
            credential.CredentialBlob = blob;
            credential.Persist = 2; // Local machine, no enterprise roaming requested.
            if (!CredWrite(ref credential, 0)) throw new Q25VaultException("unavailable");
        } finally {
            Array.Clear(key, 0, key.Length);
            if (blob != IntPtr.Zero) { Clear(blob); Marshal.FreeHGlobal(blob); }
        }
    }
    public static string Read(string target) {
        IntPtr pointer;
        if (!CredRead(target, 1, 0, out pointer)) {
            if (Marshal.GetLastWin32Error() == 1168) throw new Q25VaultException("missing");
            throw new Q25VaultException("unavailable");
        }
        byte[] key = new byte[32];
        IntPtr blob = IntPtr.Zero;
        try {
            Credential credential = (Credential)Marshal.PtrToStructure(pointer, typeof(Credential));
            if (credential.Type != 1 || credential.Persist != 2 ||
                credential.CredentialBlobSize != 32 || credential.CredentialBlob == IntPtr.Zero ||
                credential.TargetName != target || credential.UserName != "Q25-2B2-test")
                throw new Q25VaultException("failure");
            blob = credential.CredentialBlob;
            Marshal.Copy(blob, key, 0, 32);
            return Convert.ToBase64String(key);
        } finally {
            Array.Clear(key, 0, key.Length);
            if (blob != IntPtr.Zero) Clear(blob);
            CredFree(pointer);
        }
    }
    public static void Delete(string target) {
        if (!CredDelete(target, 1, 0) && Marshal.GetLastWin32Error() != 1168)
            throw new Q25VaultException("unavailable");
        if (Exists(target)) throw new Q25VaultException("failure");
    }
}
'@
    $nativeReady = $true
    Send-Reply @{v=1; status='ok'; event='ready'}
    while ($true) {
        $line = Read-BoundedLine
        if ($null -eq $line) { break }
        try {
            $request = ConvertFrom-Json -InputObject $line
            $fields = @($request.PSObject.Properties.Name)
            if ($request.v -isnot [int] -or $request.v -ne 1 -or
                $request.op -isnot [string] -or
                $request.op -cnotin @('create','read','delete','close')) { throw 'protocol' }
            if ($request.op -ceq 'close') {
                if ($fields.Count -ne 2) { throw 'protocol' }
                if (Clean-Owned) { Send-Reply @{v=1; status='ok'; event='closed'} }
                else { Send-Reply @{v=1; status='cleanup_failed'}; $exitCode = 2 }
                break
            }
            if ($fields.Count -ne 3 -or $request.id -isnot [string] -or
                $request.id -cnotmatch '^[0-9a-f]{32}$') { throw 'protocol' }
            $id = $request.id
            $target = Target $id
            switch -CaseSensitive ($request.op) {
                'create' {
                    if ($owned.ContainsKey($id)) { throw (New-Object Q25VaultException('exists')) }
                    [Q25NativeCredential]::Create($target)
                    $owned[$id] = $true
                    Send-Reply @{v=1; status='ok'; event='created'; id=$id}
                }
                'read' {
                    if (-not $owned.ContainsKey($id)) { throw (New-Object Q25VaultException('missing')) }
                    $material = [Q25NativeCredential]::Read($target)
                    Send-Reply @{v=1; status='ok'; event='loaded'; id=$id; material=$material}
                    $material = $null
                }
                'delete' {
                    if (-not $owned.ContainsKey($id)) { throw (New-Object Q25VaultException('missing')) }
                    [Q25NativeCredential]::Delete($target)
                    $owned.Remove($id)
                    Send-Reply @{v=1; status='ok'; event='deleted'; id=$id}
                }
            }
        } catch {
            $errorObject = $_.Exception
            while ($null -ne $errorObject.InnerException) { $errorObject = $errorObject.InnerException }
            $status = 'failure'
            if ($errorObject.GetType().Name -eq 'Q25VaultException' -and
                $errorObject.Code -cin @('exists','missing','unavailable','failure')) {
                $status = $errorObject.Code
            }
            Send-Reply @{v=1; status=$status}
        }
    }
} catch {
    try { Send-Reply @{v=1; status='unavailable'} } catch {}
    $exitCode = 1
} finally {
    if ($nativeReady -and -not (Clean-Owned)) { $exitCode = 2 }
}
exit $exitCode
