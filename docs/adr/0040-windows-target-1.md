# ADR-0040: Windowsの外側向けtargetを1種類にし経路を合成入力許可で決める

- 日付: 2026-09-27
- 状態: 採択
- 決定者: yo-xe + Claude Code

## 背景

E2E-03(`artifacts/e2e-live/20260927-e2e03-uiaev/`)の3試行とも、外側agentは最初`uia:`でEmail欄をclickし、
Edit欄にInvokePatternが無いためblocked→journal→`screen:`切替で約10〜12秒失った(1試行は`uia:`で5.0秒・未検証)。
screen経路はADR-0036でUIA第一になっており、外側に経路を選ばせる理由が無い。yo-xe承認の速度改善(2)。
consult: `docs/consult/20260927-0206-20260927-outer-target-and-ref-topic.md`。

## 決定

- MCPで外側に見せるtargetは`window:<HWND>:<PID>`だけ。`list_windows`の各窓に`target_id`を付ける。
- 経路はrun単位で`synthetic_input_allowed`から決める: 既定(true)はscreen経路(UIA第一・OCR補完・SendInput+ADR-0009門)、
  falseはpattern-only UIA経路(前面を奪わない)。`observe_window`はscreen経路で読む。
- 旧`uia:`/`screen:`は互換で受けるが説明から外す。

## 検討した代替案(没案)

- 候補ごとにpattern配送とSendInputを混ぜる(consult叩き台A): 前面化の有無が候補ごとに変わり利用者に予測できず、
  pattern配送がidle門の自己入力台帳に載らない等の差分が要る(consult詳細1)。E2E-03の損失は経路選択だけで消えるため見送る。
- 説明文で「Edit欄はscreenを使え」と書く: 呪文化でアプリごとに規則が増える。
- 同一target内でuia失敗時にscreenへ自動fallback: 同じrunで前面化の有無が暗黙に変わる。

## 影響

- E2E-01・03のハーネスpromptから経路の説明を外した。E2E-03で効果を計測する。
- 速度改善(3)(observe_window項目refの直接実行)はconsultの第3案で進める: refは既存pickの入口で受け、goal型runのまま
  Jev選択だけを省く(達成判定Eとoutcome語彙は不変)。同定はUIA runtime id必須・rectは移動確認、refは一回限りで
  次の観測/配送までと短い時間上限。別ADRで決める。

<!-- 現況 -->
2026-09-27: 採択。実装済み。E2E-03 Finitact 3/3・20.2〜31.7秒(E2E-I44)。
<!-- /現況 -->
