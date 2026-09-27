# 阶段 3 排序评估

- 指标版本：`metrics-v1`；正确答案：`weak-pos-v1`；K = 10
- 索引：`idx-catalog-20260926-baai-bge-m3-doc-v2`；查询：13 条

## 验收（事先约定）

- 固定混排 / 只看相关性 的平均相关性比值：**0.9867**（要求 ≥ 0.90）
- 长尾曝光提升：**1.0329**（要求 ≥ 0.50）
- 结果：**通过**

## 汇总

| 指标 | rag-rel-v1 | rag-tailmix-v1 | agent-react-v1 |
|---|---|---|---|
| hits@10 | 1.1538 | 0.9231 | 1.0 |
| recall@10 | 0.1154 | 0.0923 | 0.1 |
| ndcg@10 | 0.1409 | 0.0627 | 0.0971 |
| candidate_recall | 0.0462 | 0.0462 | 0.0462 |
| tail_candidate_recall | None | None | None |
| tail_exposure | 0.2308 | 0.4692 | 0.3923 |
| mean_relevance | 0.9955 | 0.9823 | 0.9873 |
| novelty | 0.0423 | 0.1077 | 0.0885 |
| ild | 0.1577 | 0.1892 | 0.1775 |
| serendipity | 0.0 | 0.4385 | 0.2769 |
| hallucination_rate | 0.0 | 0.0 | 0.0 |
| constraint_pass | 1.0 | 1.0 | 1.0 |
| evidence_accuracy | 1.0 | 1.0 | 0.9769 |
| catalog_coverage | 0.0054 | 0.0051 | 0.0059 |
| tail_coverage | 0.0035 | 0.0092 | 0.0089 |
| artist_coverage | 0.0446 | 0.0536 | 0.0574 |
| unique_songs | 76 | 72 | 83 |

## 分层

```json
{
 "rag-rel-v1": {
  "by_branch": {
   "both": {
    "recall@10": 0.1333,
    "tail_exposure": 0.1667,
    "mean_relevance": 0.9943
   },
   "oasis": {
    "recall@10": 0.1667,
    "tail_exposure": 0.2833,
    "mean_relevance": 0.9956
   },
   "pink_floyd": {
    "recall@10": 0.025,
    "tail_exposure": 0.2,
    "mean_relevance": 0.9963
   }
  },
  "by_exploration": {
   "0.1": {
    "tail_exposure": 0.1,
    "mean_relevance": 0.9967,
    "novelty": 0.0241
   },
   "0.5": {
    "tail_exposure": 0.3,
    "mean_relevance": 0.9958,
    "novelty": 0.0415
   },
   "0.9": {
    "tail_exposure": 0.6,
    "mean_relevance": 0.993,
    "novelty": 0.0828
   }
  },
  "by_bucket_exposure": {
   "head": 0.5615,
   "mid": 0.2077,
   "tail": 0.2308
  }
 },
 "rag-tailmix-v1": {
  "by_branch": {
   "both": {
    "recall@10": 0.1333,
    "tail_exposure": 0.5333,
    "mean_relevance": 0.9595
   },
   "oasis": {
    "recall@10": 0.1333,
    "tail_exposure": 0.4167,
    "mean_relevance": 0.9911
   },
   "pink_floyd": {
    "recall@10": 0.0,
    "tail_exposure": 0.5,
    "mean_relevance": 0.9862
   }
  },
  "by_exploration": {
   "0.1": {
    "tail_exposure": 0.2,
    "mean_relevance": 0.9948,
    "novelty": 0.0435
   },
   "0.5": {
    "tail_exposure": 0.4,
    "mean_relevance": 0.9919,
    "novelty": 0.091
   },
   "0.9": {
    "tail_exposure": 0.7,
    "mean_relevance": 0.986,
    "novelty": 0.1674
   }
  },
  "by_bucket_exposure": {
   "head": 0.3769,
   "mid": 0.1538,
   "tail": 0.4692
  }
 },
 "agent-react-v1": {
  "by_branch": {
   "both": {
    "recall@10": 0.1333,
    "tail_exposure": 0.3667,
    "mean_relevance": 0.9852
   },
   "oasis": {
    "recall@10": 0.1333,
    "tail_exposure": 0.4,
    "mean_relevance": 0.9872
   },
   "pink_floyd": {
    "recall@10": 0.025,
    "tail_exposure": 0.4,
    "mean_relevance": 0.9892
   }
  },
  "by_exploration": {
   "0.1": {
    "tail_exposure": 0.2,
    "mean_relevance": 0.9969,
    "novelty": 0.0523
   },
   "0.5": {
    "tail_exposure": 0.4,
    "mean_relevance": 0.9713,
    "novelty": 0.0743
   },
   "0.9": {
    "tail_exposure": 0.6,
    "mean_relevance": 0.9887,
    "novelty": 0.1519
   }
  },
  "by_bucket_exposure": {
   "head": 0.3538,
   "mid": 0.2538,
   "tail": 0.3923
  }
 }
}
```
