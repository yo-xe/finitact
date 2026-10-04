# ADR-0024: screen click対象をgoal単位の完全一致labelで制約する

- 日付: 2026-09-25
- 状態: 採択
- 決定者: yo-xe + Codex(Astra反証相談)

## 背景

screen pathでscrollし、名指しの行が見えたらclickするtaskでは、対象が未表示でもproviderが別行をclickして
`provider_done`にできる。operation allowlistだけではclick対象を限定できず、自由文goalの解釈を配送gateにしてはならない。

## 決定

- Windows runにgoal IDから完全一致labelへの任意`click_label_constraints`を追加する。
- 指定goalでは、一致するclick候補が一意な時だけその候補をproviderへ提示し、配送直前にも同じ制約を強制する。
- 一致なし・同名複数・近似labelは全clickを拒否する。non-click候補は従来どおり扱う。
- `provider_uncertain`後のpickは元runと同じgoal・制約だけを受け入れる。制約なしのgoalは従来動作を維持する。
- 制約は配送対象だけを限定し、OCRの正しさ・探索完遂・outcome成功を証明しない。

## 検討した代替案(没案)

- scroll候補がある間は全click禁止: 対象が見えた後もscroll候補は残るため正常な選択を止める。
- `searching`/`selecting` phase: 遷移にも対象識別が必要で、単独では同じ誤判断を残す。今回は永続stateを要しない。
- goalの引用文字列を自動抽出: 自由文の意味をpolicyへ昇格し、一般click taskを壊す。
- 外側agentだけでscroll専用runからclick専用runへ切替: click解禁後の別行選択を防げない。

## 影響

`screen-tk-scroll-select-001`だけが明示opt-inする。候補提示だけでなく、providerが返すcandidate ID、cache hit、
pickの迂回経路でも配送前検査を共有する。未指定のfill/click taskへの挙動変更はない。

<!-- 現況 -->
2026-09-25: 採択。live再比較でFinitact 3/5、誤clickは0本(`phase-i-comparison.md`)。
<!-- /現況 -->
