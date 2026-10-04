# BUG-0030: Phase I Tk scroll oracleがstate JSON更新中の空ファイルを読みJSONDecodeErrorで停止する

- 報告日: 2026-09-25 / 状態: 修正済み
- 重大度: 中 / 発生コミット: 5c06dfd

## 症状

- live比較のbaseline取得が`JSONDecodeError: Expecting value`で停止し、試行を開始できない。

## 再現手順

Windows-nativeで run_phase_i_comparison.py finitact --case screen-tk-scroll-select-001 を起動し、Tk state書換えとoracle.snapshotが重なる

## 該当箇所

- `finitact/windows_evaluation_oracles.py`の`target_state` reader。
- `scripts/check_windows_mcp_screen_scroll_live.py`のTk targetが100msごとにstate JSONを上書きする。

## 原因

- readerがwriterのtruncate後・write前に開くと、freshだが空のファイルを1回だけ読んでいた。

## 修正

- 2026-09-25 修正済み(コミット 5c06dfd): target_state readerがJSON更新中の一時的なJSONDecodeError/OSErrorを短時間再試行し、読取後にfreshnessとPIDを従来どおり検証するよう修正。race再現testを追加。
