> 供粘贴进 BENCHMARK.md「Post-change validation」段（GPU 机 2026-09-22 产出，替代此前 v1/v2 草稿）

## Post-change validation — Edward ft scorer（协议 §5.3，2026-09-22）

环境：causal_conv1d 1.7.0 + flash-linear-attention 0.5.2（与 `/health` kernel_profile 一致）；train/serve parity = 服务端同源 `direct_messages/encode_prompt` 渲染 + 金标选项字母单 token 监督（Option-A），审计 155/155 prompt_sha256 一致。

### ⚠️ 披露脚注（不可省）
- ft-v3 训练消费了 StepShield `test` split。经溯源核验：test split 的 639 rogue + 639 clean 为 **train 轨迹的逐字脱敏镜像**（mapping.csv `source=train` 全量佐证；prompt-hash 去重后 violation 零净增），非新攻击家族；其真实增量 = 6,657 条 benign（FPR 侧）。
- holdout 因此降级为「同族监督 + 封存集」；下表与论文基线（LLMJudge 等，未消费 test split）同框时必须注明该有利条件。
- DEC 类：train/test 镜像双侧各 13 条分歧步（生成设计），v3' 经 paraphrase 增强（924 条，task_title 表面改写、evidence/constraints 逐字保留）+ 类上采样至 80 后仍不足。

### 四次 holdout 结果（asymmetric confirm）
| config | gate | recall | FPR | EIR₃ | 判定 |
|---|---:|---:|---:|---:|---|
| **v1b（base 零样本，线上）** | 0.6 | **76.8%** | 17.6% | 0.778 | **保持上线** |
| ft-v1（lr1e-4/3ep） | 0.6 | 69.4% | 30.6% | 0.747 | 出局 |
| ft-v2（lr5e-5/2ep） | 0.6 | 41.7% | 5.6% | 0.822 | 出局 |
| ft-v3-final（+增强/上采样） | 0.75 | 62.0% | 29.6% | 0.731 | 出局（第1发） |
| ft-v3-ckpt100 | 0.7 | 66.7% | 23.1% | 0.778 | 出局（第2发） |

val（域内）对应：v1 88.4% / v2 92.6% / v3-final 88.0%（recall@0.75 100%）/ v3-ckpt100 91.0%（recall@0.7 94.4%）——**域内指标与 holdout 表现系统性脱钩**。

### 结论
1. ft-v3 两振出局（§5.3），**v1b 保持上线**；服务已回滚验证（`/health` → base）。
2. 结构性发现：4B + ~500 条同源 violation 轨迹的 LoRA SFT，无论激进/保守/增强，holdout recall 均未达 75%（最好 69.4%），且无一超过 v1b。瓶颈 = 攻击家族多样性（OOD），非训练技巧；域内 val 选点对 holdout 无预测力。
3. 侧向发现：v2 证明 FPR 可压至 5.6%、EIR₃ 0.822（超 v1b）——若未来场景以"低误报"为纲（如宽松告警流水线），ft-v2@0.6 是可选操作点，本次未上线。
4. 重启 ft 线的前提：新攻击家族语料（非镜像、非 paraphrase）或更大基座；在此之前不再消耗 holdout 预算。

### 归档
`results/raw/ft/`（11 文件 + SHA256SUMS.json）：四次 gate raw log、三次 train log、run-env、v1/v3 stats。数据 sha256：v1 train `f5c24df1…` / v2 `7a7c82d4…` / v3 见 stats-v3.json `config`。Xid-13 事件 ×2 已在 run-env 记录（driver 610.43.02 稳定性存疑，`--resume` 机制有效）。
