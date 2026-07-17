# 一键金融搜索实验

## GitHub 一键运行

1. 打开仓库 **Actions**。
2. 选择 **One-click FinSearch Audit**。
3. 点击 **Run workflow**。
4. 完成后打开 `github-pages` 地址。

默认演示不需要 API Key，会生成：

- `index.html`：周五展示页面；
- `report.md`：中文汇报稿；
- `trace.json`：关键词、工具调用、答案、引用与时间审计；
- `metrics.csv`：真实性、完整性和效率指标。

本地运行：

```bash
python audit/run_demo.py --output site
```

这是可交作业的最小完整版本，不构成投资建议。成功案例已经与
FinSearchComp 参考答案核对；失败案例用于说明浅层搜索、数据版本和财年对齐风险。
