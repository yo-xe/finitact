# ADR-0006: Windowsアクション adapterの暫定契約

- 日付: 2026-09-21
- 状態: 採択
- 決定者: yo-xe + Claude Code

## 背景

Phase 4(`docs/plans/finitact-mcp.md`)はWindows UI Automation向けの仮`ActionAdapter`契約を定義する
必要があった。素案(Notepadでの限定spikeを想定した`observe()`/`fresh()`/`act()`/`close()`)を
claude opus(dev consultのcodex利用枠切れのため`--via claude --model opus`で代替、
`docs/consult/20260921-1819-20260921-windows-adapter.md`)へ相談したところ、複数の契約不足を指摘
された。続けてwindows-mcpで実機(yo-xeの実windowsマシン)を読み取り専用・非破壊で確認し、指摘の
一部を裏付けた。

## 決定

- `Observation`に`complete: bool`と`read_errors`を持たせ、UIAツリー走査が部分的にしか終わらな
  かった場合、その観測から候補を作って渡すことを型で禁じる(`__post_init__`で強制)。UIAはBrowserの
  `Runtime.evaluate`単発評価(browser.py:61-104)のような原子性を持たないため。
- `fresh()`の戻り値を`bool`から`Freshness`(`FRESH`/`REBOUND`/`STALE`)へ広げる。Browserの現行
  `fresh()`(browser.py:105-142)は既にrebindを戻り値へ載せず`action`/`page`を副作用で書き換えて
  おり、これをWindows側で繰り返さない。
- `observation_id`と`semantic_id`を別フィールドとして必須にする。agent.pyの同一行動3連検知・
  `diagnose()`はこの二重性に依存している(browser.py:159-177)。
- `MutationResult`に`input_method: "pattern" | "synthetic_key"`を必須にする。UIAの`ValuePattern.
  SetValue`(原子的・フォーカス不要)と合成キー入力(フォーカス依存・他windowへ漏れうる)は危険度が
  別物のため。
- `ActionAdapter`に`ownership: "owned" | "attached"`を持たせる。Browserの`close()`
  (browser.py:153-157)は自分が作ったtargetを閉じる前提だが、Windows adapterは既存プロセスへ
  attachする構成を許すため、同じ意味で実装すると自傷経路になる。
- 未信頼な値(control本文・入力値)は`ObservedCandidate.attributes`へ入れず、`Observation.
  untrusted_values`(candidate ID→値)として分離する。Phase 2で確立した`untrusted_context`分離
  (contracts.py:46,89-93)をWindows側で後退させないため。
- スコープ不一致(設定windowと異なるプロセス・windowを指してしまった状態)は`STALE`ではなく
  `ScopeViolation`として区別する。staleは「再観測してよい」が、スコープ不一致は「止める」べきで、
  黙って再観測すると別windowへ入力しうるため。

windows-mcpでの実機確認(2026-09-21、yo-xeのマシン、読み取り専用/自作の使い捨て一時ファイルのみ)で
判明した事実は`docs/plans/finitact-mcp.md`のPhase 4項目と
`docs/consult/20260921-1819-20260921-windows-adapter.md`の追記に記録した。要点: 対象機のNotepadは
Store版(edit要素`RichEditD2DPT`)に一貫して着地し従来版へ固定できないこと、`WM_GETTEXT`が
`RichEditD2DPT`に対して独立検証channelとして機能すること、RuntimeIdはズーム・リサイズ・折り返し
トグルでは不変であること(タブ再読込等の破壊的操作での再利用可否は未検証)。

契約は`finitact/action_adapter.py`に実装し、fake adapterでの特性テスト(`tests/test_action_adapter.
py`)で契約の形だけを固定した。実際のUIA実装(Windows専用、このLinuxリポジトリでは動かせない)は
未着手。

## 検討した代替案(没案)

- **Browserの`ActionAdapter`的な既存インターフェースをそのままWindows側へ流用する。** 却下。
  ADR-0004が「Windows adapterはbrowser契約から演繹して固定せず、限定UI Automation試作後に共通境界
  を確定する」と既に決めており、かつOpusの指摘どおりBrowser側の`fresh()->bool`自体が既に弱い契約
  なので、そのまま流用すると弱点ごと継承する。
