# ADR-0009: synthetic input排他性の多層防御設計

- 日付: 2026-09-22
- 状態: 採択
- 決定者: yo-xe + Claude Code

## 背景

docs/QUESTIONS.md Q-0003(exclusive_environment_refに何を持たせ検証するか)への回答として、
「人間がそのセッションを操作していないことを技術的に証明する」先行事例を幅広くリサーチした
(`reports/Windows synthetic input human interference.md`)。結論: この証明はOS API・商用RPA・
ゲームQA・アクセシビリティツールのどこにも存在しない。GetLastInputInfo(idle time)はSendInput
自身の呼び出しで値が汚染されうるとMicrosoft自身が明記しており、証明ではなくヒント止まり。業界の
実際の解法は「検知」ではなく「構造的排他」(専用VM/専用session/使い捨てマシン)。ただしFinitactは
galleria実機の単一sharedデスクトップで動く前提であり、専用VM/専用sessionへ倒すことは今回の
スコープ外(採用すれば人間の実画面を操作できなくなる)。

## 決定

3層構成の多層防御を採る。各層の保証範囲を混同しない(過大主張しない)ことを設計上の要件とする。

1. **プロセス間排他(named mutex、実装・live検証済み: ADR-0008)**: finitactの複数プロセスが
   同時にsynthetic inputを送らないことを保証する。人間の存在とは無関係。
2. **開始前の事前条件チェック(idle time、次の一手)**: `GetLastInputInfo`ベースのidle time
   閾値チェックを、synthetic input実行の開始条件とする。閾値未達なら実行しない(fail-closed)。
   ただしこれは「証明」ではなく「ヒューリスティックな事前条件」であることをコード・ドキュメント両方に
   明記する(リサーチ結果の核心: idle timeはSendInput自身で汚染されうる)。実際に2026-09-22の
   Unity dropdown live clickで使った「idle time約91分」根拠を、一回限りの代替根拠から正式な
   事前条件へ格上げする。
3. **実行中の可視indicator(手続き的nudge、次の一手・別途相談)**: synthetic input実行中は画面上に
   「AI操作中」を示す表示を出し、人間に操作を控えるよう促す。**これは衝突を防止しない**。人間が
   気づかずに操作を続ければ衝突しうる。技術的保証としてではなく、UiPathのRobot session/PiPや
   RPA業界の「共有desktopでの運用は手続き的制約」という扱いと同じ位置づけで明記する。

`exclusive_environment_ref`はcaller側の監査ラベル(元のQ-0003選択肢A)に留め、層2のidle time
チェック自体はfactory側が都度live計測して判定する(callerが値を持ち込んで検証させる方式=選択肢B
は採らない。層2が「証明」ではなく「事前条件」である以上、値の真正性を厳密検証する意味が薄い)。

## 検討した代替案(没案)

- **WTS_SESSION_LOCKベースの「アクティブ/ロック中セッションが1つでもあれば起動拒否」
  (Power Automate Desktop方式)**: 専用の無人マシン/VM前提の設計であり、galleriaのような
  単一sharedデスクトップ(人間とfinitactが同じsessionを使う)には直接適用できない
  (「セッションが存在しない」ことを前提にしているが、galleriaには常にyo-xeのsessionが存在する)。
  将来専用VM/専用session運用に倒すなら再検討する。
- **人間操作を検知したら即座に実行中断**: idle timeが不確かな信号である以上、厳格な即時中断は
  誤検知(false positive)による過剰な操作性低下を招く。yo-xeの明示判断で却下
  (「人間操作一回で中断はあまりに操作性が悪すぎる」)。
- **機械的な排他性証明を諦めてQ-0003自体を保留する(元の選択肢C)**: リサーチにより「証明は
  存在しない」という結論が出たことで、C(先送り)ではなくヒューリスティック+手続き的nudgeの
  組み合わせという積極的な結論に到達できたため不要になった。

## 影響

- `exclusive_environment_ref`の形式が決まったため、screen-grounded/UIA factoryの
  `synthetic_input_allowed=True`拒否を解除する実装に進める。
- idle time事前条件チェックの実装(Windows-native、`GetLastInputInfo`ベース)が次の一手。
- 可視indicatorの実装形態(常時オーバーレイ/タスクトレイ通知/ウィンドウ枠の色変更等)は未決定。
  実装前に別途相談する。

<!-- 現況 -->
2026-09-22: 採択。層1(mutex)は実装・live検証済み(ADR-0008)。層2・3は未実装。
<!-- /現況 -->

## 追記: 2026-09-22 factory結線

3層を1回の配送区間へ束ねる`WindowsSyntheticInputLease`を実装し、screen-grounded factoryへ結線した。
mutex取得後にidle timeを再計測し、30秒未満なら配送せず、indicator表示中だけ再観測・宛先検証・
SendInputを許す。factory生成時と配送直前のsession/window station/desktop一致も確認する。
`exclusive_environment_ref`は決定どおり監査ラベルとして扱い、Windows固有scopeの申告値には使わない。
WSLでは観測経路を維持する一方、synthetic input opt-inはWindows-native Python以外でfail-closedにする。
UIA factoryはopt-inを受理してもpattern/window message経路だけを使い、synthetic fallbackを追加しない。

<!-- 現況 -->
2026-09-22: 層1〜3の単体live確認に加えfactory結線を実装。Windows-nativeでfactoryが実scope
(session 1 / WinSta0 / Default)から3層leaseを構築することをlive確認。実対象への配送を伴う統合live確認は未実施。
<!-- /現況 -->

## 追記: 2026-09-22 indicator readiness契約

indicator subprocessは、Tk windowを配置して`root.update()`を完了し、mapped状態を確認した後にだけ
親へ1回限りのreadiness ACKを返す。親は完全一致するACKと直後の子process生存を有界時間内に確認するまで
配送区間を開始しない。timeout、EOF、誤ACK、早期終了、cleanup未完了はいずれもleaseのyield前に失敗する。
このACKが保証するのは配送開始時の表示準備だけであり、その後の継続表示や人間との衝突防止ではない。

<!-- 現況 -->
2026-09-22: readiness handshakeとfail-closed fixture testを実装。Windows-nativeの実画面でのreadiness負例と
MCP transportを通したSendInput 0回の確認は未実施。
<!-- /現況 -->
