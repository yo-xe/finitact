# ADR-0039: 操作の事後条件を部品の期待状態で照合し、事実として返す

- 日付: 2026-09-26
- 状態: 採択
- 決定者: yo-xe + Claude Code

## 背景

E2E-02で「VPN Connection」のトグルは配送されたが、アプリの規則(機能を最低1つ選ぶ)でcheckedが戻り、エラー文も
出なかった。Claude Codeは「画素不変停止のDOM版」を提案し、yo-xeは共通化を理由にした採用を疑い先行事例の調査を
求めた。codex調査(`docs/consult/20260926-0509-state-unchanged-prior-art-brief.md`)は修正付き採用。

## 決定

- 判定は「状態が変わったか」でなく「操作と部品に対応する期待状態になったか」(Playwrightの事後条件と同型)。
  checkboxは反転、radioは選択、selectは選んだ値、fillは入れた値。結果は`met / not_met / unknown`。
  既に期待値なら`met`。対象を一意に再同定できない・期待値を定義できない時は`unknown`。
- 結果は事実として返す: 次の判断の履歴(Jev)とgoal結果(外側agent)に、対象・期待・前後の観測値・結果を載せる。
  拒否理由は根拠(入力拒否属性など)が無ければ`unknown`で、機構は規則を作らない。
- `not_met`の操作は、同じ観測状態(意味指紋)・同じ対象・同じ操作内容の間だけ再提示しない。周辺の状態が変われば戻す。
  `unknown`は除外に使わない。
- 規則の推論(AutoInSpec型の制約蓄積)、LLMでの事後判定、自動再クリックは入れない。何を先に満たすかは判断側に残す。
- 部品の状態は既存の観測(DOMのchecked・current_value・value)から取り、候補の増減では判定しない(select成功で
  その選択肢が候補から消えるため)。

## 検討した代替案(没案)

- 画素不変停止のDOM版(状態が同じなら除外): 既に選択済みのradioや同じ値の入力を失敗と誤る。対象外の状態で解除できない。
- 状態不変を規則拒否と断定して返す: 観測から原因は導けない(HTMLのcheckbox activationの取消でも同じ見え方)。
- 毎操作のLLM事後判定: 入力が増え遅い。期待を観測と取り違える。

## 影響

- browserで実装。Windows UIAのtoggle等は同じ形で後から足す(UIA TogglePattern・SelectionItemPattern)。
- 将来の制約蓄積は、この結果(文脈つきの`not_met`/`met`)を観測事実として保存するところから始める。

<!-- 現況 -->
2026-10-04: 採択。browser・Windows UIA(Toggle・SelectionItem)とも実装済み(EXP-0014)。
<!-- /現況 -->
