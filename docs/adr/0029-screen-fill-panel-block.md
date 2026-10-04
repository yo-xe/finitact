# ADR-0029: screen fill候補を一様色panelごとの1候補に束ねる

- 日付: 2026-09-25
- 状態: 採択
- 決定者: yo-xe + Claude Code(反証相談`docs/consult/20260925-0949-20260925-bug0031-panel-block-topic.md`)

## 背景

BUG-0031: fill候補のrectが文字crop(61x20)のため、Jevは編集本文をfill先と見ず初手uncertainになる。
再送実験(BUG-0031「原因」)では、panelごとに1候補へ束ねると本文を0.89〜0.92で選び、Search欄への対照も維持した。
rectを広げるだけ・別属性`panel_rect`・同panel内の全fillへ共有、はいずれも閾値0.4未満か別候補へ確率が流れた。

## 決定

- `fill`有効時、各OCR領域の外周(4px格子で1cell外側)が一様色の連結成分に属するかを見る(`finitact/fill_panels.py`)。
  外周の5割以上が1成分で、次点成分が2.5割未満、成分がframe端から3cell以上離れ、bbox充填率0.5以上、高さが文字cropの4倍以上、
  cropがbbox内(格子1cell分の許容)の時だけ所属とする。bbox内包だけでは所属としない。
- 所属のある成分ごとに1つのfill候補を出し、所属領域の個別fillは出さない。click・drag・scroll候補は変えない。
  所属しない領域(独立した小入力欄など)は従来どおり文字crop単位のfill。
- 塊候補: `rect`=成分bbox、label=所属labelを上から` / `連結し120字で切る、`delivery_point`=最下行の下・最も幅の広い行の
  左端に最も近い、周囲8cellも同成分でどの文字cropにも触れないcell中心。該当cellが無ければ塊を出さず個別fillへ戻す
  (中心fallbackは使わない)。
- hit-testと配送は同じ`delivery_point`を使う。再同定(frame不一致時)は再抽出して塊を再構成し、所属領域の集合・bbox・
  `delivery_point`が一致する塊が1つある時だけ配送する。freshnessも同じ再構成で判定する。
- 外側向け上位候補の重複排除は、塊候補ではrectも鍵に含める(同じ先頭120字の別panelを潰さない)。

## 検討した代替案(没案)

- 本文候補のrectをpanelへ広げる/`panel_rect`別属性/同panel内の全fillへ共有: BUG-0031の再送で閾値未満か誤った候補へ流れた。
- bbox内の全領域を吸収: 独立した小入力欄を候補ごと消す(相談1)。
- 配送点が無い時にpanel中心へfallback: OCRが読まない別controlへ当たり得る(相談2)。
- 塊のlabelを「本文入力欄」等と命名: 未確認の意味を与える(相談3)。

## 影響

- 一様色panelは編集可能性の証拠ではない。listbox・drag zoneも塊fillになり、goalに合う語があれば誤選択し得る。
  従来も各行がfill候補だったので経路自体は新設ではないが、面積の分だけ選ばれやすい。verifyは事後検出に留まる。
- panel推定の追加時間はframeあたり10〜140ms(4 frameの実測)。
- 同色で枠の無い小入力欄はpanelへ吸収される。
- 採用判定のlive: VSCode本文fill、同画面のSearchへのfill、Tk小Entry+listbox(listbox側にgoal一致語)で、候補の残り方・選択・
  実配送点・変更先を確認する。

<!-- 現況 -->
2026-09-25: 採択(実装ccb0a5e、live 529f2cd)。実provider liveでVSCode save 5/5可、Search・枠なしTk listboxで塊の
誤選択0/10・0/5。残る不可はBUG-0033(tooltip)・BUG-0034(Entryラベル)で塊の有無に依らない。
<!-- /現況 -->
