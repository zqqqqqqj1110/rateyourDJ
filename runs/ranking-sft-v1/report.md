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
| hits@10 | 1.2308 | 0.9231 | 0.3846 |
| recall@10 | 0.1231 | 0.0923 | 0.0385 |
| ndcg@10 | 0.1686 | 0.065 | 0.0262 |
| candidate_recall | 0.0487 | 0.0487 | 0.0487 |
| tail_candidate_recall | None | None | None |
| tail_exposure | 0.2692 | 0.4538 | 0.4308 |
| mean_relevance | 0.9948 | 0.9825 | 0.9782 |
| novelty | 0.0455 | 0.1025 | 0.0998 |
| ild | 0.1543 | 0.1834 | 0.1817 |
| serendipity | 0.0 | 0.4154 | 0.2462 |
| hallucination_rate | 0.0 | 0.0 | 0.0 |
| constraint_pass | 1.0 | 1.0 | 1.0 |
| evidence_accuracy | 1.0 | 1.0 | 1.0 |
| catalog_coverage | 0.0046 | 0.0048 | 0.0049 |
| tail_coverage | 0.0038 | 0.0089 | 0.0083 |
| artist_coverage | 0.0434 | 0.0536 | 0.0497 |
| unique_songs | 65 | 68 | 69 |

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
    "recall@10": 0.0667,
    "tail_exposure": 0.5,
    "mean_relevance": 0.9585
   },
   "oasis": {
    "recall@10": 0.05,
    "tail_exposure": 0.4,
    "mean_relevance": 0.985
   },
   "pink_floyd": {
    "recall@10": 0.0,
    "tail_exposure": 0.425,
    "mean_relevance": 0.9828
   }
  },
  "by_exploration": {
   "0.1": {
    "tail_exposure": 0.1,
    "mean_relevance": 0.9944,
    "novelty": 0.036
   },
   "0.5": {
    "tail_exposure": 0.4,
    "mean_relevance": 0.9887,
    "novelty": 0.0936
   },
   "0.9": {
    "tail_exposure": 0.7,
    "mean_relevance": 0.9607,
    "novelty": 0.1455
   }
  },
  "by_bucket_exposure": {
   "head": 0.4385,
   "mid": 0.1308,
   "tail": 0.4308
  }
 }
}
```
