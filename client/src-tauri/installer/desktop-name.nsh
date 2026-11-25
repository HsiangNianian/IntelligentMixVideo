; Explorer 从已安装程序的字符串资源读取桌面名称，NSIS 仍管理同一个快捷方式和卸载路径。
!macro NSIS_HOOK_POSTINSTALL
  WriteINIStr "$DESKTOP\desktop.ini" "LocalizedFileNames" "${PRODUCTNAME}.lnk" "@$INSTDIR\${MAINBINARYNAME}.exe,-26001;v${VERSION}"
  SetFileAttributes "$DESKTOP\desktop.ini" HIDDEN|SYSTEM
!macroend

; 只移除本安装写入的名称，保留桌面上其他应用的配置。
!macro NSIS_HOOK_POSTUNINSTALL
  ReadINIStr $0 "$DESKTOP\desktop.ini" "LocalizedFileNames" "${PRODUCTNAME}.lnk"
  StrCmp $0 "@$INSTDIR\${MAINBINARYNAME}.exe,-26001;v${VERSION}" 0 +2
  DeleteINIStr "$DESKTOP\desktop.ini" "LocalizedFileNames" "${PRODUCTNAME}.lnk"
!macroend
