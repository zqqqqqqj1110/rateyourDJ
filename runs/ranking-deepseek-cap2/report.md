# 阶段 3 排序评估

- 指标版本：`metrics-v1`；正确答案：`weak-pos-v1`；K = 10
- 索引：`idx-catalog-20260926-baai-bge-m3-doc-v2`；查询：13 条

## 验收（事先约定）

- 固定混排 / 只看相关性 的平均相关性比值：**0.9876**（要求 ≥ 0.90）
- 长尾曝光提升：**0.6857**（要求 ≥ 0.50）
- 结果：**通过**

## 汇总

| 指标 | rag-rel-v1 | rag-tailmix-v1 | agent-react-v1 |
|---|---|---|---|
| hits@10 | 1.2308 | 0.9231 | 0.9231 |
| recall@10 | 0.1231 | 0.0923 | 0.0923 |
| ndcg@10 | 0.1686 | 0.065 | 0.094 |
| candidate_recall | 0.0487 | 0.0487 | 0.0487 |
| tail_candidate_recall | None | None | None |
| tail_exposure | 0.2692 | 0.4538 | 0.4154 |
| mean_relevance | 0.9948 | 0.9825 | 0.9762 |
| novelty | 0.0455 | 0.1025 | 0.0831 |
| ild | 0.1543 | 0.1834 | 0.1837 |
| serendipity | 0.0 | 0.4154 | 0.2769 |
| hallucination_rate | 0.0 | 0.0 | 0.0 |
| constraint_pass | 1.0 | 1.0 | 1.0 |
| evidence_accuracy | 1.0 | 1.0 | 0.9846 |
| catalog_coverage | 0.0046 | 0.0048 | 0.0058 |
| tail_coverage | 0.0038 | 0.0089 | 0.0105 |
| artist_coverage | 0.0434 | 0.0536 | 0.0612 |
| unique_songs | 65 | 68 | 81 |

## 分层

```json
{
 "rag-rel-v1": {
  "by_branch": {
   "both": {
    "recall@10": 0.1333,
    "tail_exposure": 0.2333,
    "mean_relevance": 0.9928
   },
   "oasis": {
    "recall@10": 0.1833,
    "tail_exposure": 0.3,
    "mean_relevance": 0.9957
   },
   "pink_floyd": {
    "recall@10": 0.025,
    "tail_exposure": 0.25,
    "mean_relevance": 0.995
   }
  },
  "by_exploration": {
   "0.1": {
    "tail_exposure": 0.2,
    "mean_relevance": 0.9963,
    "novelty": 0.035
   },
   "0.5": {
    "tail_exposure": 0.3,
    "mean_relevance": 0.9959,
    "novelty": 0.0437
   },
   "0.9": {
    "tail_exposure": 0.6,
    "mean_relevance": 0.9931,
    "novelty": 0.0829
   }
  },
  "by_bucket_exposure": {
   "head": 0.5769,
   "mid": 0.1538,
   "tail": 0.2692
  }
 },
 "rag-tailmix-v1": {
  "by_branch": {
   "both": {
    "recall@10": 0.1333,
    "tail_exposure": 0.5333,
    "mean_relevance": 0.9582
   },
   "oasis": {
    "recall@10": 0.1333,
    "tail_exposure": 0.4167,
    "mean_relevance": 0.9912
   },
   "pink_floyd": {
    "recall@10": 0.0,
    "tail_exposure": 0.45,
    "mean_relevance": 0.9876
   }
  },
  "by_exploration": {
   "0.1": {
    "tail_exposure": 0.2,
    "mean_relevance": 0.995,
    "novelty": 0.0422
   },
   "0.5": {
    "tail_exposure": 0.4,
    "mean_relevance": 0.9919,
    "novelty": 0.0932
   },
   "0.9": {
    "tail_exposure": 0.7,
    "mean_relevance": 0.986,
    "novelty": 0.1674
   }
  },
  "by_bucket_exposure": {
   "head": 0.4385,
   "mid": 0.1077,
   "tail": 0.4538
  }
 },
 "agent-react-v1": {
  "by_branch": {
   "both": {
    "recall@10": 0.0333,
    "tail_exposure": 0.4333,
    "mean_relevance": 0.9761
   },
   "oasis": {
    "recall@10": 0.1667,
    "tail_exposure": 0.4,
    "mean_relevance": 0.9776
   },
   "pink_floyd": {
    "recall@10": 0.025,
    "tail_exposure": 0.425,
    "mean_relevance": 0.9742
   }
  },
  "by_exploration": {
   "0.1": {
    "tail_exposure": 0.2,
    "mean_relevance": 0.9814,
    "novelty": 0.0294
   },
   "0.5": {
    "tail_exposure": 0.4,
    "mean_relevance": 0.9722,
    "novelty": 0.074
   },
   "0.9": {
    "tail_exposure": 0.6,
    "mean_relevance": 0.9315,
    "novelty": 0.1017
   }
  },
  "by_bucket_exposure": {
   "head": 0.4308,
   "mid": 0.1538,
   "tail": 0.4154
  }
 }
}
```
