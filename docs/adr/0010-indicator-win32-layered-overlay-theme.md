# ADR-0010: 可視indicatorをWin32 layered overlayと差し替え可能なthemeで実装する

- 日付: 2026-09-23
- 状態: 採択
- 決定者: yo-xe + Claude Code

## 背景

ADR-0009層3のindicatorはTk windowで実装したが、表示直後にforegroundを奪い、
`target_is_valid`をfail-closedにしてscreen:1click成功caseを止めた(BUG-0012)。
Tk on Windowsは`winfo_id()`が内側の子HWNDを返し、表示(map)後にしかstyleを付けられない。
このため`WS_EX_NOACTIVATE`の付け先と付ける時機の両方が不確かになる。
Windows-MCP(`windows_mcp/desktop/flash_overlay.py`)も同じ理由でTkを捨てている。
あちらは生成時に`WS_EX_LAYERED|TRANSPARENT|TOPMOST|TOOLWINDOW|NOACTIVATE`を指定し、
`SW_SHOWNA`で表示するlayered windowを使う。

## 決定

- **描画基盤**: Win32 layered windowをctypesで直接生成する。
  - 生成時に`WS_EX_LAYERED|WS_EX_TRANSPARENT|WS_EX_TOPMOST|WS_EX_TOOLWINDOW|WS_EX_NOACTIVATE`を指定する。
  - 表示は`SW_SHOWNA`と`SWP_NOACTIVATE`で行い、描画は`UpdateLayeredWindow`とpremultiplied BGRAで行う。
  - foregroundを取らず、クリックとhit-testを素通りさせる。
  - 実行形態はsubprocessではなく、プロセス内の専用threadと、そのthreadのmessage loopにする。
    親プロセスと一緒に消えるので表示が画面に残らず、BUG-0011のstdin継承問題も構造的に消える。
- **表示要素**
  - A(隅のバッジ): run全体で控えめに常時表示する。synthetic inputを許可したrunで、
    adapter生成時に表示し、`close()`で消す。
  - C(target reticle): 配送のたびに表示する。
- **reticleの表現**: target矩形の形状に合わせたAdaptive Corner Frameにする。
  - 四隅の短いL字だけを、矩形の4〜6px外側に置く。円や十字は使わない。
  - target確定時に背景光がゆっくり立ち上がる。
  - 形状は共通にし、action typeの違いはmotionで表す(animation grammar)。
    click=四隅が一瞬収縮、type=左→右に細い光が走る、select=上下へ軽く展開、wait=低周波パルス。
- **言語非依存**: 既定themeは文字に頼らない。文字はthemeの任意要素とする。
- **差し替え可能性**: 見た目をhard-codeしない。
  - 描画はOS非依存の純粋関数(Pillow)とし、theme(色・寸法・motion)を丸ごと差し替えられる形にする。
  - Win32側は、描画結果をlayered windowへ流すだけのhostに徹する。
- **配送との順序**: `resolve_target` → reticle表示 → `verify_delivery_target` → send。
  - 宛先検証を、reticleが見えている状態で行う。foregroundとhit-rootが保たれることを実機で確認する。
  - readiness契約(ADR-0009追記)は維持する。配送は、バッジの表示を確認してから行う。
- **OS可搬性**: 非Windowsの表示は未開発とする。その旨をmodule docstringと仕様に明記する。
  Windows-MCPの見た目は移植しない。あちらはスクショ→操作の流れ向けで、finitactの配送単位の流れには合わない。

## 検討した代替案(没案)

- Tkのまま、GA_ROOTで解決したtop-level HWNDへ`WS_EX_NOACTIVATE`を後付けする
  - 表示の瞬間に既に活性化していれば、後から付けても戻らない。付ける時機の問題が残るため却下した。
- 層3の廃止
  - 無人live試行では告知相手がいない。ただし人が画面の前にいる運用を見据え、yo-xeが移植方式を選んだ。
- 画面縁の光(B)
  - 隅のバッジ(A)とtarget reticle(C)の組み合わせを採った。
