> 以下内容供粘贴进 BENCHMARK.md「Post-change validation」段（由 GPU 机评测产生，2026-09-22）

## Post-change validation (2026-09-22, GPU box RTX 3080 10GB)

环境：优化 kernel 已装（causal_conv1d 1.7.0 + flash-linear-attention 0.5.2，与服务端 `/health` kernel_profile 一致）。scoring-service v2/v4 封版数字测于参考 kernel，headline 重跑（#2/#4）另行执行。Edward ft 探针按 `lora/FINE_TUNING.md` §5 过门。

### Edward ft gate — attempt 1（FAIL）
- 数据：`lora/data` 重建（Option-A parity 修复：行内新增 `state`，训练渲染改为复用服务端同源 `direct_messages/encode_prompt`，监督目标 = 金标选项字母单 token）；sha256 train `f5c24df1…` / val `a8cb5a1e…`；stats.json 与封版逐字节一致（1911 = 540/1371）
- 训练：lr 1e-4，epochs 3，r16/α32，completion-only（tail-loss CE），grad-ckpt，bs1×accum32；transformers 5.17 兼容 patch ×2（warmup_steps、chat template Encoding）
- serve：merged 权重上 `/v1/score`；**parity 审计 155/155 prompt_sha256 一致**（train/serve 逐字节）
- 门（holdout 216，asymmetric，GATE 0.6）：**recall 69.4% ❌ FPR 30.6% ❌ EIR₃ 0.747 ❌**
- 诊断：OOD 下 FPR 爆炸（val@0.6 FPR 10% → holdout 30.6%）+ 单 token 监督过自信
- raw：`lora/out/gate-ft-probe.log`、`train.log`、`run-env.txt`

### Edward ft gate — attempt 2（FAIL，§5 两振 → 配置放弃）
- 数据：`--max-ok-per-clean 4` 重建（sha train `7a7c82d4…` / val `ba235178…`；stats 仍逐字节一致）
- 训练：lr **5e-5**、epochs **2**（§6 FPR 处方），其余同上；期间 Xid-13 崩溃一次，经 checkpoint-100 `--resume` 恢复
- serve：merged v2；val argmax **92.6%**（v1 88.4%）；val@0.6 recall 78.3% / FPR 1.4%
- 门（同协议）：**recall 41.7% ❌ FPR 5.6% ✅ EIR₃ 0.822 ✅**
- 结论：FPR/EIR₃ 超越 v1b，但 recall 塌陷（val 78.3% → holdout 41.7%）——OOD 泛化边界（§8 已预判）。**维持 v1b 基线上线**；门槛 0.6 未动（无重标定需求）
- raw：`lora/out/gate-ft-probe-v2.log`、`train-v2.log`
- 回滚验证：`/health` → `Qwen/Qwen3.5-4B`，kernel_profile `model_source=qwen3.5-4b-base`

### 环境备注
- 今日两次 **Xid 13**（10:17 serving/参考 kernel、20:33 training/fla kernel）——driver 610.43.02 稳定性存疑，建议评估降级；serving 侧已有 CUDA 自愈（systemd 拉起），训练侧建议保留 `--resume`
- 若将来重启 ft 线：先解决 recall 的 OOD 问题（UNSURE 校准数据 / violation 类上采样 / 更长训练），勿再直接烧 holdout
