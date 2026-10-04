# ADR-0038: スクロール領域に隠れた要素をスクロール付き候補として出す

- 日付: 2026-09-26
- 状態: 採択
- 決定者: yo-xe + Claude Code

## 背景

E2E-02(AWS Pricing Calculator)で、検索結果の「Configure」がページ内scroll領域の外にはみ出して候補に出ず、Jevは
提示された「Scroll down within this section」を選ばずBLOCKED寄りで止まった(E2E-I18)。要素はDOMに存在し、
見えていないだけだった。OCRでの探索も「撮影→OCR→scroll」の反復で遅い(E2E-I20)。

## 決定

- 構造(DOM・UIA)から観測できるが、scroll領域(またはページ)が隠しているだけの要素を候補に出す。候補には
  どちら側(below/above)にあり、押す前にscrollされることを付ける。自由座標は扱わず、候補は観測済みの有限個のまま。
- 範囲はその領域の高さ1つ分以内、近い順に最大40個。遠い要素は従来のscroll候補で近づける。
- 配送は押す前に対象を見える位置へscrollし、可視・最前面(hit-test)を確かめてから押す。見えないまま押さない。
  - browser: 既存の`act`が画面外・被覆時に`scrollIntoView`してhit-testする。`snapshot.js`の観測だけを変える。
  - Windows UIA: 画面外でもScrollItemPatternを持つ要素を候補にし、配送前にScrollIntoView→UIA再読→SendInput。
  - 仮想化リスト(実体の無い項目)とOCRのみの窓は対象外で、従来のscroll候補で進む。
- 隠れた要素の文字も他の画面文字と同じくuntrustedとして扱う。

## 検討した代替案(没案)

- Jevにscroll候補を選ばせる(現行): E2E-02で選ばれず、OCRでは反復が遅い。
- 隠れた要素をすべて出す: サービス一覧で163個の「Configure」になり、provider入力と判断が重くなる。
- 観測時に先にscrollして全体を読む: 観測が画面を変え、読むだけの操作でなくなる。

## 影響

- AWS Calculatorで「SQS」「VPC」の検索結果の「Configure」が候補に出て、SQSの設定画面へ進めた。ローカルの送信ページで回帰なし。
- 候補数が増える画面ではprovider入力が増える。E2Eの内部tokenで監視する。

## 追記1(2026-09-26): Windows UIAの実装

- 画面外項目は矩形が空のことが多く距離を測れないため、窓ごとにツリー順で40個までとする。
- 配送前にScrollIntoView→UIA再読で役割と名前が一意に画面内にあることを確かめ、新しい位置へSendInputする。
- WinForms ListBoxの項目はScrollItemPatternを持たず対象外(選択でscrollすると選択状態を変えるので使わない)。
- Windows側ChromeのUIAで、入れ子scroll内の画面外`Item 30`をscreen経路で選び、scrollしてから1回だけ押せた。

<!-- 現況 -->
2026-09-26: 採択。browser・Windows UIAとも実装済み(追記1)。
<!-- /現況 -->