- 円形reticleと文字ラベル(「クリック: 保存」)
  - 控えめさに欠け、空間との対応が読みにくく、言語に依存するため却下した。
- `WDA_EXCLUDEFROMCAPTURE`でcapture対象から外す
  - captureは対象窓の`PrintWindow`で、overlayは写らないため不要とした。

## 影響

- `finitact/automation_indicator.py`は全面的に置き換わる。
  - Tk、subprocess、`READINESS_TOKEN`を廃止する。
  - `tests/test_automation_indicator.py`も書き直す。
- `windows_interaction_lease.py`の`indicator_factory`は、run単位のindicatorに対する
  「配送中」contextを返す形へ変わる。
- `ScreenGroundedAdapter`はindicatorを保持する。配送前にreticleを出し、`close()`でindicatorを消す。
- 既定themeの依存としてPillowを実行時依存に加える(現状はdev groupのみ)。
- BUG-0012は本ADRの実装とlive確認をもって閉じる。

## 追記1(2026-10-04): バッジを状態で動くmascotにする

- yo-xeの依頼(愛着が持てて軽量、状態に応じて変化)により、A(隅のバッジ)を18pxの静止点から44pxのmascotへ替えた。
- 描画は既存と同じPillowの純粋関数(`render_mascot`)とWin32 layered windowで行う。CSS/SVG・Rive・Lottieは
  WebView等の実行環境が要り、前面を取らない保証(本ADR)を崩しうるため採らない。1フレーム約0.5ms、20fps。
- 状態はadapterが`phase()`で渡す: `observe`(観測中、視線が左右)、`think`(観測後〜次の操作、provider判断待ち。
  頭上の3点が揺れる)、操作名(click=つぶれ、type=小刻み、scroll=視線上下、select=跳ね、drag=傾き)。
  文字は使わない。点滅させず、動きは3Hz未満にする。
- 位置は主画面の作業領域の右下にし、タスクバーと重ねない。送信ごとの明度変化(`delivering()`)は廃止し、reticleは維持する。
- 見た目はyo-xeの採否待ち(試作。Windows実機で表示・状態変化を確認済み)。

## 追記2(2026-10-04): mascotを藍色のオーブにする

- yo-xeの指示で追記1の顔付きmascotを替えた: 目は無くし、境界のぼやけた明滅するオーブにする。生物的な動きは残す。
- `render_orb`(numpy+Pillow、48px、1フレーム約0.25ms)。halo(ぼやけた縁)と丸いcoreを重ね、縁はsin和でゆっくり揺らぐ。
  全状態で呼吸(0.28Hz)と明滅を続け、中心がわずかに漂う。
- 色は藍色を基調にし、状態で縁の色味を変える: observe=青寄り、think=菫寄り、操作=明るい青。変化は0.5秒で補間する。
- 状態ごとの動き: observe=内部の光点が巡回、think=波紋が外へ広がる、click/key=二拍の鼓動、type=縁が細かく震える、
  scroll=光点が上下、select=浮き上がる、drag=横に伸びる。
- 見た目はyo-xeの採否待ち(Windows実機で表示を確認済み)。

## 追記3(2026-10-04): オーブを不定形・64pxにし、見た目を環境変数で変えられるようにする

- yo-xeの指示で既定を64pxにし、中心の周りを漂う3つの葉(lobe)を合成して輪郭を不定形にした。1フレーム約0.26ms。
- `FINITACT_INDICATOR_*`で外から変えられる: `SIZE`(32〜160px)・`CORNER`・`MARGIN`と、
  色`COLOR_CORE`・`COLOR_IDLE`・`COLOR_OBSERVE`・`COLOR_THINK`・`COLOR_ACT`・`COLOR_RETICLE`(`#RRGGBB`)。
  既存の他の`FINITACT_*`設定とともに`.env.example`へ既定値つきで列挙した。
- 不正な値はrun開始時に変数名つきの`ValueError`で拒否する(`FINITACT_MIN_IDLE_SECONDS`と同じ扱い)。
  入力を送る前に止まるので、誤設定で表示の無いまま操作することはない。

