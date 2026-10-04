# BUG-0059: 無人時(ユーザー離席後)にPhase I runnerのscreen case準備が 'could not bring target window to foreground within 5.0s' で全件失敗し、Unityは 'Display 1' labelを0件と判定する。直前(在席時)のBlenderはUIを描画せず灰色

- 報告日: 2026-09-30 / 状態: 修正済み
- 重大度: 中 / 発生コミット: 91c3d52

## 症状

- screen 6 case(VS Code・Tk×2 × 2系)は準備の`_force_foreground`で`set_ok=False`(show/topはTrue)、5秒待っても前面化せず即失敗。
  foreground lock timeoutを0にするbypass下でも失敗。同夜のUIA case(電卓・メモ帳)は前面化を要しても通った。
- Unity 4 caseはUnity起動(01:10:30)から約100秒後に開始しており、Game view読込前だった可能性が高い(前面化とは別原因の疑い)。

## 再現手順

2026-09-30 01:03〜01:12、scripts/run_phase_i_comparison.py の screen-vscode/tk/unity case。ログ: scratchpad c1_night.log(要点はSTATUS)。list/screenshotではロック無し・前面窓なし

## 該当箇所

- (未調査。当たりを付けるには dev map を使う)

## 原因

- 前面化: 無人とは無関係。2026-09-30朝の在席時も同じ失敗を再現した。WSL interop経由で起動したprocessは直近の実入力を持つ端末に負け、
  評価用`_force_foreground`にはlock解除しかなかった。本番`_front`と同じ`AttachThreadInput`・zero move fallback(BUG-0040・0052)を足し、
  smokeで`set_ok=True attached=True`・success。Windows側root直下の古い`check_windows_mcp_screen_success_live.py`が
  `sys.path`先頭で`scripts/`版を隠していたため`.stale-20260930`へ改名した。
- Unity: 起動後`Display 1`出現を確かめてから開始して解消を確かめる。Blenderの灰色描画は未調査。yo-xeが2026-09-30にデスクトップアイコンから起動した通常のBlenderは正常に描画した(評価scaffoldの起動経路・引数・起動直後の開始時刻を疑う)。

## 修正

- 2026-09-30 修正済み(コミット ac51cdd): 前面化: 評価用_force_foregroundへ本番と同じAttachThreadInput・zero move fallbackを追加(f262aa2)。Unity: Display 1出現後に開始。Blender灰色描画はscaffold経路(pyt.bat・--factory-startup)で2〜20秒撮影して再現せず一過性と判断、C1 Blender 2 case×両系N=10で40/40成功(c1-20260930e)
