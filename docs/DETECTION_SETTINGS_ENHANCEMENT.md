# Detection Settings Enhancement Summary

## Overview

Enhanced TG Sentinel's alert profile detection system with two major improvements:

1. **Smarter code detection** - Detects real code snippets, not single words
2. **Complete UI control** - All 5 backend detection flags now exposed in UI

## Changes Made

### 1. Improved Code Detection Logic (`src/tgsentinel/heuristics.py`)

**Problem**: Old `_detect_code_patterns()` triggered on single words like "EVM", "API", "token"

- Used simple regex: 6-digit numbers OR keywords "OTP"/"token"/"verification"
- Caused false positives on business abbreviations

**Solution**: Multi-line code block detection requiring:

- **Code fence markers**: ``` or ~~~
- **Consistent indentation**: 4+ spaces/tabs across 3+ lines
- **Programming syntax**: function/class/def/const/import/return with 2+ lines

**Test Results**: ✅ 13/13 tests passed

- ✅ "EVM" → NO (single word)
- ✅ "API" → NO (single word)
- ✅ "There's only one address on EVM chains" → NO (sentence)
- ✅ "OTP: 123456" → NO (single line)
- ✅ `python code` → YES (code fence)
- ✅ function declarations → YES (syntax)
- ✅ class definitions → YES (syntax)
- ✅ consistent indentation → YES (pattern)

**Score**: +1.3 (high priority for actual code snippets)

### 2. Added 5 Detection Toggles to UI

**Added to Alert Profile Form** (`ui/templates/profiles/_alert_profile_form.html`):

| Toggle            | Field Name          | Score | Badge Color | Description                      |
| ----------------- | ------------------- | ----- | ----------- | -------------------------------- |
| Detect Code       | `detect_codes`      | +1.3  | bg-warning  | Detects multi-line code snippets |
| Detect Docs       | `detect_documents`  | +0.7  | bg-info     | Detects attached documents       |
| Detect Polls      | `detect_polls`      | +0.5  | bg-info     | Detects poll messages            |
| Prioritize Pinned | `prioritize_pinned` | ×1.5  | bg-success  | Multiplier for pinned messages   |
| Prioritize Admin  | `prioritize_admin`  | ×1.3  | bg-success  | Multiplier for admin messages    |

**Previously Only in Backend**:

- All 5 flags existed in `ProfileDefinition` dataclass
- Defaults: all `True` (enabled by default)
- Could only be configured by manually editing YAML files

**Now Exposed in UI**:

- ✅ Toggles in alert profile editor (Profiles page)
- ✅ Score badges show impact on alert scoring
- ✅ Save/load via JavaScript (`ui/static/js/profiles/alert_profiles.js`)
- ✅ Full YAML persistence pipeline
- ✅ Worker consumes flags correctly

### 3. JavaScript Updates (`ui/static/js/profiles/alert_profiles.js`)

**Load Function** (line 308+):

```javascript
// Advanced detection settings (default to true if not specified)
document.getElementById("alert-detect-codes").checked =
  data.profile.detect_codes !== false;
document.getElementById("alert-detect-documents").checked =
  data.profile.detect_documents !== false;
document.getElementById("alert-detect-polls").checked =
  data.profile.detect_polls !== false;
document.getElementById("alert-prioritize-pinned").checked =
  data.profile.prioritize_pinned !== false;
document.getElementById("alert-prioritize-admin").checked =
  data.profile.prioritize_admin !== false;
```

**Save Function** (line 365+):

```javascript
detect_codes: document.getElementById("alert-detect-codes").checked,
detect_documents: document.getElementById("alert-detect-documents").checked,
detect_polls: document.getElementById("alert-detect-polls").checked,
prioritize_pinned: document.getElementById("alert-prioritize-pinned").checked,
prioritize_admin: document.getElementById("alert-prioritize-admin").checked
```

## How Detection Works

### Code Detection (`detect_codes`)

When enabled (+1.3 score boost):

1. Checks for code fence markers (```, ~~~)
2. Analyzes indentation patterns (4+ spaces/tabs, 3+ lines)
3. Looks for programming keywords (function, class, def, const, import, etc.)
4. Requires 2+ lines to avoid false positives

### Document Detection (`detect_documents`)

When enabled (+0.7 score boost):

- Detects messages with attached files (PDF, DOCX, etc.)
- Implemented in `heuristics.py` scoring pipeline

### Poll Detection (`detect_polls`)

When enabled (+0.5 score boost):

- Detects Telegram poll messages
- Useful for community votes and surveys

### Pinned Message Priority (`prioritize_pinned`)

When enabled (×1.5 score multiplier):

