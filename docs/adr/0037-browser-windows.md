# ADR-0037: browserとWindowsの判断ループを制御骨格で共通化する

- 日付: 2026-09-25
- 状態: 採択
- 決定者: yo-xe + Claude Code

## 背景

E2Eで入れた変化なし停止(E2E-I8)・終端証拠(ADR-0035)・有限再観測(ADR-0036)・pick(ADR-0022)はWindowsループ
(`WindowsRunCoordinator`)にしか効かず、browserは旧`agent.py`ループのまま乖離が広がる(`docs/plans/unified-loop.md`)。
yo-xeは、構造(DOM/UIA)が取れない時のOCR補完も含め判断フローの共通化を採用した。consult
(`docs/consult/20260925-2218-consult-unified-loop.md`)は修正付き採用。

## 決定

- 共通化するのは制御骨格: 予算管理、判断の一回消費、再観測の回数管理、配送確実性(未配送・配送済み・配送不明)の
  区別、結果記録。
- 経路別に残すもの: 候補源(DOM/UIA、OCR補完)、鮮度・再束縛(browserのmarker・guard・一意再束縛、Windowsの
  UIA再同定・hit-test)、配送と安全門、判断要求の形とtext文脈、変化の意味(DOM意味指紋と画素は同一視しない)、
  成功証拠の取得と解釈。証拠が無ければ`unverified`のまま。
- 最初の完成形は、browserの既存契約(再束縛で選び直さない、`wait`は非mutation、provider後のcancel確認、
  goalをまたぐ予算・履歴)を保った接続。browserの低確信停止・pick・DOM達成判定・OCR補完はその後に個別に入れる。
- 判断方式の統一(browserの型付き質問とWindowsの候補直接選択)はループ移植と別の変更にし、実providerで比較する。

## 検討した代替案(没案)

- 経路差を候補源・配送・安全門の3つに限定: Jevの判断方式・text文脈・変化と証拠の意味が変わる(consult)。
- Windowsループへbrowserを直接移植: 再束縛成功を再観測へ戻し、配送前staleをuncertainにし、goal間予算が変わる。
- 共通化しない: 改善のたびに2か所へ入れ、片方へ入れ忘れる乖離が続く。

## 影響

- 移植の検証は新しい接続経路で既存の重要ケース(一意/曖昧再束縛、text生成中stale、select配送不明、配送後観測失敗、
  wait、DOM identity churn、複数goal予算、provider待機中cancel、DONEとverified_successの分離、origin・操作制約、replay)を通す。
- E2E-02(AWS Pricing Calculator)で、終端理由・mutation certainty・往復数・時間を比べる。

<!-- 現況 -->
2026-10-04: 制御骨格の共通化は実装済み。browserも既定で`WindowsRunCoordinator`+`BrowserAdapter`を使い
(`FINITACT_BROWSER_LOOP=agent`で旧ループ)、判断要求は経路別(`BrowserAdapter.decision_request`)のまま。
browserのOCR補完は入れず、届かない要素は画面経路への引継ぎ(ADR-0048・0051)で扱う。
<!-- /現況 -->
