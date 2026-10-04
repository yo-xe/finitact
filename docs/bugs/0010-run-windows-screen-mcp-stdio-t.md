# BUG-0010: run_windows(screen:...)がMCP stdio transport配下で候補抽出中にhangし、client-side timeout(20s)を超える

- 報告日: 2026-09-23 / 状態: 修正済み
- 重大度: 高 / 発生コミット: 181227b

## 症状

- Windows-native MCP stdio経由の`run_windows(screen:...)`がEdge extractorの初回NumPy load中に
  停止し、clientの20秒timeoutまで応答しない。

## 再現手順

Windows-nativeでMCP serverをstdio起動しrun_windows(screen:...)をclient経由で呼ぶ(scripts/check_windows_mcp_mutex_live.py)。factory/capture/extract-startまでlog成立(capture: 260x160を0.3-0.6sで取得)。CompositeRegionExtractorが2番目のextractor(EdgeRectangleRegionExtractor)の処理へ入った直後、以後のlog呼び出しが一切発生せずclient側asyncio.wait_for(20s)がTimeoutErrorで打ち切られるまで無応答。同一コード(capture+extract、asyncio.to_thread経由、finitact.mcp_serverをimport済み)をFastMCPのstdio_server()イベントループ外で直接実行すると1秒未満で正常終了する。合成frame・trivial numpy呼び出し・numpyの事前import(main thread)・trace書き込み方式(都度open→バッファ化)のいずれに置き換えても同じ箇所で再現し、mutex競合・capture・extractorのアルゴリズム自体が原因ではないことを切り分け済み。

## 該当箇所

- `finitact/mcp_server.py`のstdio開始と`finitact/stage1_extractors.py`のNumPy native core初回loadの順序。

## 原因

- Windows MCP stdio reader稼働後にNumPy native coreを初回loadすると、thread dispatch方式によらず
  `numpy._core.multiarray`のmodule生成で停止するloader初期化競合。importだけの事前実行では不十分で、
  Edge extractorと同型の実演算までstdio開始前に完了させる必要があった。

## 修正

- 2026-09-23修正済み: FastMCP開始前にEdge extractor同型の`asarray`/`diff`/`abs`をwarm-upするよう
  修正し、Windows-native MCP mutex競合負例を3回連続でtimeoutなし・SendInput 0回として確認した。
