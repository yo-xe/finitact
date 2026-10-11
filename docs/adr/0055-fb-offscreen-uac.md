# ADR-0055: 利用者視点FBへの対応(offscreen既定省略・UAC検知・採否)

- 日付: 2026-10-11
- 状態: 採択
- 決定者: yo-xe + Claude Code

## 背景

実利用の2件(`docs/research/usage-feedback-20261010.md`、`docs/reports/2026-10-11-blender-exp-mcp-feedback.md`)が
改善候補6点を挙げた。各点の不利益を比べて採否を決めた(yo-xe 2026-10-11「デメリットを検討の上、実装要否を決めて」)。

## 決定

| FB | 判断 | 理由 |
|---|---|---|
| 1 `observe_window`が74〜87件(大半offscreen) | 採用 | `contains`なしではoffscreen項目を省き`offscreen_omitted`で件数を返す。`contains`指定では従来どおり出す |
| 2 `unverified`の自動照合(期待ラベルを渡す) | 見送り | 期待ラベルが操作前から在ると誤って検証済みになる。新規出現に限る設計はgoal schemaを太らせ、外側のtokenが毎回増える。`screen_changes`が同じ結果で独立証拠を既に返す |
| 3 `screenshot`の返却規則が不均一 | 文言のみ | 規則(直前に読んだ状態と同じ時だけ画像)は変えず、`withheld`の理由文へ明記 |
| 4 ref無しgoalのprovider試行が6回 | 見送り | 試行増はchooserの探索が原因で、1候補なら省略する案は誤候補の無検査実行を許す。refの利用は`observe_window`説明が既に促す |
| 5 UAC(セキュアデスクトップ)が見えない | 採用 | `consent.exe`の存在を`elevation_prompt`として`list_windows`・`observe_window`・`run_windows`の結果へ出す。出現時のみで通常は増えない |
| Blender: 未知`tab_id`の生CDPエラー | 採用 | `unknown tab_id`と入手元(`run_browser`のkeep_tab)を示すエラーへ。tab一覧toolは足さない(観察は`run_browser`が返したtab_idで足りる) |

Blenderの`listening on …`エラーはBUG-0078修正後も再現した(BUG-0079)。原因未特定のため本ADRでは直さない。

## 検討した代替案(没案)

- offscreen項目の全廃: スクロール先のrefを`contains`でも拾えなくなる。件数だけ返して省くのを採った。
- UACの検知を毎回の観察で行う案は採らず、`consent.exe`がある時だけ項目を足す。無い時は出力が変わらない。
- `observe_browser`へ`tab_id:"new"`を許す案: 観察が副作用(tab作成)を持つため採らない。

## 影響

- `observe_window`の既定出力が縮む。Windows実機では設定アプリ相当の窓で省略件数が1〜数件(設定アプリでの74〜87件規模は未再測)。
- UAC表示は実機で検証した(prompt出現0.2秒で検知、拒否後に消滅)。

<!-- 現況 -->
2026-10-11: 採択。実装済み・ユニットテスト通過・UAC/observe_windowは実機確認済み。E2E-05で回帰確認中。
<!-- /現況 -->