- **`fresh()`の戻り値を2値(bool)のまま、rebind有無を別の属性やログへ出す。** 却下。呼び出し側が
  戻り値だけ見て判断を誤りうる(Browserの現行実装で既に起きている問題)。3値enumにして型で
  区別させる方が安全側に倒せる。
- **未信頼valueをcandidate属性に含めたまま、利用側でfilterする運用ルールにする。** 却下。運用
  ルールは忘れられる。Phase 2の`untrusted_context`分離と同じく、契約の型で強制する。

## 影響

- Phase 4の残りチェックリスト(実際のUIA最小試作、matrix化、Windows-MCPとの比較)は、この契約を
  土台に進める。契約自体はWindows専用実装が無くても`tests/test_action_adapter.py`で検証できる。
- Browser側の`fresh()`/`close()`は今回変更しない(ADR-0004の方針どおり、試作結果が出るまで共通化
  しない)。将来両者を統合する場合は、Browser側もこのADRの3値`Freshness`・ownership区別へ揃える
  ことになる可能性が高いが、それは別途判断する。

<!-- 現況 -->
2026-10-04: 採択・実装済み。契約は`finitact/action_adapter.py`、実装はUIA経路(`windows_uia_adapter.py`)と
screen経路(`screen_grounded_adapter.py`)。以下は2026-09-21〜22の経緯。

2026-09-21: 採択。契約(`finitact/action_adapter.py`)と特性テストまで実装。実際のUIA試作は
windows-mcp経由でPowerShell+UIAutomationClientにより実施し、6状態matrixのうちfocus_moved・
target_lost(ScopeViolation)・unsupported_controlはlive確定、duplicate_controlはこのNotepad実装
(非activeタブの編集要素がUIA木から除外される)では構造的に誘発不能と判明、stale観測/REBOUND・
uncertain mutationはPowerShell側のtooling障害(関数境界を越えたFindAll/.Currentアクセスの不具合)
で未確定のまま持ち越し。Windows-MCPとの比較は未着手。詳細は
`docs/evaluations/windows-adapter-prototype/results.md`。

2026-09-21(同日追試): FindAll呼び出しをすべてインライン展開する回避策で、REBOUND(タブclose時の
RuntimeId変化・semantic guard一致)とSTALE(構成的テスト、0件一致)をlive確定。uncertain mutationは
`ValuePattern.SetValue`後のタブclose・別タブContentDialog放置中のSetValueの2経路を試したが、
`SetValue`が想定以上にatomicでいずれもmodalブロックを起こせず未確定。6状態中5状態が確定
(1状態は構成的テスト)、duplicate_controlは構造的に誘発不能、uncertain mutationのみ持ち越し。
Windows-MCPとの比較は未着手。

2026-09-21(同日3回目): yo-xeの承認を得て、uncertain mutationを対象プロセス(使い捨てNotepadのみ)の
一時サスペンド(`NtSuspendProcess`/`NtResumeProcess`、`try/finally`で確実に復帰)で誘発しlive確定。
デッドライン(2500ms)超過時点では`Started=True, Done=False`で`MutationUncertain`へ分類、resume後に
保留呼び出しは実際には成功しており(`Done=True`)、独立検証(`WM_GETTEXT`)で書き込み内容と完全一致を
確認した。6状態すべてに証跡が揃い、matrix化は完了。残るはWindows-MCPとの比較のみ。

2026-09-21(同日4回目): Windows-MCPとの限定live比較を実施し、Phase 4を完了した。最重要の発見は
速度差ではなく構造的な違い: windows-mcpの`Click`/`Type`はOS入力キューを経由するため、比較作業中に
yo-xeの会話用ターミナルへ実際に誤入力する事故が発生した(yo-xeが確認・削除)。同一構造の失敗が
セッション内で3回独立に再現し、UIA経由(Finitactの`pattern`系)は前面windowに一切依存しないため
この種の失敗が本セッションを通じてゼロだったことと対照的だった。定量結果はFinitact 2/2完了・
約2.6秒/回、Windows-MCP 1/3完了(残り2回は前面競合で中断)。詳細は
`docs/evaluations/windows-adapter-prototype/windows-mcp-comparison.md`。Phase 4完了条件を満たした
と判断する。

