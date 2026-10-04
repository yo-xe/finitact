# BUG-0013: warm_up_native_extractor_runtime()がPillowのネイティブ初回loadを警戒しておらずEdgeRectangleRegionExtractor.extract()内でhangする

- 報告日: 2026-09-23 / 状態: 修正済み
- 重大度: 高 / 発生コミット: cd4c5ea

## 症状

- (未記入)

## 再現手順

scripts/check_windows_mcp_screen_success_live.pyをgalleria実機でlive実行。BUG-0010のNumPy warm-up
(`warm_up_native_extractor_runtime`)のみでは、EdgeRectangleRegionExtractor.extract()内で
`edge-extract-start`log後に一切のtrace進行がなくなり、45秒client timeoutまでhangした。
faulthandler.dump_traceback_laterで採取したthread stackはnumpy/_core/multiarray.py初回import中の
`<module>`フレームで固定されていた。ただしこのtraceは、venvの`_editable_impl_finitact.pth`が
Sep22 23:29断面(BUG-0010修正より前)のcheckoutを指す配置ミスの下で採取したものであり、warm-upの
効果自体がそもそも当該runへ反映されていなかった疑いが残る(原因を確定丸ごとPillowに帰属できない)。
配置を修正しwarm-up本体へ`Image.frombuffer(...).convert('L')`(EdgeRectangleRegionExtractorの実呼び出しと
同型)を追加した後は、同じ実行で`edge-extract-end`まで到達しhangが再発しなかった。

## 該当箇所

- `finitact/stage1_extractors.py`の`warm_up_native_extractor_runtime()`

## 原因

- 特定できず。可能性: (1) Pillowの`_imaging`ネイティブ拡張の初回loadがBUG-0010と同型の
  transport/threading層競合を起こす、(2) 配置ミスによりwarm-up自体が無効だっただけ。
  今回の修正はPillow初期化を先に済ませることで問題を回避したが、機序の切り分けは未完了。

## 修正

- 2026-09-23 修正済み(コミット cd4c5ea): warm_up_native_extractor_runtime()にImage.frombuffer(...).convert('L')の呼び出しを追加(EdgeRectangleRegionExtractor.extract()と同型)。配置ミス(古いcheckoutを指すeditable install)を修正した上でgalleria実機live再実行し、edge-extract-endまでhangなく到達することを確認。機序(Pillow初回load競合 vs 配置ミスの影響)は未確定だが再発しない
