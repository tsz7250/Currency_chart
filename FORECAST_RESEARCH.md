# 匯率預測研究紀錄

本文件保存探索性研究方法、結果、失敗原因與重跑入口。主站操作、部署及公開 API 請見 [README.md](README.md)。

## 結論

目前主站仍使用固定版本 Chronos-Bolt Small。研究候選不會自動替換主站，也不會改寫影子紀錄。

### 最佳方向候選

固定 50/50 算術平均以下兩組三分類機率：

- 五年公開市場資料訓練的 CatBoost regime 模型。
- 一年 Mastercard 核心四組資料訓練的 CatBoost control 模型。

六組幣對、7／14／30 天、共 1,620 筆封存樣本結果：

| 指標 | 固定融合 | 封存 Bolt |
|---|---:|---:|
| Accuracy | 61.1728% | 45.6790% |
| Macro precision | 42.6458% | 43.2126% |
| Macro recall | 44.2152% | 43.4958% |
| Macro F1 | 40.5206% | 35.4477% |

7／14／30 天 macro F1 分別為 35.1184%、40.4313%、46.0123%。三個外層時間折都優於 Bolt，但 30 日日期區塊重抽樣的 F1 差值 95% 區間仍跨 0。這是重複使用封存日期的探索性結果，不是獨立統計證明。

### 最佳價格區間候選

保留 Bolt 中位數。先在對數價格空間等權融合 yield 與 regime 市場模型的原始 10／90 分位數，再使用核心四組成熟結果按步長共同對稱 CQR 校準。

| 指標 | 共同 CQR | 封存 Bolt |
|---|---:|---:|
| 80% 涵蓋率 | 77.7778% | 74.1975% |
| 標準化寬度 | 0.0336032 | 0.0320590 |
| Interval score | 0.0480434 | 0.0500454 |
| 下尾漏出率 | 15.3086% | 14.5679% |
| 上尾漏出率 | 6.9136% | 11.2346% |

Interval score 改善 4.00%，三種日期區塊重抽樣的差值區間均低於 0。平均寬度增加 4.82%。JPY-TWD 7／14 天與 EUR-USD 30 天仍低於 70% 涵蓋率，所以未通過替換門檻。

## 保留的重跑流程

所有入口都限制 8 小時，輸出不可覆寫。多年市場資料只提供輔助資訊，不冒充 Mastercard 標籤。

1. 建立五年市場、殖利率及波動情境模型：

   `python -X utf8 scripts/research_yield_regimes.py --history-source instance/forecast-research/run-2026-11-05 --output instance/forecast-yield-regimes-final/run-2026-11-05`

2. 直接轉移方向機率並訓練市場分位數區間：

   `python -X utf8 scripts/research_yield_transfer.py --source instance/forecast-yield-regimes-final/run-2026-11-05 --history-source instance/forecast-research/run-2026-11-05 --reference-source instance/forecast-cross-pair/run-2026-11-05 --bolt-source instance/forecast-bolt-intervals/run-2026-11-05 --output instance/forecast-yield-transfer-final/run-2026-11-05-clean`

3. 評估固定方向融合：

   `python -X utf8 scripts/research_direction_blend.py --source instance/forecast-yield-transfer-final/run-2026-11-05-clean --output instance/forecast-direction-blend/run-2026-11-05-clean`

4. 評估共同對稱 CQR 區間：

   `python -X utf8 scripts/research_interval_pool.py --source instance/forecast-yield-transfer-final/run-2026-11-05-clean --market-source instance/forecast-yield-regimes-final/run-2026-11-05 --history-source instance/forecast-research/run-2026-11-05 --bolt-source instance/forecast-bolt-intervals/run-2026-11-05 --output instance/forecast-interval-final/run-2026-11-05-clean`

## 資料與驗證規則

- Mastercard 目標資料涵蓋 2025-11-05 至 2026-11-04，共一年。
- 公開市場資料涵蓋最近五年。ECB、臺灣央行及美國財政部資料都採保守可用時間。
- 美國財政部輸入使用 3 個月、2 年與 10 年平價殖利率；不是政策利率或未來實現利率。
- 原四組 TWD-HKD、USD-TWD、JPY-TWD、EUR-USD 參與訓練與校準。
- AUD-USD、GBP-JPY 不參與訓練、選模或校準，只作額外驗證。
- 三個外層測試折各含 30 個共同起點。所有訓練與校準標籤目標日都早於下一階段。
- 方向門檻固定為 ±0.1%。報告固定三類 accuracy、macro precision／recall／F1、log loss 及混淆矩陣。
- 區間報告涵蓋率、上下尾漏出率、標準化寬度及 interval score。
- 日期區塊重抽樣使用 30 日為主，7／14 日作敏感度分析，各 2,000 次。
- 歷史公布版本未完全驗證。重複使用過的封存日期只能支持探索性結論。

