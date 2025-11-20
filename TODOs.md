# COMPLETED ITEMS

## ✅ Channel Configuration Concurrency Fix (2025-01-16)

**Issue**: TOCTOU race condition in channel configuration updates where concurrent POST requests could overwrite each other's changes, causing data loss.

**Solution**: Implemented Redis distributed lock with retry logic in `ui/routes/channels.py`

- Lock key pattern: `tgsentinel:config_lock:channel:{chat_id}`
- 3 retry attempts with 0.5s exponential backoff
- 5-second lock timeout to prevent deadlock
- Safe lock release using lock identifiers

**Files Modified**:

- `ui/routes/channels.py` (lines 345-495) - Added distributed locking
- `test_channel_concurrent_update.sh` - Test script for validation
- `CHANNEL_CONCURRENCY_FIX.md` - Comprehensive documentation

**Testing**: Run `./test_channel_concurrent_update.sh` to verify concurrent update safety

---

# OPEN ITEMS

- I would like to see the sentinel boot container docker console info about what [INFO] sentence_transformers.SentenceTransformer : Download and Load pretrained SentenceTransformer, which happens at every boot and is not user authentication related,

- When restarting containers (when I was previuosly authenticated), the app opens directly (which is good), but a loading spinner should appear in a toast message, so the user knows that something is happening in the background and the app is still not fully loaded. Use the same logic to terminate the loading spinner when the app is fully loaded as we have in the login progressbar window.

- Fully describe the full login window progressbar workflow, most times when I start a fresh enviroment the login progressbar window appears but it gets stuck at 95% forever, I have to refresh the browser to make it go away. Describe all the steps that should happen from the moment the user clicks "login" until the moment the app is fully loaded and ready to use.

- in order to avaiod resource consuption, I would like to set a max number of messages stored in the related db (not sure where are they saved now if in the sentinel.db or in the redis db), so when the max number of messages is reached, the oldest messages are deleted to make room for the new ones and db vacuum runs automatically to optimise the db. Default max messages stored should be 200 or Retention (30 days), this should be configurable from the settings page.

- in /config when clicking the save configuration button I get: Could not save configuration
  Besides the buttons: Reset changes, Clean Db, Save configuration, should be placed inside the "System Settings" section. Make sure the related configuration file is read and save using the UI proxy to write the cfg on the Sentinel container using the Sentinel endpoint.

# Update all API endpoints in /docs

- scan the whole codebase and find all endpoints on ui, sentinels and other components like the yml files on the sentinel container then...
- Update all API endpoints in /docs to reflect any changes made in the application, ensuring that users have access to the most current information.
- Verify that all endpoints are correctly documented and functioning as intended.
- Ensure that any new features or changes in the application are accurately represented in the API documentation.
- Review and revise the documentation for clarity and completeness.
- do not touch anything about style and UI, just updated the contents

# DOCS UPDATE

make sure on top of docs.html (/docs) we get 4 tabs:

- Sentinel API Documentation
- UI API Documentation
- Sentinel YAML Configuration Documentation
- User Manual

Each tab should link to the respective documentation sections, providing users with easy access to the information they need.
The tabs should be clearly labeled and styled consistently with the overall design of the documentation page.

- The "Sentinel API Documentation" tab should contain all the existing API documentation related to the Sentinel application.
- The "UI API Documentation" tab should include all API documentation specific to the user interface components
- The "Sentinel YAML Configuration Documentation" tab should provide detailed information about the YAML configuration options available for Sentinel.
- Ensure that the tab navigation is intuitive and user-friendly, allowing users to easily switch between different documentation sections.

The "Navigation" style and functionability should remain as is, just make sure that switching tab will also update/replace the "Navigation" contents accordingly to each tab contents.
