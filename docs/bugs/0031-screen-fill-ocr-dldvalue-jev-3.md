# BUG-0031: screen fillの編集領域候補がOCR誤読ラベル(DLDVALUE)で低得点となり、Jevが3回uncertain→4回目にタブ名'finitact_phase_i.py'へfillを選んだ(idle gateで未配送)

- 報告日: 2026-09-25 / 状態: 修正済み
- 重大度: 中 / 発生コミット: 488f719

## 症状

- VSCode fill caseの初手が毎回`provider_uncertain`になり、外側agentのpickか言い換えを1往復要する(x5b 5/5、ADR-0028版5/5)。
- 本文候補が外側へ見せる上位5件から外れると、外側agentがタブ名へfillを選ぶ(x5 trial 1。idle gateで未配送)。

## 再現手順

Windows側artifacts/phase-i/vscode-fill-values-x5/finitact trial 1。mcp-server.log 09:07:16〜09:08:07。候補はmenu・status bar・タブ・本文を同列のfill候補として提示

## 該当箇所

- `finitact/screen_grounded_adapter.py` `observe`: OCR regionごとに同じrect(文字cropの外接箱)でfill候補を作る。

## 原因

- 初手の確率はBLOCKEDが最大(x5b 5本で0.22〜0.46)、次が`key:Press Ctrl+S`(0.13〜0.22)、本文`OLDVALUE`は0.01〜0.10
  (`mcp-server.log`のuncertain行。ADR-0028版5本も同形)。labelの誤読(BUG-0032)も、save込みのgoal文も主因ではない。
- 主因はfill候補のrect。本文候補のrectは文字crop(61x20)で、Jevはlabelでなくrectの大きさで入力欄らしさを判断する。
  `scripts/probe_fill_rect_replay.py`で09-24の実request(56候補)を各5回再送した結果(P(本文)):

  | 変種 | P(本文) | 選択 |
  |---|---|---|
  | baseline | 0.04〜0.06 | BLOCKED 4・Ctrl+S 1 |
  | goalからsaveを除く | 0.05〜0.07 | BLOCKED 5 |
  | edge_contour由来fillを除く(40候補) | 0.03〜0.05 | BLOCKED 5 |
  | 本文以外のfillを除く(17候補) | 0.57〜0.61 | 本文 5 |
  | 本文rectを行幅に広げる(1110x20) | 0.47〜0.56 | 本文 5 |
  | 本文rectを編集領域の上半分に広げる | 0.73〜0.77 | 本文 5 |
  | 本文rectを編集領域全体に広げる | 0.82〜0.85 | 本文 5 |
  | 編集領域rectを`File`へ付ける | 0.01〜0.02 | `fill:File` 5(0.63〜0.69) |

- 非終端選択の閾値は`UNCERTAIN_CONFIDENCE`=0.4(ADR-0019)。行幅rectでも閾値は越えるが余裕は小さい。
- 相談(`docs/consult/20260925-0941-*`)を受けた追加再送。panelは`capture_screen_case_frames.py`のframeから
  一様色のflood(許容差3)で推定し、frame端に触れる・充填率0.5未満・高さが文字cropの3倍未満なら不採用とした。
  VSCodeではbreadcrumb・行番号・本文が同じ編集panelに入る。

  | 変種 | P(本文) | 選択 |
  |---|---|---|
  | 行ハイライト帯rect(948x19、素朴な推定の帰結) | 0.42〜0.48 | 本文 5 |
  | rectは維持し`panel_rect`=編集領域を別属性で付与(相談の(B)) | 0.33〜0.38 | 本文 5(全て閾値未満) |
  | `File`へ`panel_rect`=編集領域 | 0.06〜0.07 | BLOCKED 1・Ctrl+S 4(誤fillなし) |
  | 推定panelを同panel内の全fillのrectへ | 0.12〜0.14 | breadcrumbの`finitact_phase_i.py` 5(0.31〜0.36) |
  | panelごとに1候補へ束ね、labelは内包文字列の連結 | 0.89〜0.92 | 本文を含む塊 5 |
  | 同じ束ね方でgoalをSearch欄への入力に変更(対照) | 塊0.01〜0.02 | Search 5(0.86〜0.89) |

- (B)は誤fillを招かない代わりに閾値へ届かない。rectの共有は同panel内の他文字へ確率を奪われる。panel単位の1候補は
  本文を閾値の倍以上で選び、小さな本物の入力欄(Search)を奪わなかった。
- 最後の対照のとおり、領域を誤って推定するとproviderは閾値を越える確信で誤った先へfillする。rectを広げる修正は、
  入力領域の推定が正しいことを別に確かめる必要がある。
- ADR-0029実装の候補(`scripts/probe_fill_panel_replay.py`、実OCR labelのまま`DLDVALUE`)を実requestへ差し込んだ再送:
  塊`è / DLDVALUE / 1 / 2`(rect 64,36,1136x728、配送点134,134)を5/5選択(0.66〜0.73)。Search対照もSearch 5/5(0.79〜0.82)。
- live(`artifacts/phase-i/vscode-fill-panel-x5`、外側agent経由): 4/5可、5本とも外側のUI呼び出しは1回(pick往復なし)、
  可の4本は14.8〜16.6秒。不可の1本(trial 3)は2操作ともconfirmedだが保存ファイルはOLDVALUEのまま。外側経路では
  Finitact内の選択と配送点が残らず原因は未特定(未保存の2手目か配送先違いか)。
- 実provider live(`scripts/check_windows_mcp_screen_fill_panel_live.py`、windows-checkoutの`artifacts/phase-i/fill-panel-*`):
  - save x5: 5/5可(9.7〜10.4秒)。塊0.52〜0.62→Ctrl+S。塊labelに本文が読めない回(`nitac / py / 7 / 2 / 1 / 2`)も塊を選んだ。
    前回の不可1本は再現せず。配送点xは86〜134で揺れる(行番号gutter側に落ちる回あり、全て可)。
  - search x5×2: 塊の選択0/10(0.01〜0.03)。判定式の不具合(`OLDVALUE\n`比較)を直した2巡目は3/5可、不可2本はBUG-0033
    (前試行のpointer残留によるtooltipへのfill、本文へ誤入力)でADR-0029と無関係。
  - tk: 枠付きListboxは全体を覆うedge_contourが配送点を消し塊が出ない(設計どおり、`test_no_free_panel_cell_...`)。
    枠なしListboxでは塊(`Nickname / Display name / ...`)が出て、選択0/5(0.10〜0.13)。Entryは全本不可でBUG-0034
    (ラベル`Name:`選択、塊の有無に依らず同じ)。
## 修正

- 2026-09-25 修正済み(コミット 529f2cd): 文字crop単位のfill候補を本文と見なさずuncertainになっていた。ADR-0029で一様色panelごとの1候補に束ね、実provider liveでsave 5/5可(ccb0a5e/529f2cd)
