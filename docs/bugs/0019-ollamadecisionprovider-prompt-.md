# BUG-0019: OllamaDecisionProviderはpromptがnum_ctx(既定8192)を超えるとOllamaが黙って切り詰め、trusted goalやcandidateが欠けたままchoiceを返す。screen観測(Unity 222-329件・Blender 415件、provider_record 45k-84k文字)は既定ctxを超える

- 報告日: 2026-09-24 / 状態: 修正済み
- 重大度: 高 / 発生コミット: 6e0fcba

## 症状

- (未記入)

## 再現手順

qwen2.5:14b-instruct、num_ctx=8192で候補400件(prompt 39588文字)を送るとprompt_eval_count=4098・choice='}'。num_ctx=32768ではprompt_eval_count=17949で正解c0200。100件(9836文字)では8192でも正解

## 該当箇所

- (未調査。当たりを付けるには dev map を使う)

## 原因

- (未調査)

## 修正

- 2026-09-24 修正済み(コミット 74efa18): 原因: Ollamaは既定でnum_ctx超過分を黙って切り詰め、生成中もcontext shiftする。対処: /api/chatへtruncate=false・shift=falseを渡し、超過はHTTP 400(exceed_context_size_error)でfail closed、auditへn_prompt_tokens/n_ctxを記録。Ollama 0.31.2実測: 400候補でctx 8192は400(24927 tokens)、32768で正解。ctxの自動拡大はVRAM方針が要るので行わない
