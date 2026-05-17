; LlamaGrid Peer Installer — NSIS script
; Usage: makensis /DVERSION=0.1.0 build\peer_installer.nsi

!ifndef VERSION
  !define VERSION "0.1.0"
!endif

Name "LlamaGrid Peer ${VERSION}"
OutFile "..\build\output\llamagrid-peer-${VERSION}-setup.exe"
InstallDir "$PROGRAMFILES64\LlamaGrid"
InstallDirRegKey HKLM "Software\LlamaGrid" "PeerInstallDir"
RequestExecutionLevel admin
SetCompressor /SOLID lzma

!include "MUI2.nsh"

!define MUI_ABORTWARNING
!insertmacro MUI_PAGE_WELCOME
!insertmacro MUI_PAGE_DIRECTORY
!insertmacro MUI_PAGE_INSTFILES
!define MUI_FINISHPAGE_RUN "$INSTDIR\llamagrid-peer.exe"
!define MUI_FINISHPAGE_RUN_TEXT "Run LlamaGrid Peer Setup now"
!insertmacro MUI_PAGE_FINISH
!insertmacro MUI_UNPAGE_CONFIRM
!insertmacro MUI_UNPAGE_INSTFILES
!insertmacro MUI_LANGUAGE "English"

Section "Main" SecMain
  SetOutPath "$INSTDIR"

  File "..\build\dist\peer\llamagrid-peer.exe"

  ; Create data directories
  CreateDirectory "C:\LlamaGrid"
  CreateDirectory "C:\LlamaGrid\logs"
  CreateDirectory "C:\LlamaGrid\rpc"
  CreateDirectory "C:\LlamaGrid\cache"

  ; Registry
  WriteRegStr HKLM "Software\LlamaGrid" "PeerInstallDir" "$INSTDIR"
  WriteRegStr HKLM "Software\LlamaGrid" "PeerVersion" "${VERSION}"

  ; Start Menu
  CreateDirectory "$SMPROGRAMS\LlamaGrid"
  CreateShortcut "$SMPROGRAMS\LlamaGrid\LlamaGrid Peer.lnk" "$INSTDIR\llamagrid-peer.exe" "" "$INSTDIR\llamagrid-peer.exe"

  ; Uninstaller
  WriteUninstaller "$INSTDIR\uninstall-peer.exe"
  WriteRegStr HKLM "Software\Microsoft\Windows\CurrentVersion\Uninstall\LlamaGrid-Peer" "DisplayName" "LlamaGrid Peer"
  WriteRegStr HKLM "Software\Microsoft\Windows\CurrentVersion\Uninstall\LlamaGrid-Peer" "UninstallString" '"$INSTDIR\uninstall-peer.exe"'
  WriteRegStr HKLM "Software\Microsoft\Windows\CurrentVersion\Uninstall\LlamaGrid-Peer" "DisplayVersion" "${VERSION}"
SectionEnd

Section "Uninstall"
  ; Stop and remove services
  ExecWait 'sc stop LlamaGridRPC'
  ExecWait 'sc stop LlamaGridAgent'
  ExecWait '"C:\LlamaGrid\nssm.exe" remove LlamaGridRPC confirm'
  ExecWait '"C:\LlamaGrid\nssm.exe" remove LlamaGridAgent confirm'

  ; Remove firewall rule
  ExecWait 'netsh advfirewall firewall delete rule name="LlamaGrid-RPC"'

  ; Remove files
  Delete "$INSTDIR\llamagrid-peer.exe"
  Delete "$INSTDIR\uninstall-peer.exe"
  RMDir /r "$INSTDIR"
  RMDir /r "C:\LlamaGrid"
  Delete "$SMPROGRAMS\LlamaGrid\LlamaGrid Peer.lnk"
  RMDir "$SMPROGRAMS\LlamaGrid"
  DeleteRegKey HKLM "Software\Microsoft\Windows\CurrentVersion\Uninstall\LlamaGrid-Peer"
SectionEnd
