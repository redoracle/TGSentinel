# 🔍 Understanding Semantic Similarity & Centroid Score

This document explains what **semantic similarity** is, why the system gives certain **similarity scores**, and how the **centroid score** works to identify messages that match your interests.

The goal is to help you understand _why_ a message is classified as "related to your interests" or not.

---

## 🧠 1. What Is Semantic Similarity?

When two messages "mean something similar," the system detects it using **semantic similarity**.

Unlike simple keyword matching, semantic similarity considers **meaning**, not exact words.

**Example:**

- "New SaaS competitor enters the market"
- "Fresh startup challenges traditional SaaS vendors"

These two sentences use different words but mean almost the same thing → **high similarity**.

---

## 📦 2. How the System Represents Messages (Embeddings)

To compare meanings, the system converts every message into a set of numbers called an **embedding**.

Think of an embedding as:

> "A coordinate in a big semantic space."

Messages with similar meaning end up in **nearby positions**.
Messages with unrelated meaning end up **far apart**.

This is how the system can understand meaning even when the wording changes.

---

## 📏 3. How Similarity Is Measured

The system measures how close two message-embeddings are using a metric called **cosine similarity**.

### Score Mapping

The raw cosine similarity value (ranging from -1 to 1) is mapped to a **0 to 1 scale** for easier interpretation:

| Score           | Meaning                                       |
| --------------- | --------------------------------------------- |
| **0.00 – 0.30** | Very weakly related / noise                   |
| **0.30 – 0.50** | Somewhat related                              |
| **0.50 – 0.70** | Moderately similar (potentially relevant)     |
| **0.70 – 0.85** | Strongly similar (highly relevant)            |
| **0.85 – 1.00** | Extremely close (almost exact semantic match) |

📝 **Important:**
With properly normalized centroids and the [-1, 1] → [0, 1] mapping, an **exact match** of a positive sample will score **1.0**.

---

## 🎯 4. What Is the _Centroid_ and Why Do We Use It?

To understand your interests, the system looks at the **examples you provide**.

If you give several messages that represent what you care about, the system:

1. Converts each one to an embedding (normalized to unit length)
2. Averages them to create a center point
3. **Re-normalizes** the average to ensure consistent cosine similarity

This normalized center point is called a **centroid**.

**It's like a summary of everything you find interesting.**

When a new message arrives, the system checks:

> "How close is this message to your centroid?"

This result is the **centroid score**.

### Why Normalization Matters

The system normalizes all centroids to unit length. This ensures:

- Dot products produce **true cosine similarity** values
- Scores are **consistent and predictable**
- Thresholds work **reliably** across different profiles

---

## 📉 5. Understanding Centroid vs. Individual Sample Scores

The **Similarity Tester** in the UI shows two scores:

| Score Type                             | What It Measures                                          |
| -------------------------------------- | --------------------------------------------------------- |
| **Score** (centroid)                   | Similarity to the average meaning of ALL positive samples |
| **Best Match** (max_sample_similarity) | Maximum similarity to any SINGLE positive sample          |

### Example Interpretation

```text
Score: 0.750 (best match: 1.000)
```

This means:

- The text is an **exact copy** of one training sample (best match = 1.0)
- The centroid score is 0.750 because it measures similarity to the **average concept**
- If your positive samples are diverse, centroid scores will be lower than best match

### What You Can Learn

| Situation                        | Meaning                                                                |
| -------------------------------- | ---------------------------------------------------------------------- |
| **High best match, lower score** | Text matches one sample well, but your samples are diverse             |
| **High score, lower best match** | Text captures the general concept but isn't close to any single sample |
| **Both high**                    | Strong match to both the concept and specific examples                 |
| **Both low**                     | Irrelevant message                                                     |

---

## ➖ 6. How Negative Samples Work

You can provide **negative samples** — examples of messages you do NOT want to match.

The system:

1. Creates a normalized centroid from your negative samples
2. Measures similarity between new messages and the negative centroid
3. **Applies a penalty** only when the negative similarity exceeds a margin threshold (0.3)

### Why Use a Margin?

