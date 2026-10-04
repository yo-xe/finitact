# BUG-0016: Unity dropdown probeの後始末EscapeでUnityPopupWindowが閉じず、dropdownが開いたまま残る

- 報告日: 2026-09-23 / 状態: 修正済み
- 重大度: 中 / 発生コミット: 3fa8dc1

## 症状

- (未記入)

## 再現手順

check_windows_mcp_unity_dropdown_live.py をSampleLab(HWND 920288)へ実行→終了後もUnityPopupWndClassが可視で残る。popupへforegroundしてEscape(wScan有無とも)でも閉じず、root windowへSetForegroundWindowすると閉じる

## 該当箇所

- (未調査。当たりを付けるには dev map を使う)

## 原因

- (未調査)

## 修正

- 2026-09-23 修正済み(コミット 3fa8dc1): Unity PopupWindowはsynthetic Escape(wScan有無とも)を無視しfocus喪失で閉じる。probeの後始末をroot再foreground+UnityEditor.PopupWindow消滅の確認に替え、独立oracleのsnapshotを後始末前へ移した。live 2回目でcleanup_popup_closed=true
