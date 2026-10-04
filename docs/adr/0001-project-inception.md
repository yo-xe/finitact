# ADR-0001: プロジェクト発足

- 日付: 2026-09-20
- 状態: 採択
- 決定者: yo-xe

## 背景

公開 upstream の高速ブラウザ agent を `jev-exp` の実験に合わせて修正・評価する一方、
private 固有の変更と公開 upstream の責任境界を明確に保つ必要がある。

## 決定

- プロジェクト名: `jev-ultrafast`(kebab-case、生涯不変。バージョンは git tag で管理)
- 種別: python
- dev-utility の文書規約・git 規約に従う
- 公開 upstream の fork ではない独立 private copy とする。
- `origin` は private copy、`upstream` は取得専用とし、upstream 変更は選択的に統合する。

## 出口戦略(発足時仮説 — 未記入のまま開発を進めない)

- 誰の何を解決するか: `jev-exp` のブラウザ操作実験で、非定型なページ状態を低遅延に判断する。
- 出口の仮説(自用 / 公開 / 事業化のどれを目指すか): 自用 MVP。
- 継続・撤退の判断基準(例: 3 ヶ月使わなければ凍結): 未決(Q-0001)。

## 検討した代替案(没案)

- GitHub fork として公開する案: private self-use MVP の境界と一致しないため採らない。
- upstream 追従を自動化する案: private 固有修正との整合確認が必要なため、選択的統合を維持する。

## 影響

- upstream 由来と private 固有変更は git 履歴と remote の役割で識別する。
- 公開・push は別途明示指示がある場合だけ行う。

<!-- 現況 -->
`main` は upstream 由来実装に private 固有変更を積んだ自用 MVP。`origin` は private、
`upstream` は fetch 専用である。
<!-- /現況 -->
