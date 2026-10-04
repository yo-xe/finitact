# BUG-0004: nested scroll外のcontrolとpopup linkが通常click候補になり未対応境界を診断できない

- 報告日: 2026-09-21 / 状態: 修正済み
- 重大度: 高 / 発生コミット: 283f5ad

## 症状

- nested scrollでclipされたcontrolと別tabを開くlinkが通常click候補になり、実行不能境界を
  モデルへ伝える構造化情報がなかった。

## 再現手順

nested overflow内でclipされたbuttonとtarget=_blank linkをsnapshotし、従来action tableへ混入してunsupported情報が無いことを確認する

## 該当箇所

- `jev_ultrafast/snapshot.js`
- `jev_ultrafast/model.py::choose`

## 原因

- viewport内だけを判定し、overflow ancestorによるclipとdocument境界を観測していなかった。

## 修正

- 2026-09-21 修正済み: clip ancestor判定とpopup候補除外を追加し、iframe/open shadow/nested
  scroll/popup/canvas/fileの件数を`unsupported` stateとしてTypeSafeへ渡す。7 fixture checks成功。
