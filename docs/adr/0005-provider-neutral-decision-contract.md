# ADR-0005: decision providerの共通契約から確率と実行責務を外す

- 日付: 2026-09-21
- 状態: 採択
- 決定者: yo-xe + Codex

decision providerの共通入力はgoal、観測ID、observed candidates、必要な履歴、残attempt budgetとし、
出力は候補IDまたは終端理由と計測に限定する。TypeSafe固有のoperation/target複数head、確率分布、
raw responseはadapter metadataであり、共通実行経路は要求しない。これにより確率を返さないLocal Qwen
やtest adapterも同じinterfaceを満たせる。

候補所属検証、freshness、mutation、run budgetはAgent/action adapter側に残す。text helperも別interfaceとし、
decision providerをlocal化しただけで入力文字列生成までlocal化されたとは扱わない。
