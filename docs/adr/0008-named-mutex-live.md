# ADR-0008: named mutex live検証の実行形態

- 日付: 2026-09-22
- 状態: 採択
- 決定者: yo-xe + Claude Code

## 背景

`WindowsNamedInteractionLease`(`windows_interaction_lease.py`)は`CreateMutexW`をctypes経由で
ネイティブ呼び出しするため、finitactのPythonプロセス自体がWindows上で動いていないと呼べない。
現行のfinitact(MCPサーバー含む)はWSL上でPythonが動き、Windows操作はPowerShellサブプロセス経由
(`PowerShellBridge`)に限定している。この構造のままではnamed mutexの真のlive検証を進められない
(docs/QUESTIONS.md Q-0002として提起)。

## 決定

named mutexのlive検証は、galleria実機にWindows-native Python環境を別途用意し、検証専用の
最小スクリプト(または将来的にfinitact本体)をそこから起動して行う。既存のPowerShellBridge経由の
他機能(observe/act等)はそのままWSL側で動かし続け、named mutex検証だけをWindows-native側の
独立した検証として位置づける。既存アーキテクチャ(WSL上のfinitact本体+PowerShellBridge)は変えない。

将来の外部公開を見据え、finitactはWindows-native実行とWSL実行の両方で動くツールであることを
設計原則とする。`WindowsNamedInteractionLease`の`os.name != "nt"`チェックはこの原則を体現しており
(WSLでは明示的に`OSError`で拒否し、黙って壊れた状態にしない)、今後の実装もこの二重動作を前提に
書く。具体的には、Windows-native実行時は`WindowsNamedInteractionLease`を直接使い、WSL実行時は
(a) 従来通りsynthetic inputを提供しないか、(b) 将来的にPowerShell常駐プロセス等でWSLから
`CreateMutexW`相当を呼べる経路を別途検討する、のいずれかを状況に応じて選べるようにする。

## 検討した代替案(没案)

- **B. finitact全体をWindows-native実行に切り替える(WSLをやめる)**: PowerShellBridgeの往復コストや
  文字コード変換問題(BUG-0006等)も解消できるが、大きな構成変更になり、exclusive_environment_refの
  形式(Q-0003)等の未決事項も一緒に決める必要が出る。外部公開時にWSL環境のユーザーを排除することにも
  なり、「両方で動く」という将来要件と矛盾するため却下。
- **C. named mutex検証を諦め、代替の排他性根拠(セッションidle time等、Phase Eで実際に使った代替)を
  正式な契約として採用する**: 追加作業は不要だが、ADR-0006が要求する「runtime-verified exclusive
  environment」の構造的保証を満たさないまま運用することになり、fail-closed設計の前提が崩れるため却下。

## 影響

- named mutexのlive検証には、galleria実機側でWindows-native Python環境のセットアップ(yo-xe側の
  作業)が要る。セットアップ完了後、検証専用スクリプトを用意して実施する。
- `exclusive_environment_ref`の形式(Q-0003)は本ADRでは未決のまま。Windows-native実行時の
  `WindowsInputScope`(session_id/window_station/desktop)組み立て方法とあわせて別途決める。
- WSL側のfinitact本体・MCPサーバーの構成は変更しない。

<!-- 現況 -->
2026-09-22: 採択・live検証完了。galleriaには既にWindows-native Python 3.12.10+uv+pywin32が
入っておりセットアップ不要だった。`WindowsNamedInteractionLease`を実session_id/window_station/
desktopから構築したscopeでlive実行し、acquire/release往復・保持中の2つ目のacquireが実際に
ブロックする相互排他性・release後の再利用可能性を確認した(検証中にBUG-0007を検出・修正、
詳細は`docs/evaluations/windows-adapter-prototype/phase-e-screen-grounded-seams.md`)。
残りはexclusive_environment_refの意味づけ(docs/QUESTIONS.md Q-0003)。
<!-- /現況 -->
