# BUG-0039: 消えるpopup内の選択(role=option等)が達成判定Eの対象外になり誤未判定を生む

- 報告日: 2026-09-28 / 状態: 修正済み
- 重大度: 中 / 発生コミット: e1bddee

## 症状

- (未記入)

## 再現手順

popup項目をclickするとpopup自体が閉じ、screenはafter証拠のhwndがNone、browserはrole=optionのnodeが消滅してpost-conditionが立たず未判定のまま外側agentの確認往復が発生する。AWSのunit dropdownはCDP経路のため両経路の修正が要る。

## 該当箇所

- (未調査。当たりを付けるには dev map を使う)

## 原因

- (未調査)

## 修正

- 2026-09-28 修正済み(コミット e1bddee): screen: popup消滅時にafter証拠をroot窓へfallbackし、root前後で選択項目の文字が新規出現+Jev fitで到達判定。browser: role=option clickを開いていたtrigger(aria-expanded)の閉鎖+label変化でmet(AWSのunit dropdownはCDP経路のため両経路に適用)。実装はcommit d6fcdb5(commit本文はBUG-0023を誤流用、正しくはBUG-0039)。実機検証は本セッションで継続。
