# BUG-0047: Start/検索を開いた後、list_windowsからtaskbar(Shell_TrayWnd)が消え、検索popup(SearchHostのCoreWindow)も出ない。E2E-10でFinitactが設定を開けず0/3

- 報告日: 2026-09-29 / 状態: 修正済み
- 重大度: 高 / 発生コミット: 39c4fdb

## 症状

- (未記入)

## 再現手順

E2E-10 Finitact: taskbarでStartをclick→以降list_windowsにShell_TrayWndが無い(試行をまたいで継続)。Windows側probe(scratch/tray_probe2.py)でEnumWindowsはShell_TrayWnd・検索・スタートを列挙せず、FindWindowExW(NULL,prev)なら届く。DWMWA_CLOAKED(14)で除外すると閉じた検索/スタート/入力エクスペリエンスも消え一覧が正しくなる。対処案: windows_inventory.list_windowsをFindWindowEx走査+cloak除外へ。refusal detail(windows_screen_grounded._refusal)がhwndしか出さず外側がpidを推測してHWND不一致になるのでtarget_id形式も出す

## 該当箇所

- (未調査。当たりを付けるには dev map を使う)

## 原因

- (未調査)

## 修正

- 2026-09-29 修正済み(コミット 4f0a5b2): EnumWindowsはStart/検索を開いた後Shell_TrayWndと検索popupを返さない。list_windowsをFindWindowEx走査+DWMWA_CLOAKED除外にし、拒否理由のwindow名をwindow:<HWND>:<PID>形式にした(4f0a5b2)。実機でStart開閉前後ともtaskbarが残ることを確認。E2E-10は残る阻害要因BUG-0048〜0051で0/3