Without a margin, even small incidental similarities to negative samples would unfairly penalize good matches. The margin ensures:

- Only messages that are **genuinely similar** to negative samples get penalized
- Legitimate matches aren't hurt by coincidental word overlap

### The Scoring Formula

```text
raw_score = positive_similarity × positive_weight

if negative_similarity > 0.3:
    penalty = (negative_similarity - 0.3) × negative_weight
    raw_score = raw_score - penalty

final_score = (raw_score + 1.0) / 2.0  # Map to [0, 1]
```

---

## 📌 7. Score Ranges You Should Expect

With properly **normalized centroids** and the scoring formula above:

| Range           | Interpretation                                |
| --------------- | --------------------------------------------- |
| **0.00 – 0.30** | Very weakly related / noise                   |
| **0.30 – 0.50** | Somewhat related                              |
| **0.50 – 0.70** | Moderately similar (potentially relevant)     |
| **0.70 – 0.85** | Strongly similar (highly relevant)            |
| **0.85 – 1.00** | Extremely close (almost exact semantic match) |

### What to Expect in Practice

- **Exact match** of a positive sample → Score near **1.0**
- **Semantically similar** messages → Scores in **0.60–0.85** range
- **Neutral/unrelated** content → Scores around **0.45–0.55** (midpoint)
- **Matches negative samples** → Scores **below 0.50** (penalized)

---

## 🎛️ 8. How the System Decides What Is "Relevant"

You can adjust a setting called the **Similarity Threshold**.

This tells the system:

> "Only consider messages above this score as relevant."

### Recommended Thresholds

| Threshold     | Result                                    |
| ------------- | ----------------------------------------- |
| **0.45–0.50** | Broad matching (more results, some noise) |
| **0.55–0.65** | Balanced filtering (recommended for most) |
| **0.70–0.75** | Strict matching (high-confidence only)    |
| **0.80+**     | Very strict (near-exact matches only)     |

### Tips for Setting Thresholds

1. **Start at 0.55** and adjust based on results
2. **More positive samples** = more stable centroid = can use higher thresholds
3. **Add negative samples** if you're getting too many false positives
4. Use the **Similarity Tester** to calibrate your threshold

---

## 🔧 9. Tuning Your Interest Profile

### Positive Samples

- Provide **5-10 diverse examples** of messages you want to match
- Include variations in phrasing and vocabulary
- More samples = more stable and accurate centroid

### Negative Samples

- Add examples of messages you **don't** want to match
- Focus on topics that are similar but unwanted (e.g., "stock prices" if you want "market trends" but not trading advice)
- The system applies penalties only when similarity exceeds 0.3

### Weights

| Parameter           | Default | Range   | Effect                        |
| ------------------- | ------- | ------- | ----------------------------- |
| **positive_weight** | 1.0     | 0.1–2.0 | Amplifies positive similarity |
| **negative_weight** | 0.15    | 0.0–0.5 | Controls penalty strength     |

---

## 📡 10. Summary

| Concept                 | Description                                                |
| ----------------------- | ---------------------------------------------------------- |
| **Semantic similarity** | Comparing meaning, not exact words                         |
| **Embeddings**          | Numerical vectors representing text meaning                |
| **Centroid**            | Normalized average of your positive sample embeddings      |
| **Cosine similarity**   | Measures angle between vectors (-1 to 1, mapped to 0 to 1) |
| **Best match**          | Maximum similarity to any single positive sample           |
| **Negative margin**     | Only penalizes when negative similarity > 0.3              |
| **Threshold**           | Your configured cutoff for what counts as "relevant"       |

### Key Takeaways

✅ **Exact matches score 1.0** — the system correctly identifies identical text  
✅ **Centroid scores reflect the general concept** — not just individual samples  
✅ **Negative samples prevent false positives** — with a margin to avoid over-penalization  
✅ **Scores are stable and interpretable** — thanks to normalized centroids  
✅ **Use the Similarity Tester** — to calibrate thresholds before going live

---

We hope this guide helps you understand how semantic similarity and centroid scores work! If you have any questions, feel free to reach out to our support team. Happy exploring! 🚀
