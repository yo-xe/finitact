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

<!-- 現況 -->
2026-09-23: 採択・実装・galleria実機でのlive確認まで完了。badge/reticle/delivering中も
GetForegroundWindow()は対象windowのまま(BUG-0012解消、`--fixed`でクローズ済み)。
scripts/check_windows_mcp_screen_success_live.pyの1click成功caseがpassed=trueで完走
(target-valid-foreground/hit-root維持、click confirmed、independent oracle popup_confirmed=true)。
live実機のpixel-sampleでrender_reticleの幾何バグ(左上以外の3隅が描画されない、corner_lengthの
inset計算漏れ)を発見・修正し、regression testを追加した上で再検証済み。
<!-- /現況 -->