2026-09-21(Phase A追記): コンテキストメニュー(右クリック)を契約へ位置付けた。新規adapter
メソッドは不要(既存`observe()`→`act()`→`observe()`循環で足りる)だが、`ScopeViolation`の判定
粒度が単一HWND比較のままだとowned popup window(メニュー自体)を誤検知・見落とすと判明したため、
owning-window tree(`GW_OWNER`)単位の判定に改めた。`MutationResult.input_method`へ
`synthetic_pointer`を追加(右クリックはキーでなくポインタの合成入力、危険度クラスは
`synthetic_key`と同じ)。`operation_vocabulary.py`に`OPEN_CONTEXT_MENU`(pattern非由来・
adapter側policyで付与)を追加。`tests/test_action_adapter.py`/`tests/test_operation_vocabulary.py`
で契約を固定(fake adapterのみ、live UIA未検証)。詳細は
`docs/plans/finitact-desktop-generalization.md` Phase A。

2026-09-21(Phase A完了): `operation_vocabulary.py`をwindows-mcp経由でgalleria実機のNotepadへ結線し
 live確認した。Edit/Document限定でない全descendant列挙(68要素)に対し`operations_for()`/
`is_candidate_visible()`とも想定どおり分岐し、特に`IsOffscreen`判定は蓄積した旧タブ7件で実際に
発火した(机上ではなく実データでの初確認)。詳細は
`docs/evaluations/windows-adapter-prototype/phase-a-wiring.md`。Phase Aのチェックリストは完了、
`ActionAdapter`本体(`observe()`/`act()`)の実装は未着手のままPhase Bへ進む。

2026-09-21(Phase B Interaction Lease): 合成入力をfail-closedにする契約を追加した。runの明示opt-inと
識別付き隔離入力環境(session/window station/desktop/generation)が揃わない限り送信せず、Windowsの
named mutexを取得してから対象を再解決し、keyboardはcontrol focus、pointerは最新hit-testを含む
方式別検証を通過した場合だけ送信する。取得期限は待機だけを制限し、所有権を自動失効させない。
送信開始後の例外・未確認結果・lease解放失敗は`MutationUncertain`として環境をblockedにし、後続の
自動実行を拒否する。pattern失敗からsyntheticへ暗黙fallbackしてはならない。強制境界はadapter、
環境割当・run直列化・uncertain後停止はcoordinatorの責務とした。Astraの反証レビューは
`docs/consult/20260921-2224-20260921-phase-b-interaction-lease-topic.md`。専用自動化セッションを
synthetic inputの必須条件にする運用判断はQ-0001の回答待ち。

2026-09-21(Phase B運用判断): yo-xeの承認により、専用Windows Session自体を共通契約の必須条件には
しない。無人synthetic runにはOS非依存の「検証済みの排他的入力環境」保証を必須とし、Windowsでは
専用Sessionを当面の推奨実装とする。共有環境のpattern-only runは許可する。これに伴い共通型から
`session_id`/`window_station`/`desktop`を除き、opaqueな環境identityとgenerationだけを保持する。
Windows固有の3値は`WindowsInputScope`へ隔離した。RDP切断・画面ロック・GPU依存アプリ等を含む
具体的なWindows隔離方式の成立性は未検証であり、adapter結線時のlive確認事項とする。

