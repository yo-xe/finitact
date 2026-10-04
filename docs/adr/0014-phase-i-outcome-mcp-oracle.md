# ADR-0014: Phase Iのoutcomeは公開MCPを変えず外部oracleで採点する

- 日付: 2026-09-23
- 状態: 採択
- 決定者: yo-xe + Claude Code

## 背景

Phase Hでverifier(`WindowsOwnedWindowVerifier`)はprobeがserverへ注入する形でlive判定できたが、
公開MCP server(`mcp_server.py`)はverifierを持たず、outcomeは常に`unverified`を返す。Phase Iで
Finitactとwindows-mcpを比較する前に、outcomeを誰が判定するかを決める必要があった(計画Phase H項目7)。
反証相談: `docs/consult/20260923-2307-consult-verifier-placement.md`。

## 決定

1. (A) Phase Iは現在の公開構成を比較対象にし、双方を共通の外部oracle(評価runner)で採点する。
   評価専用のverifier注入でFinitactの停止挙動を変えない。
2. 公開MCPの結果(`unverified`等)は書き換えない。`termination_reason`・MCPの`outcome`・外部のcase判定を
   別項目として記録し、run ID・target・baselineで対応付ける。
3. 外部で成功を検出してもrunnerから途中cancelしない。verifier無しで生じる追加provider call・
   `budget`/`provider_blocked`終了は置換評価の差として残す。verifier注入probeの時間・call数を
   公開serverの性能証拠へ流用しない。
4. baselineはrun直前に1つ取り、登録済みinitial_state(popup無し・目的値でない等)を確認して証拠に残す。
   不成立・target不整合は成功へ算入せず判定不能とする。
5. 2操作caseは前後snapshotだけでは途中popupの出現・消滅を示せないため、baselineからrun終了まで
   pollingで遷移を記録する。捕捉できなければその部分は証拠不足とし、目的値から補完しない。
6. (B) serverへのverifier注入は棄却せず保留する。価値は目的達成直後の停止であり、Phase Iで観測した
   追加call・停止の問題を踏まえて利用者向け機能として別途判断する。

## 検討した代替案(没案)

- **(B) Phase Iでserverへ評価用verifierを注入する**: 片方だけへ正解述語を与え、通常の公開構成と
  異なる条件を比較することになる。「outer agentが述語を書くので自己申告」という理由では退けない
  (述語を要求値へ固定し実値を照合すれば独立性はある)。保留であり没ではない。
- **run前後のsnapshot差分だけで判定する**: popupが開いて閉じるcaseは前後とも「popup無し」で、
  途中遷移を証明できない。
- **`unverified`を理由に置換不可とする**: 計画は範囲限定の置換判断であり、公開MCPのoutcome欄だけで
  失格にしない。

## 影響

- Phase Iの評価runnerは外部oracle(対象固有state・Win32 enumeration)とpollingを持つ。公開serverの
  コードは変えない。
- 外部baseline取得とgoal開始の隙間で対象アプリ自身が変化する競合は残る(Bでも`begin_goal`以後の
  非同期変化は排除しない)。initial_state確認と遷移の時刻記録で限界を評価に明記する。

## 追記1: 2026-09-30 電卓の表示だけUIAで読む

電卓表示のOCR読み取りは、キー入力後に付くフォーカス枠で両engineとも表示を落とし(BUG-0060)、windows-mcpの
キー入力runだけが判定不能になって分母が入力経路で偏った。表示部分の切り出しはcrop位置で読みが揺れて没。
`CalculatorResults`のUIA Nameを、FinitactのUIA readerと別の単一property問い合わせで読む。oracleはUIA providerを
共有するが、表示値はアプリ自身の状態であり、system側の候補化・判定の誤りは採点へ流れない。

<!-- 現況 -->
2026-09-24: 採択。評価runner実装済み(`finitact/evaluation_runner.py`)、Tk 2操作でlive確認。
<!-- /現況 -->
