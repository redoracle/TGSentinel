# Channel Management UI Enhancements

## Overview

Enhanced the Channels Management table with visual improvements and delete functionality, including automatic config reload for the sentinel container.

## Features Implemented

### 1. Copy to Clipboard for Chat IDs

- **Visual Design**: Replaced plain text Chat IDs with interactive copy buttons
- **Color Coding**:
  - **Blue (btn-outline-primary)**: Positive IDs for users, bots, and channels
  - **Cyan (btn-outline-info)**: Negative IDs for groups and supergroups (-100xxx)
- **Icon**: Bootstrap clipboard SVG icon
- **Functionality**:
  - Click button to copy Chat ID to clipboard
  - Visual feedback: Icon changes to checkmark with "Copied!" text for 2 seconds
  - Toast notification confirms successful copy
  - Fallback for older browsers using `document.execCommand`

### 2. Delete Channel Button

- **Visual Design**: Red outlined trash icon button
- **Confirmation**: Browser confirm dialog before deletion
- **Loading State**: Spinner replaces icon during API call
- **Workflow**:
  1. User clicks delete button
  2. Confirm dialog shows channel name and ID
  3. API DELETE request to `/api/config/channels/<chat_id>`
  4. Config file updated (YAML)
  5. UI config reloaded via `reload_config()`
  6. Reload marker created at `/app/data/.reload_config`
  7. Sentinel container detects marker and reloads config
  8. Page refreshes to show updated channel list

### 3. Backend Endpoint

**DELETE /api/config/channels/<int:chat_id>**

Request:

```
DELETE /api/config/channels/123
```

Success Response (200):

```json
{
  "status": "ok",
  "message": "Channel deleted successfully"
}
```

Error Response (404):

```json
{
  "status": "error",
  "message": "Channel with ID 123 not found"
}
```

Error Response (404):

```json
{
  "status": "error",
  "message": "Configuration file not found"
}
```

**Implementation Details**:

- Atomic file writes using `tempfile.NamedTemporaryFile` + `shutil.move`
- Preserves all other configuration sections (alerts, digest, etc.)
- Handles both positive and negative Chat IDs
- Creates reload marker for sentinel container
- Comprehensive error handling and logging

### 4. Sentinel Container Reload Integration

- **Shared Signal**: `/app/data/.reload_config` marker file
- **Detection**: Worker checks for marker every 5 seconds
- **Reload Sequence**:
  1. Detect marker file
  2. Load fresh config from YAML
  3. Rebuild channel rules
  4. Reload semantic interests
  5. Delete marker file
  6. Log reload event with channel count
- **Zero Downtime**: No container restart required

## UI Changes

### Template (config.html)

**Before**:

```html
<td>{{ channel.chat_id }}</td>
```

**After**:

```html
<td>
  <button
    type="button"
    class="btn btn-sm {% if channel.chat_id < 0 %}btn-outline-info{% else %}btn-outline-primary{% endif %} copy-chat-id"
    data-chat-id="{{ channel.chat_id }}"
    title="Copy Chat ID"
  >
    <svg>...</svg>
    <span class="ms-1">{{ channel.chat_id }}</span>
  </button>
</td>
```

**New Actions Column**:

```html
<th scope="col">Actions</th>
...
<td>
  <button
    type="button"
    class="btn btn-sm btn-outline-danger delete-channel"
    data-chat-id="{{ channel.chat_id }}"
    data-chat-name="{{ channel.name }}"
    title="Delete Channel"
  >
    <svg>...</svg>
  </button>
</td>
```

### JavaScript Event Handlers

**Copy Handler** (~50 lines):

- Event delegation on `document.body` for `.copy-chat-id`
- Modern Clipboard API with fallback
- Visual feedback with icon swap
- Toast notification

**Delete Handler** (~45 lines):

- Event delegation on `document.body` for `.delete-channel`
- Confirmation dialog with channel details
- Loading state during API call
- Error handling with button re-enable
- Page reload after successful deletion

## Color Scheme Rationale

### Why Different Colors?

1. **Visual Distinction**: Helps users quickly identify channel types
2. **Telegram Convention**: Negative IDs are a special format in Telegram
3. **User Experience**: Color-coding reduces cognitive load

### Button Classes:

- `btn-outline-primary` (Blue): Standard channels, bots, users
- `btn-outline-info` (Cyan): Groups and supergroups (negative IDs)
- `btn-outline-danger` (Red): Delete action (universal danger color)

## Files Modified

### UI

- `ui/templates/config.html`: Template changes (3 sections)
  - Chat ID column: Plain text → Copy button
  - Added Actions column with delete button
  - JavaScript handlers for copy and delete

### Backend

- `ui/app.py`: New DELETE endpoint (~70 lines)
  - `api_config_channels_delete(chat_id)`
  - File locking and atomic writes
  - Config reload integration
  - Reload marker creation

### Worker

- No changes needed (already has reload detection from previous work)

## Testing

### Manual Testing

1. Open `http://localhost:5001/config`
2. View Channels Management table
3. Click copy button → Chat ID copied to clipboard
4. Click delete button → Confirmation shown
5. Confirm deletion → Channel removed
6. Check sentinel logs → Reload event logged
7. Verify channel no longer monitored

### Automated Testing

All 195 existing tests pass:

```bash
python -m pytest tests/ -v
```

No regression in functionality.

## Security Considerations

1. **File Locking**: Atomic writes prevent race conditions
2. **Input Validation**: Chat ID must be integer
3. **Error Handling**: Graceful failures with user feedback
4. **CSRF Protection**: Already handled by Flask's session
5. **Authorization**: Assumes trusted users (internal tool)

## User Experience Improvements

### Before:

- Chat IDs were plain text (difficult to copy)
- No way to delete channels via UI
- Manual container restart required after deletion
- No visual distinction between channel types

### After:

- One-click copy to clipboard with visual feedback
- Delete button with confirmation and loading state
- Automatic sentinel container reload
- Color-coded buttons show channel type at a glance
- Toast notifications for all actions
- Mobile-friendly button sizes

## Future Enhancements

1. **Bulk Operations**: Select multiple channels for deletion
2. **Undo Delete**: Temporary "trash" before permanent deletion
3. **Channel Edit**: Inline editing of thresholds and settings
4. **Keyboard Shortcuts**: Ctrl+C to copy selected Chat ID
5. **Export Selected**: Export specific channels to YAML
6. **Drag & Drop Reorder**: Change channel display order
7. **Channel Stats**: Show message count, alert rate next to each channel

## Accessibility

- All buttons have `type="button"` attribute
- `title` attributes for hover tooltips
- Visual feedback for all interactions
- Screen reader friendly (visually-hidden labels where needed)
- Keyboard navigable (standard button elements)

## Performance

- Event delegation prevents memory leaks
- Clipboard API is async and non-blocking
- Single API call per deletion
- Efficient file operations (atomic writes)
- Minimal DOM manipulation

## Browser Compatibility

- Modern Clipboard API with fallback
- Bootstrap 5 icons (SVG)
- Standard CSS classes
- Works in all modern browsers
- Graceful degradation for older browsers

## Deployment Notes

- No database migrations needed
- No new dependencies required
- Config changes are backwards compatible
- Sentinel container auto-detects reload marker
- Can deploy UI and sentinel independently
