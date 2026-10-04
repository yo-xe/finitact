# ADR-0048: browserの境界は画面経路へ切り替えられる形で外側へ出す

- 日付: 2026-09-29
- 状態: 採択
- 決定者: yo-xe + Claude Code

## 背景

`run_browser`はiframe・open shadow root・`target=_blank`の先を候補にしない(`snapshot.js`の`unsupported`)。E2E-12でFinitactは
3/3とも境界としてBLOCKEDを正しく報告したが、4操作とも未達(windows-mcpは画面UIA経由で3/3)。yo-xeは決済・CAPTCHA・
Web Components・別タブを含むサイトの除外はありえないとした(Q-0006)。同じfixtureをFinitactの`run_windows`でChrome窓に対して
行うと3/3・4操作とも成功・誤配送0・21.9〜29.4秒・外側56k〜100k(`artifacts/e2e-live/e2e12-screen-n3-finitact-20260929/`)。

## 決定

1. `run_browser`の`final_state`と`observe_browser`に、到達できない要素の件数`out_of_reach`(frames・open_shadow_roots・
   popup_linksのうち0でないもの)と、そのタブを表示している窓の`screen_target`(`window:<hwnd>:<pid>`)を載せる。
   外側はこの構造を見て同じ窓を`run_windows`で操作する。文言での誘導ではなく値として渡す(provider文言調整を避ける方針)。
2. CDPで同一originのiframeとopen shadow rootを候補にする(旧選択肢2)のは、背景タブで動かしたい・速度が要る場面が
   実利用で出た時。別originのiframe(OOPIF)と新しいタブまでCDPで対応する(旧選択肢3)のは、それでも頻出する時。

## 検討した代替案(没案)

- 境界の報告だけで対応しない: 対象サイトを除外できないため不可(yo-xe)。
- windows-mcpへフォールバック: Finitact自身の画面経路で届くので別serverは不要。
- `run_browser`内で画面経路へ自動で切り替える: 窓の前面化と入力の占有を外側に知らせずに起こすため採らない。

## 影響

- 画面経路は窓の前面化と入力占有が要り、背景タブでは動かない。これが旧選択肢2・3へ進む条件になる。
- `screen_target`はタブが窓の表示中タブで、窓タイトルから特定できる時だけ付く(特定できなければ省く)。

## 追記1(2026-09-29): 決定1の実装とkept tabの活性化

- `out_of_reach`は`final_state`と`observe_browser`に常に載せ、`screen_target`はkept tab(`tab_id`あり)で
  `out_of_reach`がある時だけ付ける。run_browserのタブは背景で開くため窓タイトルが別タブを示し、初版は
  3回とも`screen_target`が付かず外側はBLOCKEDで止まった(`artifacts/e2e-live/e2e12-n3-finitact-reach-20260929/`)。
- そこで付与の前に`Page.bringToFront`でそのタブを窓の表示中タブにする。これはrun_windowsが同じタブを操作する
  前提でもある。窓の前面化を伴い得るのでtool説明に明記した(自動切替の没案と違い、入力はしない)。
- 結果: E2E-12を`run_browser`起点のままN=3で3/3・4操作とも成功・誤配送0・29.7〜42.8秒・外側84k〜154k
  (`artifacts/e2e-live/e2e12-n3-finitact-reach2-20260929/`)。外側は3回とも`screen_target`を見てrun_windowsへ
  切り替えた。画面経路を最初から指示した場合(21.9〜29.4秒・56k〜100k)より遅く、差はrun_browserでの1往復分。

## 追記2(2026-10-04): 決定2の棄却

決定2(同一originのiframe・open shadow root、さらにOOPIF・新しいタブをCDPで候補化する)は棄却する(yo-xe判断)。
届かない要素は決定1の`screen_target`引継ぎでE2E-12を3/3達成し、ADR-0051のrouteもframe・shadow root・popup linkを
検出すると画面経路へ回す。発動条件とした背景タブでの操作・速度の要求は実利用で観測されていない。必要になれば新しいADRで起こす。

<!-- 現況 -->
2026-10-04: 採択。決定1は実装済み(追記1)。決定2は棄却(追記2)。
<!-- /現況 -->
