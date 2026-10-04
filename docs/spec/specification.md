# Finitact 仕様書

> 種別: 対外仕様書(固定断面)
> 最終更新: 2026-09-25
> 本書は「現在の正」のみを記す。経緯・没案は docs/adr/ を参照。

## 1. 目的

観測済みの有限候補から次の操作を選ぶ、小さく交換可能な判断・実行層を提供する。
現行実装では、変化するWebページとWindows GUIを、サイト・アプリ固有の操作toolや
モデル生成コードに依存せず、観測済みの有限候補へ落として操作する。

## 2. スコープ

- 現行でやること: MCPまたはlibrary APIから1件以上の順序付き自然言語ゴールを受け、browserまたは
  Windows GUIを観測し、有限候補を作り、交換可能なdecision providerで候補IDまたは終端理由を選ぶ。
- browserとWindowsは同じ判断ループ(予算・判断の一回消費・再観測・送信確実性・記録)を通り、候補源・鮮度と再束縛・
  送信と安全門・判断要求の形・証拠の解釈だけが経路別(ADR-0037)。browserの旧Agentループは`FINITACT_BROWSER_LOOP=agent`で戻せる。
- browserとWindows UIAの候補は、scroll領域(またはページ)に隠れているだけの要素も領域の高さ1つ分以内・近い順40個まで含め、どちら側かを付ける。送信前にscrollして可視・最前面を確かめる(ADR-0038)。
- browserの操作後は部品の期待状態(checkboxの反転・radioの選択・selectの値・fillの値)を、Windowsのclick後はUIA Toggle・SelectionItemの期待状態を照合し`met/not_met/unknown`を履歴に、`not_met`をgoal結果の`unmet_effects`に事実として載せる。`not_met`は同じ観測状態・同じ入力の間だけ再提示しない。拒否理由は推論しない(ADR-0039)。
- browserのrun結果は、終了時のページ要約`final_state`(URL・タイトル・開いているダイアログの文言・入力を拒否された欄と理由・表示文字の先頭40行/2000字)を持つ。例外で止まったgoalのdetailは型と短い理由(E2E-I27・I28)。
- STDIO MCPは`run_browser`、`run_windows`、`cancel_run`、`get_run_journal`、`list_windows`、`observe_window`、`observe_browser`を公開する。run ID、deadline、
  action/provider budget、許可origin/operationを必須境界とし、同一requestの完了済みrunは再送信せずreplayする。
- `TYPE_TEXT` のときだけ text LLM を呼び、構造化された短い文字列を生成する。
- ローカル inspector、実行例、計測・デモ成果物を提供する。
- Windowsの外側向けtargetは`window:<HWND>:<PID>`の1種類。合成入力許可(既定true)ならscreen-grounded経路、
  falseならpattern-only UIA経路で、run途中で経路を変えない(ADR-0040)。旧`uia:`/`screen:`も受ける。窓タイトル
  (完全一致優先・一意な部分一致、曖昧なら候補付きで拒否)も受けて`window:`へ解決する(ADR-0050)。
- Windows goalが`unverified`で送信確定なら、goal開始・終了のlabel差分`screen_changes {gone, new}`を返す(ADR-0050)。
- `drop_target_id`(窓タイトルか`window:<HWND>:<PID>`、dragと合成入力が前提)で、dragの終点だけを別窓へ置ける。drop窓はdrag終点の選択時にだけ観測し、他の操作・成功判定は対象窓のまま(ADR-0045)。
- browser経路(`run_browser`)のdragは`extra_operations=["drag"]`でopt-in。開始候補→終点候補の2段で、mouse eventとHTML5 DnDの両方を送信する。成功判定は対象外(ADR-0046)。
- screen-grounded synthetic inputは明示opt-inかつWindows-native限定とし、named mutex、idle事前条件(既定1秒、`FINITACT_MIN_IDLE_SECONDS`で変更。ADR-0032)、
  readiness確認済みindicator、fresh再観測、foreground/hit-testを全て通過した場合だけ送信する。
  mutexとidle確認はrun単位(初回送信で取得・確認し、run終了まで保持)、indicatorの送信表示は送信ごと(ADR-0012)。
  前runの終了後に最終入力tickが変わっていなければ、idleはそのrunが確認した起点から測る(ADR-0021)。
