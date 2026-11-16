# Open a workbook in Excel, recalculate every formula, save it and close.
# openpyxl writes formulas without their results; this stores the results so the file can be checked and previewed.
#   powershell -ExecutionPolicy Bypass -File tools\recalc_excel.ps1 -Path output\weekly.xlsx
param([Parameter(Mandatory = $true)][string]$Path)

$full = (Resolve-Path $Path).Path
$excel = New-Object -ComObject Excel.Application
$excel.Visible = $false
$excel.DisplayAlerts = $false
try {
    $wb = $excel.Workbooks.Open($full)
    $excel.CalculateFull()
    $wb.Save()
    $wb.Close($false)
    Write-Output "recalculated: $full"
}
finally {
    $excel.Quit()
    [System.Runtime.InteropServices.Marshal]::ReleaseComObject($excel) | Out-Null
}