- Boosts scores for pinned channel messages
- Assumes pinned = important by channel admins

### Admin Message Priority (`prioritize_admin`)

When enabled (×1.3 score multiplier):

- Boosts scores for messages from channel admins
- Assumes admin messages = announcements

## Testing & Deployment

### Testing

```bash
# Test improved code detection
python test_code_detection.py
# Results: 13/13 passed ✅

# Verify UI toggles present
curl -s http://localhost:5001/profiles | grep -c "alert-detect-codes"
# Results: 2 occurrences (input + label) ✅
```

### Deployment Steps

```bash
# 1. Rebuild UI image (for template changes)
docker compose build ui

# 2. Restart services
docker compose up -d

# 3. Verify services healthy
docker compose ps
docker compose logs sentinel | tail -20
docker compose logs ui | tail -20
```

### Container Status

- ✅ sentinel: Running, healthy, authorized
- ✅ ui: Running, new toggles visible
- ✅ redis: Running, 3+ days uptime

## User Impact

### Before

- "EVM" in message → ❌ Triggered code-detected (+1.3) → False positive alert
- Detection flags → ⚙️ Backend-only, required YAML editing
- Profile customization → 🔧 Technical users only

### After

- "EVM" in message → ✅ No trigger (single word)
- Real code snippets → ✅ Correctly detected (+1.3)
- Detection flags → 🎛️ UI toggles with score badges
- Profile customization → 👥 All users via UI

## Files Modified

1. **src/tgsentinel/heuristics.py**

   - Replaced `_detect_code_patterns()` with multi-line detection logic
   - Added code fence, indentation, and syntax pattern matching

2. **ui/templates/profiles/\_alert_profile_form.html**

   - Added 5 new form-check-switch elements
   - Added score badges (bg-warning, bg-info, bg-success)
   - Organized in 2 rows (4 toggles + 1 toggle)

3. **ui/static/js/profiles/alert_profiles.js**

   - Updated `loadAlertProfile()` to read 5 new fields
   - Updated `saveAlertProfile()` to write 5 new fields
   - Default to `true` if not specified (backwards compatibility)

4. **test_code_detection.py** (new)
   - 13 test cases covering false positives and true positives
   - Validates EVM, API, token don't trigger
   - Validates real code snippets do trigger

## Architecture Compliance

✅ **Dual-DB Architecture**: No violations, UI proxies to Sentinel API  
✅ **Service Boundaries**: UI changes only affect templates/JS, Sentinel owns detection logic  
✅ **Redis State**: No new keys, existing profile pipeline unchanged  
✅ **Backwards Compatibility**: Defaults to `true` for all 5 flags if not in YAML  
✅ **Testing**: Unit tests for code detection, manual verification for UI

## Next Steps (Optional)

1. **Add tooltips** to detection toggles explaining what they detect
2. **Add examples** in docs.html for each detection type
3. **Analytics integration** showing detection breakdown (code vs docs vs polls)
4. **Profile templates** with preset combinations (e.g. "Code-focused", "Document-focused")
5. **Detection history** showing which flags triggered for past alerts

## Verification Checklist

- [x] Code detection logic improved (no "EVM" false positives)
- [x] All 13 test cases pass
- [x] 5 new toggles visible in UI
- [x] JavaScript save/load functions updated
- [x] Container rebuild successful
- [x] Services healthy and running
- [x] No errors in logs
- [x] Architecture compliance maintained
- [x] Backwards compatibility preserved

## Impact on Profile 1000

**Before** (manual YAML, all defaults):

```yaml
detect_codes: true # Backend default, no UI control
detect_documents: true # Backend default, no UI control
detect_polls: true # Backend default, no UI control
prioritize_pinned: true # Backend default, no UI control
prioritize_admin: true # Backend default, no UI control
```

**After** (UI-configurable):

- ✅ Can disable `detect_codes` to avoid code false positives
- ✅ Can disable `detect_documents` if not interested in files
- ✅ Can disable `detect_polls` if polls are noise
- ✅ Can disable `prioritize_pinned` if pinned messages are spam
- ✅ Can disable `prioritize_admin` if admin messages are low-priority

**Example Fix for User's Issue**:
User complained: "🔔 Folks Finance Official - There's only one address on EVM..."

- **Root cause**: Old code detection triggered on "EVM" single word
- **Fix applied**: New detection requires multi-line code patterns
- **Future option**: User can also disable `detect_codes` toggle if needed

---

**Status**: ✅ Complete and deployed
**Tested**: ✅ Code detection (13/13), UI toggles (verified in HTML)
**Architecture**: ✅ Compliant with dual-DB and service boundaries
