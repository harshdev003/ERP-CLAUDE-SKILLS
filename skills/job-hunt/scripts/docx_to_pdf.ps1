# Export a .docx to PDF with Microsoft Word (keeps the layout exactly).
# Usage: powershell -ExecutionPolicy Bypass -File docx_to_pdf.ps1 -In <file.docx> -Out <file.pdf>
param(
    [Parameter(Mandatory = $true)][string]$In,
    [Parameter(Mandatory = $true)][string]$Out
)
$ErrorActionPreference = 'Stop'
$inPath = (Resolve-Path -LiteralPath $In).Path
$outPath = [System.IO.Path]::GetFullPath($Out)
$word = New-Object -ComObject Word.Application
$word.Visible = $false
$word.DisplayAlerts = 0
try {
    $doc = $word.Documents.Open($inPath, $false, $true)   # ConfirmConversions=false, ReadOnly=true
    $doc.ExportAsFixedFormat($outPath, 17)                # 17 = wdExportFormatPDF
    $pages = $doc.ComputeStatistics(2)                    # 2 = wdStatisticPages
    $doc.Close($false)
    Write-Output "pdf: $outPath ($pages page(s))"
}
finally {
    $word.Quit()
    [System.Runtime.InteropServices.Marshal]::ReleaseComObject($word) | Out-Null
}
