# ADR-0011: screen有界2操作のlease・scope設計

- 日付: 2026-09-23
- 状態: 置換(ADR-0012)
- 決定者: yo-xe（相談指針）+ Claude Code

## 背景

Phase Fの2操作case実装に向けて、Windows GUI操作における複数ウィンドウの観測・配送・scope管理をどう設計するかをY相談（`docs/consult/20260923-2055-20260923-screen-two-action-scope-idle-topic.md`）で検討した。該当caseは`screen-unity-select-resolution-001`：Unity画面でdropdownを開き（操作1）、表示された解像度選択肢から1つを選ぶ（操作2）。

要件：
- 操作1後にpopupが新しく出現し、観測の対象に加わることを許容
- 操作1と操作2の間に別Finitactプロセスが割り込まないことを保証（既存ADR-0008 mutex）
- 人間の入力がないこと**の証明ではなく**、現実的な検出・防止の設計（ADR-0009の原則に従う）
- 45秒deadlineと1action budgetの制約内で完了

現行実装の問題点：
1. adapterが1 HWNDのみcaptureし、popup候補が出現しない
2. idle gateが配送ごとに再判定され、popup再観測の遅延で操作2がidle不足で拒否される可能性

## 決定

### A案：観測範囲を広げ、配送先はcandidate単位に限定

**推奨**する実装差分：

| 項目 | 設計 |
|---|---|
| runのroot | `screen:<HWND>:<PID>`は開始時のrootを固定。popup出現時にrootを変更しない |
| 観測対象 | root HWND + 可視・同PID・owner chainがrootに到達するtop-level window全て |
| candidate identity | root識別子、所属`scope_hwnd`（候補が属するHWND）、PID、観測世代、region idに束縛 |
| frame管理 | 観測ごとにHWND別にframeを保持。別窓の同一画素を同一観測として扱わない |
| freshness再検証 | 選択candidateの所属HWNDについて、identity/owner chain/寸法/frameを再確認。座標系変換も対応 |
| 配送許可 | hit-root（配送先window）は**選択candidateの所属HWNDそのもの**と一致させる。foregroundはrootまたは選択popupのみ許可 |
| scope遷移 | 操作間の新しいpopup出現を許容。選択済み観測から配送までの窓の消失・置換・owner変更は拒否 |

**反証と根拠**：
- 「同PID のowned windowなら目的のpopup」は成立しない。所有関係はアプリとの関係だけで、resolution menuという意味を示さない。操作前との差分、`UnityPopupWndClass`、owner chain、Full HD候補の視覚的文脈を組み合わせて複数popupを区別する
- hit-testを「許可集合のどれかに当たればよい」に緩めると、popup候補に対してrootをクリックできてしまう。配送判定では foreground と hit-root の両方を単一targetへ一致させることを維持する
- candidate idにはHWND/PIDがあるが、観測の保存キーは別。複数frame化ではscope情報込みの観測識別が必要

### B案：B2。mutex保持時間をrunスコープに拡張、idle再チェックは1回

**推奨**する寿命設計：

1. 最初の配送に入る**前に**mutexを取得し、**取得後に**30秒idleを確認する
2. 同じrunの操作1、popup再観測、操作2までmutexを**保持し続ける**
3. 各配送では環境scope・blocked状態・freshness・宛先を再検証
4. 完了/停止/例外/期限切れでleaseを終了。次runへidleを持ち越さない
5. バッジはrun全体、reticleと`delivering`状態は各配送区間に維持

この判断はADR-0009追記の「1配送区間」を「有界run区間」へ変更する仕様変更であり、ADR-0010のバッジ/reticleの寿命とは整合する。

**反証と根拠**：
- B1（tick比較）では入力の出所を復元できない。`GetLastInputInfo`は最後の入力時刻を返すだけで、tickの単調増加も保証しない。人間入力の後に自分のSendInputが入ればその時刻が最後に残る
- ADR-0009は人間操作検知による即時中断を明示的に却下。B1を必須安全条件とするのはこの決定の変更になる
- **B2の残余リスク**：開始後に人間が入力しても、2操作目で30秒idleを要求しなくなること。mutexはFinitactプロセスのみを排他し、人間やmutexを使わない別プログラムを止めない。画面と宛先が変わらない入力は freshness/hit-test でも検出できない

このリスクはB2採用時に明示記録する。「人間入力の完全検出」は採択条件に追加しない。

**変更してはいけない点**：
- action_budget は操作数を制限。保持時間は制限しない。既存45秒deadlineを維持し、遅い観測・provider処理から戻った後も期限超過なら送らない
- lease終了時の異常処理を維持。操作1後のrelease失敗・abandoned・不確実な配送を忘れて次runで再試行しない
- 別runがidle確認なしで配送できる許可として「mutex保持中」を流用しない

## 検討した代替案

- **別runへ分割する案**：callerにroot→popupの所有関係確認、合算budget・deadline、失敗状態引継を移す差分になり、idle問題は残る。単一HWND adapter維持の利点があるため、単一run・2操作caseの第一案にはしない

## 実装検証計画

以下をliveで反証する。成功判定自体は既存独立oracleを維持：

1. **通常の2操作**: 新規disposable Unityで、操作1後のpopupを観測し、合計4入力イベント・45秒以内で完了
2. **popupとrootのforeground差**: foregroundがrootのままでも正しいpopupへ配送できるか確認
3. **同名候補を持つ複数popup**: `Full HD`という文字だけで取り違えないこと確認
4. **別PID窓・非owned窓・owner変更**: candidateへ混入しないこと確認
5. **観測後のpopup消失・再生成・移動・寸法変更**: 古いcandidateを別frameへ適用しないこと確認
6. **操作間のmutex競合**: 別Finitactプロセスが操作1と2の間に配送できないこと確認
7. **操作間の人間入力**: popup閉鎖・foreground変更・候補変更は既存検証で拒否すること確認
8. **操作1後の遅延・配送不確実性**: deadline後に2操作目を送らず、不確実な操作を新runで自動再試行しないこと確認

Manifestの`second_action_idle_recheck_denied`は現在の配送単位gateを前提とする。B2採用時は、この停止条件とscope変化の定義を更新し、旧条件での結果と区別する。

## 関連ADR

- ADR-0008（named mutex）
- ADR-0009（synthetic input多層防御）
- ADR-0010（indicator Win32 layered overlay）
