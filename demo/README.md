# 🎯 TG Sentinel Demo Profiles

This folder contains **ready-to-use example profiles** that demonstrate TG Sentinel's capabilities across various use cases. Each profile is production-ready and can be imported directly into your TG Sentinel instance.

## 📁 Folder Structure

```bash
demo/
├── Alerts/          # Keyword-based alert profiles (heuristic detection)
└── Interests/       # Semantic interest profiles (AI-powered matching)
```

## 🚀 How to Use

### Importing Profiles

1. **Via Web UI:**

   - Navigate to the **Profiles** section
   - Click the **Import** button (⬆️ icon between Backtest and Export)
   - Select a JSON file from `demo/Alerts/` or `demo/Interests/`
   - Profile will be automatically saved (starts disabled for safety)

2. **Customization:**

   - After import, edit the profile to add:
     - Specific channel IDs or usernames
     - VIP sender IDs for prioritization
     - Custom keywords matching your needs
     - Digest schedules (hourly, daily, weekly)
   - Enable the profile when ready

3. **Testing:**
   - Use the **Backtest** feature to test against recent messages
   - Adjust thresholds and keywords based on results
   - Enable and monitor in production

## 📊 Alert Profiles (Keyword-Based)

Alert profiles use **heuristic detection** with keywords, patterns, and message characteristics. Fast and deterministic.

### Available Alert Profiles

| Profile                          | Use Case                   | Key Features                                                                   |
| -------------------------------- | -------------------------- | ------------------------------------------------------------------------------ |
| **personal-important-only.json** | Personal Signal-Over-Noise | Detects mentions, questions, urgent keywords. Hourly digest.                   |
| **security-monitoring.json**     | Security & Threat Intel    | CVE tracking, exploit detection, security advisories. Prioritizes admin posts. |
| **devops-ci-cd.json**            | DevOps & SRE               | Failed builds, deployment issues, infrastructure alerts. Real-time monitoring. |
| **crypto-trading-signals.json**  | Crypto & DeFi              | Exploits, listings, governance votes, regulatory news. Daily digest.           |
| **community-moderation.json**    | Community Management       | Spam, toxicity, rule violations. Instant alerts for moderation.                |
| **brand-monitoring.json**        | Marketing & Brand          | Brand mentions, feedback, sentiment tracking. Daily summary.                   |
| **product-feedback.json**        | Product & UX               | Bug reports, feature requests, user feedback. Evening digest.                  |
| **compliance-audit.json**        | Compliance & Legal         | Policy changes, regulatory updates, sensitive content. Weekly digest.          |

### Alert Profile Anatomy

```json
{
  "name": "Profile Name",
  "description": "What this profile detects",
  "enabled": false,                    // Start disabled for safety
  "min_score": 3.0,                    // Minimum score threshold (0-50)

  // Keyword categories (each adds score weight)
  "critical_keywords": ["urgent", "emergency"],
  "security_keywords": ["CVE", "exploit"],
  "urgency_keywords": ["asap", "now"],
  "financial_keywords": ["revenue", "profit"],
  "technical_keywords": ["bug", "error"],
  "project_keywords": ["release", "launch"],
  "community_keywords": ["feedback", "report"],
  "general_keywords": ["important"],

  // Detection toggles (each adds score)
  "detect_questions": true,            // +1.0 for questions
  "detect_mentions": true,             // +2.0 for @mentions
  "detect_links": false,               // +0.5 for URLs
  "detect_codes": false,               // +1.0 for code blocks
  "detect_documents": false,           // +1.0 for file attachments
  "detect_polls": false,               // +1.5 for polls
  "prioritize_pinned": true,           // +2.0 for pinned messages
  "prioritize_admin": true,            // +1.5 for admin posts

  // Advanced filtering
  "vip_senders": [],                   // Numeric Telegram IDs (always +1.0 each)
  "excluded_users": [],                // Numeric IDs to ignore
  "channels": [],                      // Limit to specific channels
  "users": [],                         // Limit to specific users

  "tags": ["category1", "category2"],  // Organizational metadata
  "digest_schedules": [...]            // Optional digest delivery
}
```

