# 容量与资源规格

基准命令：

```bash
python backend/benchmarks/run_benchmark.py --scenario health --concurrency 20 --requests 500 --warmup 100 --workers 4 --dataset-label acceptance --output-json reviews/capacity/health.json
python backend/benchmarks/run_benchmark.py --scenario db-read --concurrency 20 --requests 500 --warmup 100 --workers 4 --dataset-label acceptance --output-json reviews/capacity/db-read.json
python backend/benchmarks/run_benchmark.py --scenario review-submit --concurrency 10 --requests 100 --warmup 20 --workers 4 --dataset-label disposable --output-json reviews/capacity/review-submit.json
```

测试必须在独立环境分别执行健康检查、数据库只读查询、审批提交及 LLM 流式场景，记录
吞吐、错误率、P50/P95/P99、CPU、内存、数据库连接和磁盘增长。

## 首版部署规格

- 最低：2 CPU、4 GiB 内存、20 GiB SSD，单实例，适合不超过 5 个并发用户。
- 推荐：4 CPU、8 GiB 内存、50 GiB SSD，Redis 共享限流，适合 20 个并发用户。
- 高于 20 并发用户必须根据真实模型延迟和目标数据库容量重新压测，不直接沿用推荐值。

验收门槛：非 LLM API P95 小于 500 ms、错误率低于 1%；审批写入必须零重复执行；
LLM 延迟单独按 Provider 统计，不混入应用 API 延迟。

`review-submit` 会创建真实审核任务，只允许在一次性测试环境运行。自定义接口可使用
`--scenario custom --method POST --path /api/... --payload-json '{...}'`；认证环境通过
`--token` 传入短期测试令牌。脚本在错误率或 P95 超过门槛时返回非零退出码，可直接作为 CI 门禁。

组织接口压测必须同时提供 `--organization-id` 和 `--membership-id`，脚本会为每个请求生成
独立 `X-Request-ID`。预热请求不计入吞吐和延迟；JSON报告包含操作系统、Python版本、CPU数、
Worker数、数据集标签、吞吐、P50/P95/P99和错误率，可直接作为CI产物归档。CPU、内存、数据库
连接和磁盘增长仍应由部署环境监控同步采集，不能使用压测客户端进程指标代替服务端指标。
