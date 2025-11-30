# 📖 Feedback Learning System - User Guide

Learn how to use TG Sentinel's feedback learning system to improve profile accuracy over time.

---

## What is Feedback Learning?

Feedback learning allows TG Sentinel to **automatically adjust** your Interest and Alert profiles based on your thumbs up/down feedback. Instead of manually tweaking thresholds and training samples, the system learns from your corrections.

### Two Feedback Systems

TG Sentinel uses **separate feedback systems** for different profile types:

#### Interest Profiles (Semantic Matching)

- Uses semantic embeddings to understand meaning
- **Thumbs down (👎)** → Raises threshold or adds negative training samples
- **Thumbs up (👍)** → Adds positive training samples
- Learns complex patterns through sample augmentation
- Profile IDs: 3000-3999 range

#### Alert Profiles (Keyword Matching)

- Uses keyword matching with min_score thresholds
- **Thumbs down (👎)** → Raises min_score (stricter matching)
- **Thumbs up (👍)** → Logged for statistics only (no auto-adjustment)
- Focuses on reducing false positives only
- Profile IDs: 1000-1999 range

### How It Works

1. **You provide feedback:** Click 👍 (good match) or 👎 (false alarm) on messages in your feed
2. **System aggregates:** Feedback is collected per profile (not acted on immediately)
3. **Automatic adjustments:** After enough feedback, the system automatically:
   - Raises thresholds to reduce false positives (both types)
   - Adds examples to training data to improve semantic understanding (interest only)
4. **You review:** Pending samples appear in profile settings for optional review/rollback (interest only)

### Safety Features

- **Aggregation:** Adjustments require multiple feedbacks (3 for threshold, 2 for samples)
- **Drift caps:** Profiles can't drift too far from original intent (0.25 for interest, 0.5 for alerts)
- **Pending buffer:** New training samples must be reviewed before final commit (interest only)
- **Automatic decay:** Old feedback (>7 days) automatically stops counting toward adjustments

---

## Providing Feedback

### In the Feed View

1. Browse your Interest or Alert feed
2. For each message, click:
   - 👍 **Thumbs Up** - Good match, should be alerted
   - 👎 **Thumbs Down** - False positive, should NOT be alerted

### What Happens Next

Your feedback is:

1. Saved to the database (`feedback` and `feedback_profiles` tables)
2. Added to the profile's in-memory feedback counter
3. When thresholds are met (3 for threshold, 2 for samples), automatic adjustment is triggered
4. You'll see changes in the profile settings page
5. Old feedback (>7 days) is automatically decayed and no longer counts

---

## Understanding Adjustments

### For Interest Profiles

#### Threshold Adjustments (Fast)

**What:** Raises the minimum score needed for a match  
**Triggered by:** 3 borderline false positives (score in range [threshold, threshold + 0.20])  
**Effect:** Reduces false positives quickly  
**Reversible:** Yes (manually lower threshold or wait for drift cap reset)

Example:

- Original threshold: `0.45`
- After 3 false positives scoring `0.50-0.60` → threshold raised to `0.55`
- Messages scoring `0.50` are now filtered out
- Adjustment recorded in `profile_adjustments` table with reason "3 borderline false positives"

### Sample Augmentation (Semantic Learning)

**What:** Adds your feedback messages to training data  
**Triggered by:**

- 2 severe false positives (score ≥ threshold + 0.20) → add to negative samples
- 2 strong true positives (score ≥ threshold + 0.15) → add to positive samples

**Effect:** Teaches profile to recognize new patterns  
**Reversible:** Yes (rollback from pending buffer before committing)

Example:

- Profile keeps matching "TO THE MOON 🚀" with high scores
- You click 👎 twice when it scores ≥ 0.65 (severe FP)
- Message goes to **pending negative samples** buffer
- After review in profile settings, commit → profile learns to reject hype messages
- Sample is added with weight 0.4 to the profile's `feedback_negative_samples` list
- Batch processor schedules semantic cache clear for next scoring run

#### Min Score Adjustments (Keyword Alerts Only)