## 基礎研究與評測

保留以下基礎入口，因為最佳候選仍依賴其資料、切分或對照：

- `benchmark_forecasts.py`：比較 Bolt Small、Bolt Base、Chronos-2 與 90／180／270 天輸入。
- `research_forecasts.py`：建立 Mastercard 歷史、公開市場快照及 Chronos-2 對照。
- `research_supervised.py`：建立 CatBoost control 模型及共同特徵資料。
- `research_nested.py`：建立三折巢狀日期切分。
- `research_evaluation_v2.py`：固定評分、群組、門檻及日期區塊重抽樣。
- `research_bolt_intervals.py`：建立固定 Bolt 中心及原始區間。
- `research_cross_pair.py`：提供最佳方向融合使用的 CatBoost control 機率。

這些入口產生的報告皆保存在 `instance`。探索性研究與主站按需預測分開，不以歷史回放冒充未見資料驗證。

## 已淘汰方法

下列方法的程式與本地輸出已移除；保留結果與淘汰原因，避免重複研究。

| 方法 | 主要結果 | 淘汰原因 |
|---|---|---|
| Chronos-2 偏差修正與最新價融合 | 未穩定改善價格誤差 | 中心偏差不一致 |
| 市場價差 constant／Ridge | 未通過價格門檻 | 市場價格不能補造 Mastercard 長期標籤 |
| Cross-pair 擴充特徵 | 候選未通過；control 保留作融合 | 新增特徵沒有穩定增益 |
| 新 ECB 時點對齊 | 方向 F1 37.04%，區間變差 | 資料較新不等於訊號更好 |
| 固定統計趨勢／反轉／EWMA | 最佳方向 F1 約 35.66% | 未穩定優於 Bolt |
| 延遲線上區間校準 | 最佳涵蓋率約 76.67% | 區間更寬且 score 未改善 |
| 波動標準化與殘差區間 | 涵蓋率或寬度改善不能兼得 | 未通過共同門檻 |
| 風險特徵消融 | Macro F1 34.49% | 波動與美元曝險不足以解釋改善 |
| 幾何機率池 | Macro F1 40.45% | 略低於固定算術融合 40.52% |
| 校準集學習融合權重 | Macro F1 39.56% | 三折權重 0.705／1.000／0.192，不穩定 |
| 兩階段 flat 分類 | Macro F1 40.23% | 找回 16 個 flat，但產生 192 個 false flat |
| 獨立上下尾校準 | 涵蓋率 71.98% | score 與涵蓋率都變差 |
| Mondrian 幣對校準 | 涵蓋率 78.46%，score 0.04926 | EUR-USD 30 天仍低於 70%，且差於共同 CQR |

## 替換門檻

方向候選須同時滿足：

- 整體 macro F1 比封存 Bolt 與 Chronos-2 至少增加 3 個百分點。
- Accuracy 不得下降超過 2 個百分點。
- 7／14／30 天 macro F1 均不得退步。

區間候選須同時滿足：

- 整體 80% 涵蓋率介於 75%～85%。
- 任一幣對／期間涵蓋率不得低於 70%。
- 標準化 interval score 至少改善 10%。
- 平均標準化寬度不得增加超過 20%。

更嚴格的雙目標要求涵蓋率 78%～82%、每格至少 70%、score 改善 10% 且寬度縮小 5%。目前區間候選沒有通過。

## 限制與下一步

- 最佳方向候選已通過工程門檻，但日期已用於多輪研究。
- 最佳區間候選只改善 score 4.00%，沒有通過替換門檻。
- 目前資料不足以安全訓練更細的局部校準或幣對專屬模型。
- 下一個可信步驟是鎖定方向候選，使用真正未見的新日期驗證；修改候選後必須重新開始驗證期。
- 研究不提供交易指令，也不保證未來命中率或 80% 區間在每段期間成立。
