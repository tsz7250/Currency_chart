# 匯率預測研究紀錄

本文件保存探索性研究方法、結果、失敗原因與重跑入口。主站操作、部署及公開 API 請見 [README.md](README.md)。

## 結論

目前主站使用固定版本 Chronos-Bolt Small 為中心；方向融合候選已依下方 2026-11-16 治理變更以探索性等級上主站。其餘研究候選不會自動替換主站，也不會改寫影子紀錄。2026-11-17：影子驗證基建（`app/forecast_shadow.py`、`app/forecast_supervised_shadow.py`、`scripts/shadow_forecasts.py`、`scripts/shadow_supervised.py`）已整組移除，判定改由滾動回測承擔。

### 2026-11-16 治理變更（擁有者決定）

方向融合候選改為探索性等級直接上主站，放棄影子累積驗證：

- 傾斜路徑定義（實作即定義）：各預測點的傾斜倍率 = exp(d·σ)，d 為融合機率的 P(up)−P(down) 於 7／14／30 節點線性內插，σ 取該點校準帶隱含標準差 ln(high80/low80)/(2×1.2816)；帶於 log 空間同步平移。

- 彈窗新增融合傾斜路徑（regime＋control CatBoost 固定 50/50，波動尺度傾斜、帶同步平移），標示實驗性。
- 持續驗證由彈窗既有滾動回測承擔：上線初期起點落在模型訓練期（約 3 個月），分數會偏高；汙染起點滾出 90 天窗口後數字轉為可信。
- 判定規則（事先記錄）：汙染期過後，若傾斜路徑回測 macro F1 ≥ Bolt 中位數回測 macro F1，則保留；否則移除傾斜路徑，彈窗退回校準帶＋到價計算器。
- 2026-11-17 執行判定：首次訓練（11-16）的 holdout 樣本外評估（342 樣本）accuracy 50.1%、macro F1 23.2%，TWD-HKD/7 天 accuracy 34.5%——方向訊號在未見資料上不存在，證實原研究 61.2% 為重用日期的探索性灌水。擁有者決定立即停用融合推論（`instance/models/blend-disabled` 標記檔；自動訓練與 holdout 評估照常每 7 天刷新證據）。重新啟用＝刪除該標記檔。
- 舊影子基建已於 2026-11-17 整組移除（見結論段）。

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

方向候選鏈（2026-11 版）用單一入口依序執行，失敗即停、可重跑續用既有輸出：

```powershell
python -X utf8 scripts/research_chain.py
```

鏈的順序：`benchmark_forecasts`（抓一年 Mastercard 與 Bolt/Chronos-2 基線）→ `research_forecasts`（歷史、市場快照、Bolt 分位數）→ `research_supervised`（control 資料與基線 predictions.json）→ `research_bolt_intervals`（raw 分位數；來源缺 `price.current` 時自動 raw-only）→ `research_cross_pair`（control cat4 模型）→ `research_yield_regimes`（regime 模型）→ `research_yield_transfer`（融合輸入）→ `research_direction_blend`（融合評估數字）。

跳過 `research_nested` 與 `research_evaluation_v2`：兩者只服務已淘汰的區間候選。`research_interval_pool` 同理不跑。

### 最小訓練路徑（2026-11-16 起，自動）

模型訓練不需手動執行，也不依賴完整研究鏈：

- `scripts/train_blend_models.py` 只用既有過去資料：regime 模型來自五年公開市場資料，control 模型來自一年 Mastercard 歷史；排除最後 35 天 holdout，訓練後輸出樣本外評估至 `instance/forecast-blend/training-summary.json`。
- 排程自動化：app 啟動時與每日 10:30（台北）檢查 `instance/models/blend-regime.cbm` 與 `blend-control.cbm`，缺失或超過 7 天舊即以背景子程序重訓（`app/scheduler.py` `check_blend_training`）；訓練失敗只寫 `train.log`，主站行為不變。
- 模型檔存在即自動啟用融合（方向卡、傾斜路徑、含漂移到價機率）；刪除任一檔案即停用，主站退回純 Bolt 行為。
- `research_chain.py` 保留為可選的完整研究數字路徑（含 Bolt 重播與門檻評估），不再是部署的必要步驟。

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

- 方向融合候選已依 2026-11-16 治理變更上主站（實驗性）；回測數字在訓練期汙染消退前（約 3 個月）會偏高。
- 判定以彈窗滾動回測為準：汙染期後 blend macro F1 ≥ Bolt 則保留，否則移除傾斜路徑。
- 最佳區間候選只改善 score 4.00%，沒有通過替換門檻；區間維持現行 CQR 校準。
- 目前資料不足以安全訓練更細的局部校準或幣對專屬模型；融合僅涵蓋研究的 6 個幣對。
- 研究不提供交易指令，也不保證未來命中率或 80% 區間在每段期間成立。