2026-09-21(Phase C): browser loopへWindows条件を押し込まず、`WindowsRunCoordinator`を別module相当の
実装として`runs.py`へ追加した。共有するのは`DecisionRequest`/`RunResult`の意味、run fingerprint、
永続ledgerのreplay・未完了run拒否であり、URL originとowning-window tree、DOMとUIAの鮮度処理は
分離する。`windows_decision_request()`は不完全観測をproviderへ渡さず、control値をuntrusted contextへ
分離する。TypeSafe内部のbrowser必須field依存はadapter-neutral候補選択payloadで解消した。MCPには
`run_windows`を登録するが、live UIA adapter未設定時はrun開始前に`adapter_not_configured`で拒否する。
設計反証は`docs/consult/20260921-2241-20260921-phase-c-windows-coordinator-topic.md`。fake adapterと
実provider classの通信差替えによるOS非依存結線のみ確定し、live UIA/Lease/判断品質は未検証。

2026-09-21(Phase D途中): PowerShell 5.1/UIAutomationClientによるpattern-only live adapterを実装し、
MCP targetを`uia:<HWND>:<PID>`へ限定してfactoryを結線した。target形式不正、HWND/process不一致、
synthetic opt-inはfail-closedで拒否する。Notepad fillは`WM_GETTEXT`独立照合で完全一致した。一方VSCodeは
ValuePattern呼び出しが成功しても値・fixtureが変化せず、File menuからSaveも列挙できなかった。
従って`confirmed`は配信確定でありoutcome確定ではない契約を維持し、VSCode対応済みとは判定しない。
ただし隔離profileのVSCodeではPrimary Side BarのTogglePattern後にWin32 `PrintWindow`画像差分を確認でき、
単一candidateの最小workflowに限って独立outcome検証付きで成立した。

2026-09-22(Phase D途中2): Electron menuの`IsOffscreen=true`を矩形だけで上書きする案は、
`ElementFromPoint()`が背面要素を返し現在表示を証明できなかったため不採用とした(Astra反証:
`docs/consult/20260921-2316-finitact-uia-visibility-consult.md`)。代わりにQuick Accessからcommand palette相当へ
入るpattern-only経路を確立した。common file dialog対応ではconfigured HWNDのdescendantだけでなく、
同一PID・owner chain内のvisible top-level windowをscopeへ含める。Electron root treeとの重複RuntimeIdは
owned scope優先で除外し、candidate identityと実行時再解決へscope HWNDを含める。読み取り専用
ValuePatternは値の観測だけに使い、fill候補を生成しない。

2026-09-22(Phase D途中3): `OPEN_CONTEXT_MENU`のNotepad live経路を確立した。Phase Aでは一般的な
右クリックを`synthetic_pointer`としたが、native HWNDへ直接送る`WM_CONTEXTMENU`は共有OS入力キュー・
物理カーソル・foregroundを使わないため、Astraレビュー
(`docs/consult/20260922-0332-20260922-context-menu-message-topic.md`)に従い`window_message`へ分離した。
共有環境で許可するのは、UIA elementのnative HWND/PID/矩形を実行直前に再解決し、非同期送達後1秒以内に
同一PID・owner tree内の新規popupを観測した場合だけ。送達acceptだけなら`uncertain`として再送しない。
新規Notepad fixtureのDocumentでpopupから11候補を再観測し、「すべて選択」をInvokeした後、popup消滅と
Win32 `EM_GETSEL`の0..49(本文長50)を独立確認した。証拠はこのNotepad control/経路に限定し、Electronへ
一般化しない。共有input queueを使う通常の`synthetic_pointer`には従来どおりInteraction Leaseを要求する。

2026-09-22(Phase D途中4): 候補値sweepはadapterメソッドを増やさず、UIA
`SelectionItemPattern.SelectionContainer`を候補属性へ載せ、`selection_sweep.py`の小さいinterfaceへ
反復・再解決・停止規則を集約した。動的UIで集合自体を再生成する処理はprepare callback seamとし、
VSCode固有のcommand palette手順を共通moduleへ入れない。隔離profileのColor Theme 14値で14/14 confirmed、
Win32画像の隣接13遷移すべてに非ゼロ差分を確認した。非値actionは集合から除外し、初期集合の順序と
membershipを固定する。曖昧化・消失・uncertain時は当該値を再送せず停止する。

