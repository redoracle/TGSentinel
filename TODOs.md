These (sentence_transformers) shold be happening before any user authentication directly at boot not after a user authenticates, and it happens only once not everytime we restart the containers, not removed or re downloaded relloaded between login/logout or container restarts.
2025-11-18 12:47:39 [INFO] sentence_transformers.SentenceTransformer: Use pytorch device_name: cpu
2025-11-18 12:47:39 [INFO] sentence_transformers.SentenceTransformer: Load pretrained SentenceTransformer: all-MiniLM-L6-v2

- Consolidate sections header title and related collapsed function, not interfiring with the related section buttons, make them indipendent like it is already in the elements of the dashboard "/" template.
  For istance for:
  System Health
  Real-time infrastructure pulse

We need to apply this behaviour to all the sections in:
/alerts
/config
/profiles
/analytics
/developer
/console

making sure:

- they are all collapsable
- the section header title has a subtitle (describing the section content) if missing
- the section header title and the collapse button are indipendent (not interfering with each other)
- the section header title and the collapse button are aligned on the same line
- the section header title and the collapse button have the same style across all the sections mentioned above, as is in /dashboard if not already
- if a button is not present at the right end of the section title, than add and info button that redirect the user to the related /docs API section for that specific section

# Rename in /config

- "Channels Management" in "Monitoring Management"
- "Manage channels, rules, and exports" to "Manage monitored channels and users"

# Update all API endpoints in /docs

- Update all API endpoints in /docs to reflect any changes made in the application, ensuring that users have access to the most current information.
- Verify that all endpoints are correctly documented and functioning as intended.
- Ensure that any new features or changes in the application are accurately represented in the API documentation.
- Review and revise the documentation for clarity and completeness.
- do not touch anything about style and UI, just updated the contents

# DOCS UPDATE

make sure on top of docs.html (/docs) we get 3 tabs, using the same style we got tabs in profile.html showing:

- Sentinel API Documentation
- UI API Documentation
- Sentinel YAML Configuration Documentation

Each tab should link to the respective documentation sections, providing users with easy access to the information they need.
The tabs should be clearly labeled and styled consistently with the overall design of the documentation page.

- The "Sentinel API Documentation" tab should contain all the existing API documentation related to the Sentinel application.
- The "UI API Documentation" tab should include all API documentation specific to the user interface components
- The "Sentinel YAML Configuration Documentation" tab should provide detailed information about the YAML configuration options available for Sentinel.
- Ensure that the tab navigation is intuitive and user-friendly, allowing users to easily switch between different documentation sections.

The "Navigation" style and functionability should remain as is, just make sure that switching tab will also update/replace the "Navigation" contents accordingly to each tab contents.
