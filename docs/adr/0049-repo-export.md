# ADR-0049: 公開repoは許可リストの断面を新規履歴へ一方向exportし評価はレポートとして公開する

- 日付: 2026-09-30
- 状態: 採択
- 決定者: yo-xe + Claude Code

## 背景

本repo(private `yo-xe/finitact`)を外部公開する(north_star 出口仮説)。本repoの履歴は473 commitのauthorが
個人email、docs履歴に個人パス・live記録を含む。方式の検討は`docs/plans/public-repo.md`、判断はQ-0011(2026-09-30回答)。

## 決定

1. 公開側が`yo-xe/finitact`を名乗り、本repoは`yo-xe/finitact-dev`(private)へ改名する。公開名はQ-0012で再検討中。
2. 公開repoは新規履歴で始め、release単位の断面commitを積む。private → public の一方向で、公開側PRはpatchで本repoへ取り込む。
3. 書き出しは許可リストと検査(個人情報・秘密・test・build)を通った断面だけ。ADRは全件同梱する。
4. 他システムとの比較・Jev自体の性能評価(`docs/evaluations/`)は公開価値そのものなので、raw記録ではなく
   整えたレポート(方法・環境・結果・限界)として公開する。
5. 公開時期は、export検査・公開用README・評価レポートが揃った時点でyo-xeが判断する。

## 検討した代替案(没案)

- `git filter-repo`で本repo履歴を浄化して公開: 消し切った保証を毎回取る必要があり、断面検査より確実性が低い。
- `docs/evaluations/`をそのまま同梱: 個人パス・私的な対象名・未整理の中間記録を含み、読者に方法と限界が伝わらない。
- 評価を公開しない: 外側token・速度の比較が本プロジェクトの主な訴求点であり、捨てる理由がない。

## 影響

- `scripts/export_public.py`と許可リストを新設する。評価レポートは公開用に書き起こし、検査の対象に含める。
- upstream帰属は履歴でなく`LICENSE`・`NOTICE`が担う。

## 追記1(2026-09-30)

Q-0012はA: 公開名は`finitact`のまま、Jevとの関係はGitHubのdescription・topics・README冒頭で示す。repo名にJevを入れると
TypeSafe公式品と誤認させ、provider非依存(north_star)とも食い違うため。評価レポートはグラフを含め、単一commitで再計測した値を主にする。

## 追記2(2026-10-04)

公開後の同期手順(yo-xe了承 2026-10-04)。開発は本repo(private、公開時に`finitact-dev`へ改名)で続け、
公開側へはrelease単位で`scripts/export_public.py --repo <公開checkout> -m "<release>"`の断面commitを積み、
本repoと同じ`vX.Y.Z` tagを公開側にも打つ。外部PRは公開側でmergeせず、patchを本repoへ適用して
`Co-Authored-By`で帰属を残し、次のreleaseのexportで公開側へ戻す。公開側だけの変更を作らないことで
一方向(決定2)を保ち、断面検査(決定3)を毎回通す。

## 追記3(2026-10-04)

Q-0013はA: `docs/bugs`も原本のまま同梱する(公開コード・ADRが参照するBUG-IDの参照先を残すため)。加えて、追記のみの
原本は開発の経緯をそのまま映し読者の入口に向かないので、現時点の断面として主題別に整理した文書(設計判断の地図・
既知の問題)を英日2本ずつ書き、原本への入口にする(yo-xe 2026-10-04)。原本は書き換えない。公開後の利用者報告はgh issueで受ける。

<!-- 現況 -->
2026-10-04: 採択。export scriptと許可リストは実装済み、公開後の同期手順は追記2、bug記録と整理文書は追記3。評価レポートと再計測は`docs/plans/public-repo.md`。
<!-- /現況 -->