- screen経路の観測範囲はrootと、owner chainがrootへ届く可視owned popup。popup候補は所属HWNDに束縛し、
  hit-testは所属HWND、foregroundはrootまたはそのpopupに限る。
- やらないこと: サイト固有 plan、固定 field value、selector・座標・shell command・
  executable JavaScript のモデル生成、UIAからscreen経路への暗黙fallback、予約・購入、
  オフラインテストからの有料 API 呼び出し。

## 3. 確定事項

- decision providerの共通入力はgoal、観測ID、observed candidates、必要な履歴、残budgetとする。
  共通出力は候補IDまたは終端理由と計測であり、確率分布・複数head・raw responseは任意metadataとする。
- decision provider、text helper、action adapterを別の交換点とし、候補所属検証、freshness、mutation、
  run budgetはproviderの外で強制する。
- Windows runでは完全一致するdecision requestだけをprocess-local cacheで再利用できる。cacheは明示opt-inで、
  hit時もfreshness、mutation、post-observation、outcome検証を省略しない。mutation/outcomeはcacheしない。
  goal結果はprovider attempt数とdecision cache hit数を別々に返す。
- Windows runで判断のconfidenceが非終端の選択で0.4未満、`BLOCKED`で0.6未満の時は操作せず`provider_uncertain`で
  終え、確率上位5件の候補(ref・label・rect・popup、あれば現在値。untrusted、形状だけのedge候補は除く)を`screen_candidates`で返す。
  その観測が窓の最新観測になり、呼び出し側は最初のgoalの`ref`でその候補を直接指定するか、goalを言い換える(ADR-0019/0020/0043)。
- `observe_window`は各項目の`ref`を返し、その観測が窓の最新観測になる。最初のgoalの`ref`は窓の最新観測(observeまたは
  uncertain runの終端)を指し、runの最初の1手を判断器なしでその項目にする。observe項目は最初のgoalにfill値があればfill、
  無ければclickへ解決し、操作が1つだけの項目(run候補)はその操作。無い・許可外の操作は`blocked`。refは窓ごと最新1件・
  60秒以内・同じ窓の次のrun開始までで1回限り、窓が変わっていれば操作せず`blocked`(ADR-0041/0042/0043)。
- Windows runはgoal IDごとに任意の`click_label_constraints`を受ける。指定時は完全一致するclick候補が一意な時だけ
  その候補を判断器へ提示・送信し、他のclick、近似label、同名複数候補を拒否する。自由文goalからは推定せず、pickにも
  同じ制約を引き継ぐ。この制約は送信対象を限定するだけでoutcomeを検証しない(ADR-0024)。
- Windows runはgoal IDごとに任意の`fill_values`を受ける。指定goalのfillはtext helperを使わずその文字列を
  そのまま送信し、goal文と食い違えば構造化値を優先する。欄の選択は判断器が行い、値は判断器にも渡す。pickは同じ値だけ
  受け入れる(ADR-0026)。
- Windows runはgoal IDごとに任意の`selection_policies`(`find_and_click`、同goalのclick制約必須)を受ける。一意な制約
  labelが見えればclick、見えず単一list塊ならdown→無変化→up→無変化の順にwheelし、ここまで判断器を使わない。複数塊は
  判断器へ戻し、click 1回後は判断器の`done`以外を`blocked`で返す。両端で未発見なら`blocked`(ADR-0027)。
- Windows runは任意の`app_annotations`(外側がアプリ固有手段で得た事実)を受ける。run初回観測で正規化label完全一致が
  1領域(subject+rect)に限られる時だけ、その領域の全候補の`attributes["app"]`として判断器へ渡す。曖昧なら付けない(ADR-0053)。
