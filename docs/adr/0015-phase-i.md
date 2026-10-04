# ADR-0015: Phase I比較の実行条件

- 日付: 2026-09-23
- 状態: 採択
- 決定者: yo-xe + Claude Code

## 背景

Phase I(計画`docs/plans/windows-mcp-replacement.md`)はFinitactとwindows-mcpを同一case setで比較し、
操作範囲ごとに置換可・限定可・不可・判定不能を出す。着手前に比較の駆動方法、provider、対象アプリ、
試行回数、windows-mcp側の安全策、Stage1のOCRエンジンを決める必要があった。outcome判定はADR-0014。

## 決定

1. OCR: Phase I前にWindows標準OCR(Windows.Media.Ocr)のextractorを追加し、Phase Gの較正で
   tesseractと比較してから採用を決める。tesseract由来の問題のうち導入(PATH、K-4a09e29bd7c9)・
   言語(`eng`のみ)・候補idの非局所性はエンジン選択に起因し、BUG-0018型の修正では消えないため。
2. 駆動: 双方とも外側agent(`claude -p` headless)へ同じgoal文を渡し、一方はFinitact MCPのみ、
   他方はwindows-mcpのみを使わせる。外側agentのtokenは同条件で測り、Finitact内部providerの
   tokenは別集計にする。
3. provider: Finitactは公開serverの既定(TypeSafe)で動かす。Windows側に`TYPESAFE_API_KEY`が要る。
4. 対象: UIAはNotepad+電卓、screen-groundedはUnity+Blender。
5. Unityは起動中の既存project(sample-unity-project)へattachしてよい。manifestの「runごとに新規作成した使い捨て
   target」規則は外し、HWND/PIDをrun直前に解決する部分だけ残す。初期状態(dropdown閉・目的値でない)は
   ADR-0014どおりrun直前に確認する。
6. 試行: case×システムごとに5回。5/5成功で可、1〜4で限定可、0で不可、外部証拠欠落のrunは判定不能として
   分母から除き件数を併記する。
7. 安全: windows-mcpはlease・indicator・scope制限を持たないため、galleriaで事前告知し、yo-xe不在時に
   使い捨て/評価用targetだけで実行する。

## 検討した代替案(没案)

- **tesseractのままPhase I**: 日本語UIや候補id揺れが置換評価の結果へ混入し、Finitactの設計差と
  OCRエンジン差を分離できない。
- **windows-mcpをscriptで決め打ち駆動**: 判断主体が無くtoken・判断の比較にならない。
- **Ollama provider**: 公開構成と異なり、ADR-0014の「公開構成で比較」に反する。
- **Unity projectを毎run新規作成/template複製**: yo-xeが規則の価値を低いと判断。attachで生じる差は
  初期状態確認で吸収する。
- **使い捨てTkをscreen 2アプリ目に**: 合成UIで置換判断の根拠として弱い。
- **3回試行**: 限定可と可の区別が粗い。
- **VM/別sessionへ隔離**: 準備費用に対し、事前告知+不在時実行の前例(Phase F〜H)で足りる。

## 影響

- `case-manifest.json`: `rules.target_binding`を改訂(`target_binding_revision`)。電卓・Blenderのcaseを追加する。
- Phase I前の作業: Windows OCR extractorの実装と較正比較。

## 追記1(2026-09-23)

決定1の比較対象と基準を広げる(yo-xe指示)。Windows OCRの試作は続けるが、最終採否は手軽さでなく
品質(位置recall・label正確性・id安定性・速度)と展開性(OS可搬性・言語追加・配布・license)で決め、
tesseractとWindows OCRに加えPP-OCRv5(ONNX Runtime)を較正に載せる。比較表と実測は
`docs/evaluations/windows-mcp-replacement/ocr-engine-comparison.md`。

<!-- 現況 -->
2026-09-24: 採択(yo-xeが1〜4・6・7は推奨案、5はsample-unity-project attachを選択)。決定1はADR-0016で確定(PP-OCRv6 medium+OpenVINO)。
<!-- /現況 -->
