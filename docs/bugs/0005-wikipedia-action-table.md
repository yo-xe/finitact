# BUG-0005: Wikipediaの透明な言語セレクタがaction tableから欠落する

- 報告日: 2026-09-21 / 状態: 修正済み
- 重大度: 中 / 発生コミット: bb448ef

## 症状

- DOMに存在し画面上で操作できる「145 languages」がaction tableへ現れない。

## 再現手順

https://en.wikipedia.org/wiki/Web_browser をobserveし、145 languages操作が候補に含まれるか確認する

## 該当箇所

- `jev_ultrafast/snapshot.js`の可視性判定とhidden native proxy。
- `jev_ultrafast/browser.py`の実行直前可視性判定。

## 原因

- checkbox本体は`opacity:0`だが実際のhit-test対象で、可視labelは`aria-hidden=true`だった。
  既存実装は前者をopacity、後者をARIA可視性で除外したためproxyを解決できなかった。

## 修正

- 2026-09-21 修正済み(コミット c87a535): 透明native checkbox/radioのうち本体自身が直接hit-test可能なclick targetだけを候補化し、観測・guard・実行を同一条件に揃えた。negative fixture 3種とWikipedia live language dialogで確認。