2026-09-22(Phase D途中5): 実Qwen live loopで、outcome達成後もproviderが同じworkflowを繰り返し
action budgetへ到達するfalse continuationを確認した。独立verifierはgoal loop終了後だけでなく初回観測と
各confirmed mutation後に呼び、Trueなら`provider_done`と区別した`outcome_verified`で即時成功終了する。
Falseは未達、Noneは判定不能、例外は検証errorとし、uncertain mutation等の停止を成功で上書きしない。
Astra反証は`docs/consult/20260922-0402-20260922-windows-live-verifier-termination-topic.md`。またWindowsの
operation allowlistは実行直前だけでなくdecision requestの候補集合にも適用し、providerへ選択不能候補を
提示しない。Notepad Select AllはWin32選択範囲、VSCode toggleはWin32画像差分でそれぞれ
`outcome_verified`完了し、2アプリの単発provider loopをlive確認した。

2026-09-22(Phase D Tier 1完了): `SelectionSweepCoordinator`でproviderの役割を多値groupのseed選択1回へ
限定し、全値列挙は固定membershipを決定的に実行する。全値をproviderへ逐次選ばせてexhaustive性を失う
案は採らない。live QwenはActivity Bar 5候補とColor Theme 14候補からtheme groupを選び、14/14 confirmed、
12固有Win32画像hashを得た。Notepad/VSCodeの単発loop、sweep、独立outcome、pattern対応率が揃い、Tier 1の
技術的完了条件を満たした。Tier 2着手はQ-0001の判断待ち。

2026-09-22(Tier 2優先判断): yo-xeはQ-0001のAを選択し、Phase 5 OSS公開準備より先にUnityへ進むと
決定した。最初の対象は`sample-unity-project`のGame view解像度候補で、Tier 1のSelectionContainer sweepを
独自描画Editor chromeへ転用できるかを検証する。ClipStudio/Blenderの順序はUnity完了後に再確認する。

2026-09-22(Tier 2評価軸修正): Unity内部API専用adapterはWindows ActionAdapterのアプリ横断汎用化に
ならないため不採用とした。VSCodeで証明済みなのはUIA pattern fast pathの複数アプリ適用であり、
screen-grounded synthetic経路ではない。後者は対象window frameから有限visual candidateを作る内部seamを
追加し、既存`ActionAdapter`外部interfaceを維持する。Jev等は根拠付き候補間の意味選択だけを担い、座標生成・
再同定・入力安全・成功確定は担わない。fallbackは必要操作をUIAで表現不能と観測時に判定した場合だけ
明示選択し、pattern失敗から自動再送しない。

2026-09-22(Phase E target-scoped pointer評価): Astra反証に基づき、共有input queueを使わないWin32 mouse
message列をUnity GameView解像度dropdownの1回だけ評価した。fresh anchor、同一PID/root配下child HWND、
foreground、button/modifier/capture無しを確認し、MOVE後のframe再確認を経て同一child/client座標へDOWN/UPを
送った。message列は完了したがdropdownは開かずanchorも不変だったためoutcome false。自動再送・SendInput
fallbackは行わず、この経路を汎用click方式には採用しない。次は既存のInteraction Lease契約を変えず、runtime
検証済み専用Windows SessionでSendInput 1 clickを評価する。

2026-09-22(SendInput live成立・目的の再確認): yo-xeがgalleria物理コンソールから離れ接続中と確認できたため
(idle time約91分)、これを唯一の排他性根拠にSendInput 1 clickを実施し成立を確認した(named mutexは
`CreateMutexW`がWSLから呼べず真のlive検証は未完了)。対象window単体の再captureでは見落としたが、対象PIDの
owned popup HWND列挙(`WindowsOwnedWindowEnumerator`、汎用実装)で実際の成功を確認できた。yo-xeから
「本plan・本ADRの目的はUnity対応ではなくGUI操作全般の汎用化であり、Unityは検証車両」と明示的な
軌道修正指摘があった(過去セッションでUnity専用MCPラッパーを作りかけ巻き戻した経緯を踏まえたもの)。
以降のPhase判断は常にこの区別を優先する。EXP-0001(ADR-0007)でStage1候補抽出の一般化に着手した。
<!-- /現況 -->