- Windows runはgoalごとに任意の`app_expect={source,key,equals}`を受ける。操作0.3秒後以降に書かれたfeedの値が一致した
  時だけ`outcome_verified`とし、最大2秒待つ。不一致・古い・欠落は判定せず通常の判定へ戻す(ADR-0053)。
- 対応操作は `CLICK`、`TYPE_TEXT`、`SELECT`、`SCROLL_UP`、`SCROLL_DOWN`、
  `WAIT`、`DONE`、`BLOCKED`。
- target は同じ観測内の要素と対応し、選択操作に一致する target head だけを消費する。
- browser mutation は再試行せず、実行を記録してから結果を観測する。
- mutation intent は browser input 前に記録する。成功は `confirmed`、入力前 stale は
  `not_attempted`、結果を確定できない例外は `uncertain` としてrunを停止する。
- model providerへのlogical callと各HTTP attemptを分離して記録する。記録対象はprovider、model、
  request hash、question ID、HTTP status、latency、retry状態とし、credentialとrequest本文は保存しない。
- TypeSafeとtext helperのretry・失敗を含むHTTP attemptをrun単位で合算し、120回へ達した後は
  次のrequestを送らず`BLOCKED`相当で停止する。
- iframe、open shadow root、nested scroll、popup link、canvas、file inputの可視件数を
  `page.unsupported`として判断stateへ渡す。内部・clip外・別tabの要素は操作候補へ入れない。
- stale retry の text value は text helper の入力全体が一致する間だけ再利用する。
- `DONE` 選択とは別に、例ごとの成功条件を実ページ状態から検証する。
- screenshot は inspector / 記録用の任意機能であり、モデル入力にしない。
- Windows mutationの`confirmed`は送信成立であってgoal成功ではない。独立outcome verifierがない結果は
  `unverified`のまま返し、同一UIA再観測だけを独立検証とは呼ばない。
- screen pathのclick・fill・scrollは、操作前後のframeで効果が対象へ届き(click・fillは`landed_conn`、scrollは枠内の2行以上が
  共通offsetで走査方向へ動くか全行入替)かつJevが操作をgoalに適合と答えた時だけ`verified_success`にする。
  clickはさらに、対象位置の文字状態の置換・owned popup出現・閉じたpopupの値反映など具体的な終状態が要る。
  画素変化だけのclickは選択状態を示さないため`unverified`にする(ADR-0030追記4)。
  届かない・不適合は`unverified`で、`verified_failure`にはしない(ADR-0030追記1、ADR-0031)。
- screen-grounded候補はcapture由来のregion、根拠、矩形、frame/anchor fingerprintを持ち、送信直前に
  同一scopeで一意に再同定できない候補を拒否する。regionは既定でPP-OCRv6 medium(x64はOpenVINO、他はONNX
  Runtime)のtext領域と、中心がtext領域外にあるedge矩形の合併で、OCRにはoptional extra `screen`が要る(ADR-0016、ADR-0018)。
- synthetic deliveryが不確実になったrunは自動再送しない。lease放棄・release失敗・送信後例外で
  blockedになったinput environmentは、明示的な復旧なしに次runでも再利用しない。

## 4. 非機能要件

- Python 3.12 以上。認証情報は `.env` 等の server-side 設定に置き、追跡しない。
- オフライン品質ゲートは Ruff、pytest、JavaScript 構文検査、wheel/sdist build。
- DOM/ARIA の一部を対象とし、shadow root、frame、canvas、upload、popup tab、nested scroll、
  任意の keyboard widget はbrowser MVPの保証外。
