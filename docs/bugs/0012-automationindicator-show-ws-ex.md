# BUG-0012: AutomationIndicator.show()がWS_EX_NOACTIVATE適用後もforegroundを奪う

- 報告日: 2026-09-23 / 状態: 修正済み
- 重大度: 中 / 発生コミット: cd4c5ea

## 症状

- guard.execute()内でAutomationIndicator.show()を呼ぶとGetForegroundWindow()がindicator側へ
  移り、target_is_validのforeground一致判定がFalseになりSendInput 0回でfail-closed停止する。

## 再現手順

scripts/check_windows_mcp_screen_success_live.pyをgalleria実機でlive実行。guard.execute()内でAutomationIndicator.show()を呼んだ直後、GetForegroundWindow()がindicatorのTk root('tk', TkTopLevel)に切り替わり、target_is_validのforeground==target判定がFalseになりnot_attempted/SendInput 0回でfail-closed停止する。単独の最小再現(root.overrideredirect+topmost+update後にSetWindowLongWでWS_EX_NOACTIVATEを付与)ではforeground奪取を阻止できたが、automation_indicator.py本番コード(_apply_noactivate_styleをroot.update()後に適用)へ同じ順序で組み込んでも実パイプライン内では再現し続けた。原因未特定

## 該当箇所

- (未調査。当たりを付けるには dev map を使う)

## 原因

- Tk on Windowsでは`winfo_id()`が内側の子HWNDを返し、styleはmap後にしか付けられない。
  このため`WS_EX_NOACTIVATE`の付け先(top-level HWNDでない)と時機(表示の瞬間に活性化済み)が不確か。
  live未確認で、両方が寄与している可能性がある。

## 修正

- Tkを捨て、生成時にNOACTIVATEを指定するWin32 layered overlayで作り直す(ADR-0010)。
- 2026-09-23 修正済み(コミット c397e4e): ADR-0010実装(Win32 layered overlay+WS_EX_NOACTIVATE at CreateWindowExW time)。galleria実機live確認: badge/reticle/delivering中もGetForegroundWindow()は対象windowのまま。scripts/check_windows_mcp_screen_success_live.pyの1click成功caseがpassed=trueで完走(target-valid-foreground/hit-rootとも対象windowを維持、click confirmed、independent oracle popup_confirmed=true)。付随して発見: indicator_theme.pyのrender_reticleでcorner頂点位置からcorner_lengthが抜けており左上以外の3隅が描画されない幾何バグがあった(実機pixel-sampleで発見、修正済み、regression test追加)。
- test-ref: `tests/test_indicator_theme.py::test_render_reticle_draws_all_four_corners_not_just_one`(付随geometryバグの症状名テスト)。
