# BUG-0034: Tk小Entry('Name:'+OLDVALUE 88x21)へのfillでprovider がラベル'Name:'を0.68〜0.84で選び、ラベル上(57,41)へ配送。Entryへfocusが移らず入力は消失(completed/unverified)

- 報告日: 2026-09-25 / 状態: 修正済み
- 重大度: 中 / 発生コミット: 30a96b5

## 症状

- (未記入)

## 再現手順

scripts/check_windows_mcp_screen_fill_panel_live.py tk --trials 5: 枠付きlistboxで7/7、枠なしで5/5がラベル選択。P(OLDVALUE)=0.01

## 該当箇所

- (未調査。当たりを付けるには dev map を使う)

## 原因

- (未調査)

## 修正

- 2026-09-25 修正済み(コミット c5b9e63): 原因: 末尾':'のcaption領域(Name:)が単独fill候補で、goal語と一致するためproviderが選び、label上へ配送していた。対処: 同行右隣と1対1に対応するcaptionを入力欄fill候補のlabelへ前置し(配送点は欄内)、caption単独のfill候補を出さない。再同定はfill候補の再構築で行う。Tk live 6試行で誤配送0(修正前5/5)、ただし直接成功0: listbox塊が首位でprovider_uncertain停止、融合候補は返却2位
- 2026-09-25 追補: 融合候補を「label=caption、欄の文字=現在値(`Observation.untrusted_values`)」へ分けた。一つのlabel内では
  goalの`Name`が`*name`行の塊とも同程度に合い、塊が首位を競っていた。実Tk候補3組のオフライン再生(各3回)でEntryの確率は
  平均0.27→0.97、塊0.40→0.01(caption属性の追加だけでは0.52、塊の行数属性だけでは0.31)。live Tk直接成功5/5(0.95〜0.97、
  1試行6.1〜6.7秒、listbox無変更)、VSCode Search 3/3・save 3/3で回帰なし(`bug0034-caption-value-results.jsonl`)。