- Windows UIA fast pathはNotepad・電卓・VSCodeの成立済み操作に限定し、VSCode editor fillやElectron menuを
  一般対応とはしない。screen-grounded pathの操作候補は、COM UIAが名前付き・画面内の要素を返す窓ではそれ(click系role、fillは編集可能根拠のあるもの、現在値は別)から作り、名前付き画面内要素が覆わない24px以上の非空白領域だけをOCRした候補を加える(中心がUIA要素内のものは捨てる)。再同定は源ごとで、UIA候補はUIA再読、OCR候補は領域OCRで一意に行う(ADR-0044)。UIAが答えない窓はOCRで作る。成功判定の画面証拠はOCRのまま必要時に取り、確定操作の後は画面が変わっていれば0.3秒間隔で最大2回再観測して判定する(ADR-0036)。screen-grounded pathはclick・double_click・right_click・middle_click・hover・ctrl_click・shift_click・fill・allowlist key(window内で意味が閉じる60種、Win系・Alt+Tab・Alt+F4は除く)・scroll・dragを送信し、各pointer操作は`allowed_operations`で要求された時だけ候補にする。送信直前にtarget root windowを前面化してから前面・hit-test検証を行う(ADR-0033)。`run_windows`は`target_id`とgoalsだけで呼べ、省略時はrun_id・goal id(g1…)・操作(screen: click・fill・key・scroll、UIA: click・fill・toggle・select)・合成入力許可(screenのみ、ADR-0009の3層は毎送信)が補われる(ADR-0034)。key・click・double_clickの後は、対象に結び付いた終端証拠(押したlabelを名に含む新しい前面窓の出現、goalのfill文字の別位置への移動)とJevの完了判断(既済操作つき)がそろえば`verified_success`とする(ADR-0035)。MCPは読み取り専用の`observe_browser`(`keep_tab`のtabを入力なしで読み、候補の種類・label・状態(入力が拒否された理由、画面外)と
表示文字を返す)と、`list_windows`(可視top-level窓のhwnd・pid・title・process・class)と`observe_window`(targetの現在labelを入力なしで返し、フォーカス中入力欄のcaret行の項目に`in_focused_input`を付ける)も提供する。scrollは縦に連なるtext region塊ごとのup/downで、wheel後に画面が変わらない方向は候補から外す。dragはtext由来の始点を入力なしで選んだ後、同じ窓の観測源(UIA窓ではADR-0044の源)とedge終点とのcompositeを1 actionとして両端を同じ源で再同定・hit-testし、down・有限個の中間move・upを一括送信する。fill候補は一様色panelに属する文字領域をpanelごとの1候補(rect=panel、入力点=文字の無い内部cell)に束ね、属さない領域は文字crop単位で出す(ADR-0029)。同じ行で右隣の領域と1対1に対応する末尾`:`の短い文字(例`Name:`)は、その領域のfill候補のlabelとし、領域の文字は現在値(untrusted)として渡す(入力点は領域内のまま)。captionは単独のfill候補にしない(BUG-0034)。click前後のMSAA caretが同位置のまま候補rect外ならfillはCtrl+A・貼付を送らず、mutation不確定とする。入力環境はblockしない(caret取得不可なら送る。BUG-0033・0036、ADR-0033)。fillはclipboard経由の貼付で、
  clipboardを入力値で上書きし復元しない(貼付後に対象が非同期で読むため)。clickはBlender・Unityの有界1〜2 clickで
  windows-mcpと同条件比較済み(置換範囲は`evaluations/windows-mcp-replacement/phase-i-comparison.md`)。
- `run_windows`の`routing=browser_if_singleton`(または引数省略時の`FINITACT_WINDOW_BROWSER_ROUTE=1`)は、ローカルCDPのbrowser PIDの表示窓1件(target窓所有のtool window popupは数えない)・page target1件・title/URL一致・visibleを読み取り専用で確かめた時だけ、1 goalのpage操作をそのtabのbrowser経路(click・fill・select・scroll・wait、originは当時のURLのみ)で実行し`routed`を返す。それ以外は画面経路。経路は`run_id`ごとに共通受付台帳へ固定し、再送で再判定せず、別経路への自動再送もしない。ページ内のframe等は経路条件にせず、到達外は`final_state.out_of_reach`で返す(ADR-0051)。
