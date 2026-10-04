# BUG-0064: C2 E2E-02でFinitact経由の外側agent(claude-sonnet-5-5)が単位million per monthのSQS欄へ42500をそのまま入れ4/5失敗(SQS 17000.00)。同じ外側モデルのwindows-mcpは5/5で0.0425へ換算した

- 報告日: 2026-09-30 / 状態: 修正済み
- 重大度: 中 / 発生コミット: 8ef87a5

## 症状

- 5試行とも外側agentはSQSの単位が million per month だと気付き、"per month" の選択肢を探して失敗した。0.0425へ換算したのは2試行で、うち1試行(1790736968)は入力直後の`final_state.invalid_fields`に「有効な値を入力してください。有効な値として最も近いのは 0 と 1 です。」が出たため42500へ戻した。残り3試行は換算を試みず42500のまま報告した。

## 再現手順

2026-09-30 11:56〜12:05 run_e2e_live.py finitact --scenario E2E-02 --trials 5(artifacts/e2e-live/c2-20260930-E2E-02-finitact)。回答は単位不一致に気付きつつ『指定どおり42500』と報告。前回6/6(bug0039-e2e02-x6)は外側claude-sonnet-5で0.0425へ換算

## 該当箇所

- `finitact/snapshot.js`の入力拒否判定(E2E-I21、commit 4a6e339)。

## 原因

- 誤った拒否報告: snapshot.jsは`:user-invalid`をそのまま拒否とみなしていた。AWSのSQS欄は`type=number`・step属性無し・form外(headless Chromiumで実ページを確認)で、0.0425はブラウザ標準のstep=1に違反するがページは値を保持し見積りも0.02 USDになる。ページが強制しない標準制約を拒否として返したため、正しい換算が取り消された。
- 換算を試みない試行: 外側agentは単位も17,008 USDの見積りも見た上で「指定どおり42500」を選んだ。windows-mcp系も最初は42500を打ち、画面の見積りを見て0.0425へ直しており、「欄を見る前に値を決める」仮説は棄却。主因はハーネスの非対称で、Finitact系のpromptにだけ「Put the exact numbers to type in fill_values」があった(fill_valuesの説明はtool説明に既にある)。これが文字どおりの入力を後押しし、sonnet-5-5で顕在化した。同じHEAD・旧promptで外側をsonnet-5にすると5/5(`bug0064-model-20260930-E2E-02-finitact-sonnet5`、全試行が単位を見た時点で0.0425を入力)。
- 対処: 標準制約はform経由で検証される時(`e.form && !e.form.noValidate`)だけ拒否とし、ページ自身の`aria-invalid`はそのページの文言を優先する。修正後のN=5再計測(`artifacts/e2e-live/bug0064-20260930-E2E-02-finitact`、snapshot.js修正込み)は1/5・中央値105.6秒・478k。誤った拒否報告は0回になり、換算した1試行は成功した。3試行は換算を試みず42500のまま、1試行はサービス一覧が読み込まれず失敗(環境要因)。残る主因は外側agentが換算しないことで、windows-mcp系(同モデル5/5)との差は欄を見る前に値を決めるrunのまとめ方にあると見る。文を削除して両系のpromptをそろえた後のsonnet-5-5 N=5(`bug0064-prompt-20260930-E2E-02-finitact`)は4/5・中央値93.3秒・467k。残る1試行は同じく42500のまま報告したモデル側の判断で、windows-mcp系と同じpromptの条件下での差として扱う。

## 修正

- 2026-09-30 修正済み(コミット 12b553b): snapshot.jsが標準入力制約を拒否と報告していた点(c419aad)と、E2E-02のFinitact系promptだけが『exact numbers』を指示する非対称(run_e2e_live.py)を直した。そろえた後のsonnet-5-5は4/5。