## 🧠 Interest Profiles (Semantic)

Interest profiles use **AI-powered semantic matching** to understand message meaning, not just keywords. Flexible and context-aware.

### Available Interest Profiles

| Profile                           | Use Case              | Threshold | Key Concepts                         |
| --------------------------------- | --------------------- | --------- | ------------------------------------ |
| **product-launches.json**         | Product Announcements | 0.45      | Launches, releases, unveilings       |
| **security-incidents.json**       | Security Intelligence | 0.50      | Vulnerabilities, breaches, exploits  |
| **infrastructure-incidents.json** | DevOps & SRE          | 0.48      | Outages, degradation, failures       |
| **defi-protocol-analysis.json**   | Crypto & DeFi         | 0.42      | Governance, exploits, tokenomics     |
| **ai-research.json**              | AI & ML Research      | 0.44      | Breakthroughs, papers, models        |
| **regulatory-updates.json**       | Compliance            | 0.46      | Regulations, enforcement, legal      |
| **user-frustration.json**         | UX Research           | 0.43      | Pain points, confusion, frustration  |
| **partnerships-ma.json**          | Business Strategy     | 0.47      | Acquisitions, partnerships, deals    |
| **technical-deep-dives.json**     | Engineering Learning  | 0.40      | Architecture, patterns, optimization |
| **market-intelligence.json**      | Competitive Intel     | 0.45      | Trends, analysis, market shifts      |

### Interest Profile Anatomy

```json
{
  "id": 3000,                          // Unique ID (3000-3999 range)
  "name": "Profile Name",
  "description": "What this profile matches semantically",
  "enabled": false,                    // Start disabled for safety
  "threshold": 0.45,                   // Similarity threshold (0.0-1.0)

  // Training samples for semantic matching
  "positive_samples": [
    "Example of message to match",
    "Another relevant message",
    "High-quality training example"
  ],
  "negative_samples": [
    "Example of message to ignore",
    "Noise or irrelevant content",
    "Low-quality non-match"
  ],

  // Optional keyword boost (supplements semantic)
  "keywords": ["keyword1", "keyword2"],

  // Advanced filtering
  "channels": [],                      // Limit to specific channels
  "users": [],                         // Limit to specific users
  "vip_senders": [],                   // Numeric Telegram IDs (threshold reduction)
  "excluded_users": [],                // Numeric IDs to ignore

  "tags": ["category1", "category2"],  // Organizational metadata
  "digest_schedules": [...]            // Optional digest delivery
}
```

### Threshold Guide

| Threshold     | Behavior        | Use When                     |
| ------------- | --------------- | ---------------------------- |
| **0.35-0.40** | Broad matching  | Exploratory, high recall     |
| **0.40-0.45** | Balanced        | Most use cases (recommended) |
| **0.45-0.50** | Strict matching | High precision needed        |
| **0.50+**     | Very strict     | Critical alerts only         |

**Pro tip:** Start with **0.42-0.45**, run backtest, then adjust based on false positives/negatives.

## 📅 Digest Schedules

Both profile types support digest delivery:

### Hourly Digest

```json
{
  "schedule_type": "hourly",
  "top_n": 10, // Top 10 messages per hour
  "min_score": 3.0 // Only include if score >= 3.0
}
```

### Daily Digest

```json
{
  "schedule_type": "daily",
  "daily_hour": 9, // 9 AM delivery
  "top_n": 20,
  "min_score": 3.0
}
```

### Weekly Digest

```json
{
  "schedule_type": "weekly",
  "weekly_day": 1, // Monday (0=Sunday, 6=Saturday)
  "weekly_hour": 9, // 9 AM delivery
  "top_n": 50,
  "min_score": 3.0
}
```

## 🎨 Customization Tips

### For Alert Profiles

1. **Keywords:**

   - Add industry-specific terms
   - Include product/brand names
   - Use partial matches (e.g., "CVE-" matches "CVE-2024-1234")

