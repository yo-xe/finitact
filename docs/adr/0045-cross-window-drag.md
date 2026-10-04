# ADR-0045: 窓間dragをdrag限定の2窓契約で扱う

- 日付: 2026-09-29
- 状態: 採択
- 決定者: yo-xe + Claude Code

## 背景

E2E-05でFinitactは窓間dragができず、外側agentが切り取り・貼り付けで代替した(3/3成功、165.7〜373.1秒)。
相談は`docs/consult/20260928-2051-*`。1配送に2つの宛先が要る操作はdragだけで、他の2窓操作は窓ごとのrun分割で足りる(yo-xeと確認、2026-09-29)。

## 決定

- `run_windows`に`drop_target_id`(`window:<HWND>:<PID>`)を追加する。`allowed_operations`にdragが要り、`synthetic_input_allowed`がfalseなら受けない。requestのfingerprintに含め、未指定時は従来値を保つ。
- drop窓は`drag_end_observation`の中でだけ観測する。終点候補(scope=`drop_window`)だけを作り、通常観測・click/fill/key/scroll候補・達成判定Eは1窓のまま。
- 鮮度: drop窓はidentity(HWND・PID・寸法)で、終点は配送直前の再同定で判定する。drop窓の無関係な再描画でstale扱いにしない。
- 配送: 開始窓だけ前面化し、終点は自窓の座標で変換してhit-testが指定drop窓に一致することを要求する。開始窓の前面化でdrop点が隠れる配置はbutton-down前に拒否する(左右に露出する配置に限定)。
- 安全境界: drop窓にも保護窓のtarget guardを適用する。第2窓を`owned_popup`として偽装しない。drop窓がrun自身のscopeと重なる指定は拒否する。
- lease・idle門はrun単位のまま。indicatorは開始窓の始点に1つ出す(reticleが共有overlayのため)。

## 検討した代替案(没案)

- 案1(扱わない): E2E-05は終状態に達したが、速度面の十分性を示せない。
- 案3(複数target・scope和集合): 観測・候補・鮮度・Eの全部が複数窓化する。E2E-05だけでは正当化できない。
- 終点候補だけ足して観測はrootのまま: 配送前の鮮度・宛先確認が両端に要るため成立しない。

## 影響

- drag中の前面変更は扱わない(現行dragも配送中の画面変化は見ない)。
- dragのEは従来どおり判定なし。窓間移動の達成証明は未対応。
- live未確認。E2E-05のdrag有りとdrag無しの比較で速度への寄与を測る(次の一手)。

## 追記1(2026-09-29): live結果

E2E-05をdrop_target_idで再計測、N=3・3/3(`artifacts/e2e-live/e2e05-drop-20260929{,-b}/`)。40.2〜62.6秒・外側75k〜170k、
全回dragで移動(コピーでない)。従来のcut/paste代替(165.7〜373.1秒)より速い。他の窓間操作型は未検証。

## 追記2(2026-09-29): 回帰確認

単体544件合格。同一窓dragはlive probe(`check_windows_mcp_screen_drag_live.py`)とPhase I `screen-tk-drag-drop-001`(N=3・3/3・7.4〜9.1秒)で回帰なし。
外側tokenは基準(2026-09-25)比+約4kだが原因は露出tool 4→7(先頭turn入力 3.9k→6.0k)で、`drop_target_id`の寄与は`run_windows`schema +261字のみ。