**What:** Raises the minimum score needed for a keyword match  
**Triggered by:** 3 false positives AND ≥30% negative feedback rate  
**Effect:** Reduces false positives for alert keywords  
**Reversible:** Yes (manually lower min_score in profile settings)  
**Drift cap:** Can raise up to +0.5 total (alert drift cap is 0.5, higher than interest's 0.25)

Example:

- Alert profile has min_score: `1.0`
- Keyword "emergency" keeps matching non-urgent messages
- After 3 👎 with ≥30% negative rate → min_score raised to `1.1`
- Fewer marginal keyword matches trigger alerts
- If you also give many 👍, negative rate stays below 30% and adjustment won't trigger

**Note:** Alert profiles do NOT use sample augmentation. Thumbs up (👍) is logged for approval rate tracking only, not for auto-adjustments.

- After 3 👎 with 30%+ negative rate → min_score raised to `1.1`

## Reviewing Pending Samples

**Note:** This section applies to **Interest profiles only**. Alert profiles do not use sample augmentation.

### Where to Find Pending Samples

1. Go to **Config** → **Profiles** (or navigate to profile management UI)
2. Select your Interest profile (ID 3000+)
3. Scroll to **"Pending Samples (Awaiting Review)"** section

### What You'll See

- **Positive Samples (Green):** Messages you marked 👍 that scored high (≥ threshold + 0.15)
- **Negative Samples (Red):** Messages you marked 👎 that scored high (≥ threshold + 0.20)
- **Pending Count:** How many samples are waiting in buffer
- **Sample metadata:** Chat ID, message ID, semantic score, timestamp

### Commit or Rollback

- **Commit:** Adds samples to training data permanently
  - Click "Commit Positive" or "Commit Negative" button
  - Samples move from `pending_*_samples` to `feedback_*_samples` in YAML
  - Database status updated from 'pending' to 'committed'
  - Profile scheduled for batch recomputation (semantic cache cleared within 10 minutes)
- **Rollback:** Deletes pending samples without committing

  - Click "Rollback" button if samples look wrong
  - Database status updated to 'rolled_back'
  - No changes to profile training data

## Monitoring Dashboard

**For Interest Profiles:**

- 👍 **Positive (7d):** Recent thumbs up count (for strong TPs: score ≥ threshold + 0.15)
- 👎 **Negative (7d):** Recent thumbs down count
  - Borderline FPs: score in [threshold, threshold + 0.20]
  - Severe FPs: score ≥ threshold + 0.20
- **Cumulative Drift:** How much threshold has changed from original (max +0.25)
- **Pending Samples:** Number of samples in pending buffer awaiting review
- **Adjustment History:** Recent automatic threshold/sample changes with timestamps
- **Status:** Monitoring / Adjustment Pending / Drift Cap Reached

**For Alert Profiles:**

- 👍 **Positive (7d):** Recent thumbs up count (logged for approval rate only)
- 👎 **Negative (7d):** Recent false positive count
- **Cumulative Min_Score Delta:** How much min_score has increased from original (max +0.5)

### Drift Warning

If you see **"Approaching Drift Cap"** or **"Drift Cap Reached"**:

**Interest Profiles (cap: +0.25):**

- Profile threshold has been auto-adjusted significantly
- System will stop auto-adjusting at +0.25 total cumulative drift
- Consider manual review to ensure behavior is correct
- May need to add negative samples instead of continually raising threshold
- Contact operator to reset cumulative drift if working correctly

**Alert Profiles (cap: +0.5):**

- Min_score has been raised significantly (stricter matching, higher cap than interest)
- System will stop auto-adjusting at +0.5 total cumulative drift
- Review if alerts are too restrictive

### Adjustment History

See recent automatic changes in profile settings:

- **Date/Time:** When adjustment was applied
- **Type:** "threshold" (interest), "min_score" (alert), or "sample" (interest only)
- **Old → New values:** e.g., "0.45 → 0.55" for threshold
- **Reason:** e.g., "3 borderline false positives detected in last 7 days"
- **Feedback Count:** How many feedbacks triggered this adjustment
- **Trigger Message:** Chat ID and message ID that triggered it (if applicable)

This history is stored permanently in `profile_adjustments` database table.

## Advanced: Feedback Window & Batch Processing

### Feedback Window

- System only counts feedback from last 7 days (configurable)
- Old feedback automatically decays (stops counting, but stays in database)
- This lets your preferences evolve over time
- Decay runs automatically every 24 hours in background task

### Batch Processing

- Interest profile semantic changes are batched (every 10 minutes or 5+ profiles)
- After committing samples, wait up to 10 minutes for semantic cache refresh
- Check operator guide for batch processor status if changes seem delayed.

---

## Best Practices

### 1. Be Consistent

- Provide feedback regularly (not just when annoyed by false positives)
- Give 👍 to good matches too (helps profile learn true positives)

### 2. Review Pending Samples

- Don't blindly commit pending samples
- Read each message in pending buffer
- Rollback if they don't represent what you want

### 3. Monitor Drift

- Check "Cumulative Drift" periodically
- If drift > 0.20, review profile behavior
- Reset profile to defaults if it's drifted too far

### 4. Leverage Feedback Window

- System only counts feedback from last 7 days
- Old feedback automatically decays
- This lets your preferences evolve over time

---

## Troubleshooting

### "No adjustments happening after feedback"

**For Interest Profiles:**

- **Cause:** Not enough feedback yet
- **Solution:** Keep providing feedback. Threshold adjustment needs 3 borderline FPs, sample addition needs 2 severe FPs or 2 strong TPs.

**For Alert Profiles:**

- **Cause:** Need both 3 false positives AND 30%+ negative rate
- **Solution:** Either provide more 👎 feedback, or if you have many 👍, the system correctly interprets the alert as mostly working.

### "Too many false positives"

**For Interest Profiles:**

1. Provide 👎 feedback on false positives
2. Review pending negative samples and commit
3. If urgent, manually raise threshold in profile settings

**For Alert Profiles:**

1. Provide 👎 feedback (need 3 at 30%+ rate)
2. Ensure you're not also giving too many 👍 (dilutes negative rate)
3. If urgent, manually raise min_score in profile settings
4. Consider refining keywords if false positives are structural

**Q: How many feedbacks before adjustment?**
A:

- **Interest profiles:**
  - Threshold raise = 3 borderline FPs (score in [threshold, threshold+0.20])
  - Sample addition = 2 severe FPs (score ≥ threshold+0.20) or 2 strong TPs (score ≥ threshold+0.15)
- **Alert profiles:**
  - Min_score raise = 3 false positives AND ≥30% negative rate (need enough negatives relative to positives)

1. Review if alerts are working correctly
2. If too restrictive: manually lower min_score
3. If working well: reset cumulative drift (requires operator)
4. Consider if keyword refinement is better than higher min_score

### "Profile stopped matching relevant content"

**Cause:** Threshold raised too high or negative samples too broad  
**Solution:**

1. Check adjustment history
2. Manually lower threshold
3. Rollback pending negative samples if they're too generic
4. Provide 👍 feedback on good matches

### "Drift cap reached"

**Cause:** Profile auto-adjusted to maximum allowed drift (+0.25)  
**Solution:**

1. Review profile behavior
2. If working well: reset cumulative drift (requires operator)
3. If broken: reset profile to defaults and start fresh

---

## FAQ

**Q: How many feedbacks before adjustment?**
A:

- **Interest profiles:** Threshold raise = 3 borderline FPs. Sample addition = 2 severe FPs or 2 strong TPs.
- **Alert profiles:** Min_score raise = 3 false positives AND 30%+ negative rate.

**Q: What's the difference between Interest and Alert feedback?**
A:

- **Interest (semantic):** Uses AI embeddings, learns patterns, adjusts threshold + adds training samples, thumbs up/down both trigger learning.
- **Alert (keyword):** Uses keyword matching, only adjusts min_score threshold, thumbs down triggers adjustment, thumbs up only logs stats.

**Q: Why doesn't thumbs up on alerts trigger adjustments?**
A: Alert profiles use keyword matching, not semantic learning. Lowering min_score could increase false positives. Manual adjustment is safer for broadening alerts.

**Q: Can I disable feedback learning?**
A: Yes, in `config/tgsentinel.yml`, set:

- `feedback_learning.enabled: false` (for interest profiles)
- `alerts.feedback_learning.enabled: false` (for alert profiles)

**Q: What happens to alert feedback when I reach drift cap?**
A: Alert drift cap is 0.5 (higher than interest's 0.25). Once reached, no more automatic adjustments. Review profile behavior and contact operator to manually reset cumulative drift if needed.

**Q: Do pending samples ever auto-commit?**  
A: No. You must manually commit samples via profile settings UI or API. They stay pending forever until you commit or rollback.

**Q: How long until old feedback decays?**  
A: 7 days by default (configurable in `feedback_learning.aggregation.feedback_window_days`). Decay runs automatically every 24 hours. After decay, feedback no longer counts toward adjustments but remains in database for history.

**Q: Can I see what triggered an adjustment?**  
A: Yes, in adjustment history you can see:

- Exact date/time
- Old and new values
- Reason (e.g., "3 borderline false positives")
- Feedback count
- Trigger message (chat_id and msg_id if available)

**Q: What happens during batch processing?**
A: When you commit samples to an interest profile:

1. Samples are moved from pending to feedback lists in YAML
2. Profile is added to batch processor queue
3. Every 10 minutes (or when 5+ profiles pending), batch runs
4. Semantic cache is cleared for all queued profiles
5. Next message scoring will recompute embeddings with new samples

Batch processing only applies to interest profiles. Alert adjustments are immediate.

---

## Related Documentation

- [Configuration Reference](CONFIGURATION.md) - Configure feedback learning settings
- [Operator's Guide](OPERATORS_GUIDE_FEEDBACK_LEARNING.md) - System administration
- [Engineering Guidelines](ENGINEERING_GUIDELINES.md) - Technical architecture
