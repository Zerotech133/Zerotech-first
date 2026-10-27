param([Parameter(Mandatory=$true)][string]$Wav)
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Speech
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$culture = [System.Globalization.CultureInfo]::GetCultureInfo('zh-CN')
$rec = New-Object System.Speech.Recognition.SpeechRecognitionEngine($culture)
$rec.LoadGrammar((New-Object System.Speech.Recognition.DictationGrammar))
$rec.InitialSilenceTimeout = [TimeSpan]::FromSeconds(2)
$rec.EndSilenceTimeout = [TimeSpan]::FromSeconds(2)
$rec.BabbleTimeout = [TimeSpan]::FromSeconds(2)
$rec.SetInputToWaveFile($Wav)
$result = $rec.Recognize()
if ($result) {
    Write-Output $result.Text
}