2. **Thresholds:**

   - Low traffic channel: `min_score: 2.0-3.0`
   - High traffic channel: `min_score: 5.0-7.0`
   - Critical only: `min_score: 8.0+`

3. **VIP Senders:**
   - Add key team members' Telegram IDs
   - Official accounts always get +1.0 boost
   - Find IDs via `/api/telegram/users` endpoint

### For Interest Profiles

1. **Training Samples:**

   - Add 5-10 **high-quality** positive samples
   - Include 3-5 **clear** negative samples
   - Use actual message text from your channels

2. **Threshold Tuning:**

   - Too many alerts? Increase threshold by 0.05
   - Missing important messages? Decrease by 0.05
   - Use backtest to validate changes

3. **Keyword Boost:**
   - Add keywords for explicit matching
   - Supplements semantic scoring
   - Useful for acronyms/abbreviations

## 🔧 Troubleshooting

### Too Many Alerts

**Alert Profiles:**

- Increase `min_score` threshold
- Remove generic keywords
- Add excluded users/channels
- Use stricter detection toggles

**Interest Profiles:**

- Increase `threshold` (e.g., 0.45 → 0.50)
- Add more negative samples
- Remove broad keywords

### Missing Important Messages

**Alert Profiles:**

- Decrease `min_score` threshold
- Add missing keywords to relevant categories
- Enable more detection toggles
- Check VIP senders are configured

**Interest Profiles:**

- Decrease `threshold` (e.g., 0.50 → 0.45)
- Add more diverse positive samples
- Add explicit keywords for must-catch terms
- Review backtest results

### False Positives

**Alert Profiles:**

- Add to `excluded_users` list
- Remove overly generic keywords
- Increase min_score requirement

**Interest Profiles:**

- Add false positives as negative samples
- Increase threshold slightly
- Review and improve positive samples

## 📚 Use Case Mapping

Refer to `docs/USE_CASES.md` for detailed scenarios. Quick reference:

| Use Case Category           | Recommended Profiles                           |
| --------------------------- | ---------------------------------------------- |
| **Personal Productivity**   | personal-important-only, product-launches      |
| **Security & Threat Intel** | security-monitoring, security-incidents        |
| **DevOps & SRE**            | devops-ci-cd, infrastructure-incidents         |
| **Crypto & Trading**        | crypto-trading-signals, defi-protocol-analysis |
| **Community Management**    | community-moderation                           |
| **Marketing & Growth**      | brand-monitoring, market-intelligence          |
| **Product & UX**            | product-feedback, user-frustration             |
| **Compliance & Legal**      | compliance-audit, regulatory-updates           |
| **Engineering & Learning**  | ai-research, technical-deep-dives              |
| **Business Strategy**       | partnerships-ma, market-intelligence           |

## 🎯 Best Practices

1. **Start Disabled:** All demo profiles start disabled. Test with backtest before enabling.

2. **Iterate:** Import → Customize → Backtest → Adjust → Enable → Monitor

3. **Combine Approaches:** Use Alert profiles for speed, Interest profiles for nuance

4. **Tag Everything:** Use tags for organization and filtering

5. **Monitor Performance:** Check digest quality, adjust thresholds weekly

6. **Privacy First:** Demo profiles contain no real data. Customize with your own channels/users.

## 🤝 Contributing

Have a great profile configuration for a specific use case? Consider contributing:

1. Anonymize all real data (IDs, names, specific keywords)
2. Add clear description and tags
3. Test with backtest functionality
4. Submit via PR to `demo/` folder

## 📖 Further Reading

- **Main Documentation:** `/docs/USER_GUIDE.md`
- **Configuration Reference:** `/docs/CONFIGURATION.md`
- **Use Cases:** `/docs/USE_CASES.md`
- **Profile Reference:** `/docs/PROFILES_QUICK_REFERENCE.md`

---

**Note:** All profiles start **disabled** for safety. Always run a backtest before enabling in production!
