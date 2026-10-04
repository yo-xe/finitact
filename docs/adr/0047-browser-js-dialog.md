# ADR-0047: browserのJS dialogを有限候補として扱う

- 日付: 2026-09-29
- 状態: 採択
- 決定者: yo-xe + Claude Code

## 背景

BUG-0045: `confirm`/`alert`/`prompt`/`beforeunload`が開くとrendererが止まり、run_browserは0/3(E2E-08)。
実Chrome 154のprobe(2026-09-29):

- `Page.enable`済みsessionには開いた瞬間に`Page.javascriptDialogOpening`が届く。main frameなら`frameId == targetId`。
- dialogを開いたclickの`mouseReleased`、`Runtime.evaluate`、`Page.captureScreenshot`は閉じるまで返らない。
  `Target.getTargetInfo`は返る。
- 開いた後にattachした別sessionでは`Page.enable`がhangし、`Page.handleJavaScriptDialog`は"No dialog is showing"。
  開いた時点でPageを有効にしていたsessionだけが閉じられる。

## 決定

- dialogは観測の一種。自tabにdialogが開いていれば、observeはDOMを読まず、本文に種別とmessage(untrusted)を置き、
  候補をdialogの操作だけにする: alert=OK、confirm=OK/Cancel、beforeunload=Leave/Stay、prompt=入力して OK(fill)/Cancel。
  語彙はclick/fillのまま(`dialog`属性で配送先を分ける)で、providerの型は変えない。自動承認はしない。
- 検出: Browserは自sessionで`Page.enable`し、daemonの`pending_dialog`のうち`frameId == target`を自tabのものとみなす。
  入力の配送は別threadで待ち、応答が遅い間にdialogが開けば「入力は実行され、dialogが開いた」として返す(MutationUncertainにしない)。
- 閉じられるsessionを失わない: dialogが開いたままkeepで終わるrunはdetachせず、sessionをtab_idで同一process内に保持し、
  次のrun・observe_browserが同じsessionを使う。
- 閉じた後の処理はページ側が続行する(clickのhandlerがconfirmの戻り値で分岐)ため、通常の観測に戻る。

## 検討した代替案(没案)

- 自動承認(alertとconfirmを常にOK): 削除確認のような判断をprovider・外側から隠す。誤承認は取り消せない。
- dialogでrunを止め外側agentへ別toolで返す: 外側token・往復が増える(速度第一)。有限候補で内側が選べる。
- `Page.setInterceptFileChooserDialog`相当の事前抑止(`window.confirm`上書き注入): ページの挙動を変え、beforeunloadは覆えない。
- iframe内dialog: frameIdがtargetIdと異なり今回は検出しない(従来通りMutationUncertain)。必要になったらframe treeで照合する。

## 影響

- dialog観測ではscreenshotを取らない(hangするため)。達成判定Eはdialog候補を対象外(None)。
- 保持sessionはMCP serverのprocess寿命に依存する。process再起動後に開いたままのdialogは閉じられず、tabを閉じるしかない。

<!-- 現況 -->
2026-09-29: 採択。実装済み(相談`docs/consult/20260929-1311-*`の指摘3点を反映)。dialog状態はsession別・開閉世代で鮮度判定、
入力待ち中のdialogは配送`pending`とし回答後に残りの段を送って`confirmed`(fillは焦点が対象fieldに無ければuncertain、
dragは再開しない)。keepしたtabはsessionを保持する。dialog候補はpage nodeを持たず、Jevへは候補idを要素として渡す。
live: E2E-08 Finitact 3/3・7.9〜13.3秒・外側27k〜54k(`artifacts/e2e-live/e2e08-n3-finitact-dialog-20260929/`)。
影響節のprocess再起動後は「保持sessionを失った後の自動再開は未保証」と読む。同一tabへの並行runの排他は未実装。
<!-- /現況 -->