## 追記4(2026-10-04): 外部providerを別の光点で示し、reticleをオーブと同じ表現にする

- Jevは Finitact の外部である(Agent→Finitact→Jevの流れ)。yo-xeと検討した3案(衛星・流れの帯・色分け)から衛星案を採った。
  - オーブの左上に琥珀色の小さな光点(provider)を置く。普段は暗く、要求の送信中だけ光る。
    送信時はオーブから光点へ光が飛び、応答時は光点からオーブへ戻ってオーブが明るくなる。
  - 検知は`model.post_json`(外部modelへの全HTTP呼び出しの入口)で行い、`provider_activity`のcontext変数で
    そのrunのindicatorへだけ伝える。並列のheatにも届くよう、worker threadへcontextを複製する。
    判断・達成判定・TYPE_TEXTのいずれも外部modelなので光る。Jevを使わないgoal(`ref`・`find_and_click`)では光らない。
- reticle(入力対象の囲み)を四隅のL字から、対象の角の外を通る超楕円の柔らかい光の輪に替えた。ADR-0010の
  「円や十字は使わない」を本追記で置き換える。内側は透明のまま対象の文字を隠さない。長辺のはみ出しは14pxまで。
  色は既定でオーブの`act`色に従う。操作ごとの動き(click=鼓動、type=光が左→右、select=膨らむ、wait=明滅)は維持する。
- 既定の大きさを96px(範囲48〜192)にした。`FINITACT_INDICATOR_ANIMATION=0`で動きを止め、状態が変わった時だけ
  静止画を描き替える。表示そのものは安全層(ADR-0009層3)なので消せない。`FINITACT_INDICATOR_COLOR_PROVIDER`を追加した。
- Windows実機で一連の流れ(観測→provider往復→操作とreticle)を表示して確認した。見た目はyo-xeの採否待ち。

## 追記5(2026-10-04): reticleも小さなオーブにする

- yo-xeの指示で、追記4の光の輪をやめ、対象の中心付近に浮かぶ小さな半透明のオーブにした。位置はおおよそでよい。
- 描画はコーナーのオーブと共通(`render_orb(solo=True)`、光点なし)。大きさは対象の短辺から決め、44〜96px。
  不透明度0.75で対象の文字は透けて読める。色と操作ごとの動きはコーナーのオーブと同じ文法に従う。
- Windows実機で一連の流れを表示して確認した。見た目はyo-xeの採否待ち。

## 追記6(2026-10-04): 操作ごとに動きを分ける

- reticleとコーナーのオーブは、実際の操作名で動く(以前はclick系7種がclick、key・fillがtype、scrollがselectに丸まっていた)。
- click=一拍の鼓動、double_click=二拍、right_click=鼓動のあと右下へ小さくはみ出す、middle_click=鼓動と上下の伸び、
  ctrl_click/shift_click=鼓動と寄り添う粒(ctrlは時計回り、shiftは逆回り)、hover=鼓動せず漂う、fill=縁が震える、
  key=縮んで一度光る、scroll=光点がスクロール方向へ流れる、drag=終点の方向へ尾を引いて伸びる(別窓への
  dropでは向きを付けない)、set_range=横に伸び縮みする。色は増やさず、動きだけで区別する(yo-xe了承の割り当て)。
- dragのreticleは始点の上に出す(以前は始点と終点を囲む矩形)。Windows実機で全13操作を表示して確認した。

<!-- 現況 -->
2026-09-23: 採択・実装・galleria実機でのlive確認まで完了。badge/reticle/delivering中も
GetForegroundWindow()は対象windowのまま(BUG-0012解消、`--fixed`でクローズ済み)。
scripts/check_windows_mcp_screen_success_live.pyの1click成功caseがpassed=trueで完走
(target-valid-foreground/hit-root維持、click confirmed、independent oracle popup_confirmed=true)。
live実機のpixel-sampleでrender_reticleの幾何バグ(左上以外の3隅が描画されない、corner_lengthの
inset計算漏れ)を発見・修正し、regression testを追加した上で再検証済み。
<!-- /現況 -->
