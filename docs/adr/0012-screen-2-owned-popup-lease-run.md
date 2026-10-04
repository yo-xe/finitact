# ADR-0012: screen有界2操作のために観測範囲をowned popupへ広げ、leaseをrun単位で保持する

- 日付: 2026-09-23
- 状態: 採択
- 決定者: (yo-xe承認待ち) + Claude Code
- 参照: 置換元 ADR-0011(yo-xe承認前に採択扱いしていたため提案として起票し直した)

## 背景

`screen-unity-select-resolution-001`(dropdownを開き、出現したpopupから既知候補を1つ選ぶ)は
現行実装で実行不能と確定した。adapterは開始時の1 HWNDしかcaptureせずpopup候補が出ない。また
ADR-0009層2のidle判定は配送ごとに行われ、操作1のSendInput自身がidle値を0へ戻すため操作2が拒否される。
反証相談の全文は`docs/consult/20260923-2055-20260923-screen-two-action-scope-idle-topic.md`。

## 決定

### A: 観測範囲をrootのowned popupへ広げ、配送先はcandidate単位に限定する

- runのrootは開始時の`screen:<HWND>:<PID>`に固定し、popupへ付け替えない。
- 観測対象はroot + 可視・同PID・owner chainがrootへ到達するtop-level window。PID一致だけでは加えない。
- candidateはroot、所属`scope_hwnd`、PID、観測世代、region id・rectに束縛する。観測識別子にもHWNDを含め、
  observationごとにHWND別frameを保持する。
- freshnessは選択candidateの所属窓についてidentity、owner chain、寸法、frame、anchorを再確認し、
  lease内でも所属frameを比較する。
- 配送時のhit-rootは選択candidateの所属HWNDそのものと一致させる。foregroundはrootまたは選択popupに限る。
  popup表示中のforegroundがどちらになるかは未確認のため、liveで確かめる前に`foreground == popup`へ置き換えない。
- 操作間の新しいpopup出現は許容する。選択した観測から配送までの窓の消失・置換・owner変更は拒否する。
- owned windowであることと「目的のpopupであること」は別判定にする。複数popupを区別できなければ配送しない。

### B2: mutexをrun全体で保持し、idleは取得後に1回だけ確認する

1. 最初の配送前にmutexを取得し、取得後に30秒idleを確認する。
2. 同一runの操作1、popup再観測、操作2までmutexを保持する。
3. 各配送では環境scope・blocked状態・freshness・宛先を再検証する。
4. 完了・停止・例外・deadlineでleaseを終える。idle許可を次runへ持ち越さない。
5. バッジはrun全体、reticleと`delivering`状態は各配送区間に保つ(ADR-0010と整合)。

これはADR-0009追記の「1配送区間」を「有界run区間」へ変える保証範囲の変更である。

**B2の残余リスク**: 開始後の人間入力に対し、2操作目で30秒idleを再要求しなくなる。mutexは協調する
Finitactプロセスしか排他しない。画面と宛先を変えない入力はfreshness/hit-testでも検出できない。
「人間入力の完全検出」は採択条件にしない(ADR-0009で証明不能と整理済み)。

維持する制約:

- 既存の45秒deadlineとaction_budgetを保つ。provider処理から戻った後も期限超過なら送らない。
- 操作1後のrelease失敗・abandoned・配送不確実は`InputEnvironmentState`へblockし、次runで再試行しない。
- mutex保持中であることを、別runがidle確認なしで配送できる許可として流用しない。

## 検討した代替案(没案)

- **B1: 操作間でlast-input tickが進んでいないことを継続条件にする**: `GetLastInputInfo`は最後の入力時刻しか
  返さず、人間入力の後に自分のSendInputが入れば見落とす。ADR-0009が却下した人間操作検知の再導入にもなる。
- **B3: 現状維持(操作間で30秒idleを再待機)**: 現行は待機せず即拒否する。45秒内に収まる可能性はあるが、
  待機と待機後の再観測を新たに実装しないと成立しない。
- **2操作を別runへ分割する**: root→popupの所有関係確認、合算budget・deadline、失敗状態の引継ぎがcallerへ
  移り、idle問題も残る。

## 影響

- `finitact/interaction_lease.py`・`windows_interaction_lease.py`: lease/idle/indicatorの寿命をrun単位へ分離する。
- `finitact/screen_grounded_adapter.py`・`windows_screen_grounded.py`: 複数HWND capture、scope付きcandidate、
  所属HWNDでのfreshness・hit-test。
- `case-manifest.json`: `second_action_idle_recheck_denied`停止条件とscope変化の定義を更新し、旧条件の結果と区別する。
  成功判定と独立oracleは変えない。
- live反証項目: 通常2操作(4入力イベント・45秒以内)、root/popupのforeground差、同名候補の複数popup、
  別PID・非owned窓・owner変更、観測後のpopup消失・再生成・移動、操作間のmutex競合、操作間の人間入力、
  操作1後の遅延・不確実配送。

## 追記: 2026-09-23 採択とB3の没理由

yo-xeが速度優先でB2を採択した。本開発の目的はwindows-mcpの低速性の解消であり、操作ごとに30秒以上の
idle待機を挟むB3は、ADR-0009の保証を保てても目的に反する。B2の残余リスク(操作間の画面を変えない人間入力は
検出しない)は本ADRの記載どおり受容する。

## 追記: 2026-09-23 実装

- B2: `WindowsSyntheticInputLease`がrun単位で保持する。初回配送でmutex取得とidle確認を1回行い、
  後続配送はdeadlineと環境一致だけ再確認する。`adapter.close()`→`guard.close()`で解放し、解放失敗は
  共有`InputEnvironmentState`へblockする。idle不足・indicator readiness失敗の初回は即解放またはclose時解放。
- A: capture任意seam`capture_scope()`がrootと、owner chainがrootへ届く可視owned popupを1回のPowerShell呼び出しで返す。
  popup候補だけ`scope`/`scope_hwnd`属性を持ち、root単独の候補・観測IDは従来と同一。popup宛て配送は
  `foreground_hwnds=(root, popup)`を渡し、hit-testは所属HWNDに固定する。popup消失はstale(not_attempted)。
- 仕様`docs/spec/specification.md`とmanifestの停止条件を同時に更新した。live未実行。

## 追記: 2026-09-24 B2の4項をADR-0021で改める

自runの入力だけが続いた時に限り、idle起点を次runへ持ち越す(BUG-0025)。

<!-- 現況 -->
採択(2026-09-23)。A+B2実装済み、offline検証済み、live未実行。B3は没。
<!-- /現況 -->
